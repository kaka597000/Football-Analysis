"""Collect as-of supplemental observations for the registered Brazilian Serie A batch."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, urlopen
from zoneinfo import ZoneInfo

RUNTIME = Path(__file__).resolve().parents[1]
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))
from automation import market_snapshot
from data_sources.clients import NoRedirect, fetch
from publication import atomic_json
from research.analysis_freeze import aware, verify
from research_program.three_arm_forward import market_pair
from storage_lock import exclusive_lock

ENROLLMENT = Path('workspace-updates/20261118-three-arm')
OPEN_BASE = 'https://raw.githubusercontent.com/openfootball/south-america/f51b2dcf5e0fab9f0abd5206ab8c24a413e67277/'
ESPN = 'https://site.api.espn.com/apis/site/v2/sports/soccer/bra.1/summary?event='
OPEN_TEAMS = {
    '3456': 'Coritiba FBC', '2674': 'Santos FC', '4936': 'Clube do Remo', '9318': 'Chapecoense AF',
    '819': 'CR Flamengo', '3458': 'CA Paranaense', '2022': 'Cruzeiro EC', '2029': 'SE Palmeiras',
    '6273': 'Grêmio FBPA', '9967': 'EC Bahia', '874': 'SC Corinthians Paulista', '6086': 'Botafogo FR',
}
# Provider display names at the registered batch, including the latest opponents.
BSD_TEAMS = {'3456':'Coritiba','2674':'Santos','4936':'Remo','9318':'Chapecoense','819':'Flamengo',
             '3458':'Athletico','2022':'Cruzeiro','2029':'Palmeiras','6273':'Grêmio',
             '9967':'Bahia','874':'Corinthians','6086':'Botafogo','7632':'Atlético Mineiro',
             '3454':'Vasco da Gama','2026':'São Paulo','6079':'Red Bull Bragantino','1936':'Internacional'}
SIDES = ('home', 'away')


def now():
    return datetime.now(timezone.utc)


def read(path):
    return json.loads(path.read_bytes())


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def public_fetch(url):
    req = Request(url, headers={'User-Agent': 'WCPP-football-research/1.0'})
    with urlopen(req, timeout=5) as response:
        raw = response.read(5_000_001)
        if len(raw) > 5_000_000 or response.geturl() != url:
            raise ValueError('unexpected source response')
        return raw, sha(raw)


class Source:
    """One serial queue per provider. Independent providers run in parallel."""
    def __init__(self, out, name):
        self.cache = out / 'cache' / name
        self.cache.mkdir(parents=True, exist_ok=True)
        self.calls = []
        self.block = None
        self.last_request = 0.
        self.deadline = time.monotonic()+32

    def get(self, url, load, parse, *, ttl=None, not_before=None):
        path = self.cache / (sha(url.encode()) + '.json')
        old = read(path) if path.is_file() else None
        if (old and (not_before is None or aware(old['observed_at']) >= not_before)
                and (ttl is None or timedelta(0) <= now() - aware(old['observed_at']) <= ttl)):
            self.calls.append({'url':url,'status':'cached','observed_at':old['observed_at'],'response_sha256':old['source_sha256']})
            return old
        base = {'url': url, 'status': 'missing'}
        if self.block:
            return dict(base, reason=self.block, attempted=False)
        for attempt in range(2):
            if time.monotonic() >= self.deadline:
                return dict(base, reason='provider round time budget exhausted',attempted=False)
            time.sleep(max(0., .4 - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            started = now().isoformat()
            try:
                data, checksum = load()
                observed = now().isoformat()
                facts = parse(data)
                result = {'url':url, 'status':'ok', 'source_sha256': checksum, 'observed_at':observed,
                          'first_observed_at': (old['first_observed_at'] if old and old['facts'] == facts else observed),
                          'facts':facts}
                if old and old['source_sha256'] != checksum:
                    history = self.cache/'replaced'
                    history.mkdir(exist_ok=True)
                    saved = history/(sha(url.encode())+'-'+old['source_sha256']+'.json')
                    if not saved.exists():
                        atomic_json(saved,old)
                atomic_json(path, result)
                self.calls.append({'url':url,'requested_at':started,'observed_at':observed,'status':'ok','response_sha256':checksum})
                return result
            except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError, TypeError, IndexError) as exc:
                code = getattr(exc, 'code', None)
                status = str(code) if code else (str(exc) if isinstance(exc, ValueError) else type(exc).__name__)
                self.calls.append({'url':url,'requested_at':started,'observed_at':now().isoformat(),'status':status})
                if code in (401,403,429):
                    self.block = 'provider HTTP ' + str(code)
                if attempt or self.block or not (code in (500,502,503,504) or isinstance(exc,(TimeoutError,URLError)) and not isinstance(exc,HTTPError)):
                    return dict(base, reason=status, attempted=True, observed_at=now().isoformat())
        raise AssertionError('unreachable')


def home_away(teams, fixture):
    if len(teams) != 2:
        raise ValueError('expected exactly two teams')
    matched = {r['homeAway']: r for r in teams}
    if any('espn:' + str(matched[s]['team']['id']) != fixture[s] for s in SIDES):
        raise ValueError('unexpected home or away team')
    return matched


def espn_summary(raw, fixture, historical):
    doc = json.loads(raw)
    header = doc['header']
    contest, = header['competitions']
    if (str(header['id']) != fixture['match_id'].split(':')[1] or header.get('league',{}).get('slug') != 'bra.1'
            or aware(contest['date']) != aware(fixture['kickoff_at'])):
        raise ValueError('ESPN event, league or kickoff mismatch')
    sides = home_away(contest['competitors'], fixture)
    status = contest['status']['type']
    if historical:
        if status['name'] != 'STATUS_FULL_TIME' or status.get('completed') is not True:
            raise ValueError('expected regulation full time')
        rows = {str(r['team']['id']): r for r in doc.get('boxscore',{}).get('teams',[])}
        data = {}
        for s in SIDES:
            team_id = fixture[s][5:]
            if str(sides[s].get('score')) != str(fixture[s+'_goals']):
                raise ValueError('historical score mismatch')
            entries = {e['name']:e.get('displayValue') for e in rows.get(team_id,{}).get('statistics',[])}
            data[s] = {}
            for name in ('totalShots','shotsOnTarget','totalPasses','accuratePasses','expectedGoals'):
                value = entries.get(name)
                if value is not None:
                    number = float(value)
                    if math.isfinite(number) and number >= 0:
                        data[s][name] = number
            if data[s].get('shotsOnTarget',0) > data[s].get('totalShots',math.inf):
                raise ValueError('on-target shots exceed all shots')
        return {'statistics':data}
    if status['state'] != 'pre' or status.get('completed') is not False:
        raise ValueError('not prematch')
    starters = {}
    if doc.get('rosters'):
        rosters = home_away(doc['rosters'], fixture)
        for s in SIDES:
            ids = [str(p['athlete']['id']) for p in rosters[s].get('roster',[]) if p.get('starter') is True and p.get('athlete',{}).get('id')]
            if len(ids) == len(set(ids)) == 11:
                starters[s] = ids
    return {'starters':starters, 'both_elevens':len(starters) == 2 and not set(starters['home']).intersection(starters['away'])}


def original_inputs(data):
    root = data / ENROLLMENT
    plan = verify(read(root/'batch/plan.json'))
    fixtures = plan['payload']['fixtures']
    history, membership, first = {}, {}, {}
    for f in fixtures:
        mid = f['match_id']
        key = mid[5:]
        path, meta = root/'identity'/(key+'.json'), read(root/'identity'/(key+'.meta.json'))
        raw = path.read_bytes()
        if (sha(raw) != meta['sha256'] or not any(s['response_sha256'] == meta['sha256'] for s in f['sources'])
                or aware(meta['captured_at']) > aware(f['cutoff_at'])):
            raise ValueError('original ESPN evidence mismatch')
        first[mid] = {'facts':espn_summary(raw, f, False),'url':meta['url'],'observed_at':meta['captured_at'],
                      'source_sha256':meta['sha256'],'status':'ok'}
        groups = {str(t['team']['id']):t['events'] for t in json.loads(raw).get('lastFiveGames',[])}
        membership[mid] = {}
        for s in SIDES:
            team_id = f[s][5:]
            valid = [e for e in groups.get(team_id,[]) if e.get('leagueAbbreviation') == 'Brazil Serie A'
                     and team_id in (str(e['homeTeamId']),str(e['awayTeamId'])) and aware(e['gameDate']) < aware(f['cutoff_at'])
                     and e.get('gameResult') in ('W','D','L') and e['id'] != key]
            if not valid:
                continue
            e = max(valid,key=lambda r: aware(r['gameDate']))
            hid = 'espn:'+str(e['id'])
            row = {'match_id':hid,'home':'espn:'+e['homeTeamId'],'away':'espn:'+e['awayTeamId'],
                   'kickoff_at':e['gameDate'],'home_goals':int(e['homeTeamScore']),'away_goals':int(e['awayTeamScore'])}
            if hid in history and history[hid] != row:
                raise ValueError('prior event conflict')
            history[hid] = row
            membership[mid][s] = hid
    return plan, fixtures, history, membership, first


def espn_job(src, fixtures, history, stamp):
    observations, stats = {}, {}
    for f, previous in [(f,False) for f in fixtures if stamp < aware(f['kickoff_at'])] + [(v,True) for v in history.values()]:
        prior = aware(f['kickoff_at']) - stamp
        ttl = None if previous else timedelta(minutes=5 if prior <= timedelta(minutes=90) else 60)
        url = ESPN + f['match_id'][5:]
        window_start = aware(f['kickoff_at'])-timedelta(minutes=90) if not previous and prior<=timedelta(minutes=90) else None
        result = src.get(url, lambda: public_fetch(url), lambda raw: espn_summary(raw,f,previous),ttl=ttl,not_before=window_start)
        if not previous and result['status'] == 'ok' and aware(result['observed_at']) >= aware(f['kickoff_at']):
            result = {'status':'missing','reason':'observed_after_kickoff','url':url}
        (stats if previous else observations)[f['match_id']] = result
    return observations, stats


def open_rows(raw):
    year, current, rows = 2026, None, []
    for line in raw.decode().splitlines():
        head = re.fullmatch(r'\s*(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) ([A-Z][a-z]{2}) (\d{1,2})(?: (\d{4}))?\s*',line)
        if head:
            month, day, declared = head.groups()
            year = int(declared or year)
            current = date(year, datetime.strptime(month,'%b').month, int(day)).isoformat()
        if ' v ' in line and current:
            home, rest = re.sub(r'^\s*(?:\d\d:\d\d\s+)?','',line).split(' v ',1)
            away = re.split(r'\s+\d+[-:]\d+',rest.strip())[0]
            rows.append([current,home.strip(),away.strip()])
    return rows


def public_job(src, fixtures):
    def fetch_file(name):
        url = OPEN_BASE + name
        def parse(raw):
            if name == 'LICENSE.md' and not raw.startswith(b'CC0 1.0 Universal'):
                raise ValueError('unexpected dataset license')
            return raw.decode()
        return src.get(url,lambda:public_fetch(url),parse)
    license_ref = fetch_file('LICENSE.md')
    if license_ref['status'] != 'ok':
        return {}
    ref = fetch_file('brazil/2026_br1.txt')
    if ref['status'] != 'ok':
        return {}
    rows = open_rows(ref['facts'].encode())
    out = {}
    for f in fixtures:
        league_day = aware(f['kickoff_at']).astimezone(ZoneInfo('America/Sao_Paulo')).date().isoformat()
        k = [league_day, OPEN_TEAMS[f['home'][5:]], OPEN_TEAMS[f['away'][5:]]]
        out[f['match_id']] = {k1:v for k1,v in ref.items() if k1!='facts'} | {
            'date_and_sides_verified':rows.count(k)==1,'registered_identity':k,'license':'CC0-1.0',
            'license_sha256':license_ref['source_sha256'],'file_fixture_count':len(rows)}
    return out


def bsd_load(path, params=None):
    transport = build_opener(NoRedirect())
    item = fetch('bsd',path,params or {},opener=lambda request,timeout:transport.open(request,timeout=min(5,timeout)))
    call = item['call']
    if not call.get('api_success'):
        if call.get('error_code')=='NETWORK_ERROR':
            raise URLError('network error')
        code = call.get('http_status')
        if code:
            raise HTTPError(call['url'],code,'provider error',None,None)
        raise ValueError(call.get('error_code','request_failed'))
    return item['response'],call['response_sha256']


def bsd_event(rows, f):
    matches = [e for e in rows if e.get('league_id')==9 and e.get('stage')=='regular-season'
               and e.get('status')=='finished' and not e.get('replaced_by') and not e.get('penalty_shootout')
               and aware(e['event_date']) == aware(f['kickoff_at'])
               and e.get('home_team')==BSD_TEAMS.get(f['home'][5:]) and e.get('away_team')==BSD_TEAMS.get(f['away'][5:])
               and (e.get('home_score'),e.get('away_score')) == (f['home_goals'], f['away_goals'])]
    if len(matches) != 1:
        raise ValueError('no unique league, kickoff, oriented team names and regulation score match')
    return matches[0]['id']


def xg_values(record, event_id):
    if int(record['event_id'])!=event_id:
        raise ValueError('BSD stats event mismatch')
    result = {'provider_event_id':event_id,'xg_estimated':record.get('xg_estimated'), 'teams':{}}
    for s in SIDES:
        team = (record.get('stats') or {}).get(s) or {}
        xg = team.get('xg') or {}
        fields = {'expected_goals':team.get('expected_goals'),'xg_actual':xg.get('actual')}
        if any(v is not None and (type(v) not in (float,int) or not math.isfinite(v) or v<0) for v in fields.values()):
            raise ValueError('invalid BSD xG')
        result['teams'][s] = dict(fields, xg_actual_estimated=xg.get('estimated'))
    return result


def bsd_job(src, history):
    base = 'https://sports.bzzoiro.com/api/v2'
    directories, output = {}, {}
    for mid, f in sorted(history.items()):
        day = aware(f['kickoff_at']).date().isoformat()
        if day not in directories:
            fields = ('id','league_id','stage','status','replaced_by','penalty_shootout','event_date',
                      'home_team','away_team','home_score','away_score')
            def facts(r):
                return [{k:v.get(k) for k in fields} for v in r['results'] if v['league_id']==9]
            directories[day] = src.get(base+'/events/?league_id=9&limit=100&date='+day,
                                      lambda:bsd_load('/events/',{'league_id':9,'limit':100,'date':day}),facts,ttl=timedelta(days=1))
        directory = directories[day]
        if directory['status'] != 'ok':
            output[mid] = directory
            continue
        try:
            eid = bsd_event(directory['facts'],f)
        except ValueError as exc:
            output[mid] = {'status':'missing','reason':str(exc),'directory':directory['source_sha256']}
            continue
        path = f'/events/{eid}/stats/'
        item = src.get(base+path,lambda:bsd_load(path),lambda raw:xg_values(raw,eid),ttl=timedelta(days=1))
        output[mid] = dict(item, directory=directory['source_sha256'])
    return output


def markets(data, fixtures, stamp):
    candidates = []
    for m in (data/'market/runs').glob('*-bra1/source-metadata.json'):
        sources = read(m)
        if sources.get('odds',{}).get('captured_at'):
            received = aware(sources['odds']['captured_at'])
            if timedelta(0) <= stamp-received <= timedelta(hours=1):
                candidates.append((received,m.parent,sources))
    result = {}
    for _,root,sources in sorted(candidates,reverse=True):
        rebuilt = market_snapshot.build(root,sources['odds'],sources['identity'],competition='bra.1',
                                        cutoff=stamp.isoformat(),now=stamp,market_max_age_seconds=3600)
        for snapshot in rebuilt['snapshots']:
            mid = snapshot['target']['match_id']
            if mid not in result and mid in fixtures:
                q = market_pair(snapshot)
                if q:
                    result[mid] = dict(q,archive=root.relative_to(data).as_posix())
    return result


def has_real_xg(obs):
    if obs.get('status') != 'ok':
        return False
    facts = obs['facts']
    return (facts['xg_estimated'] is False and all(
        facts['teams'][s]['expected_goals'] is not None or
        (facts['teams'][s]['xg_actual_estimated'] is False and facts['teams'][s]['xg_actual'] is not None)
        for s in SIDES))


def lineup_window(kickoff, through, live, old=None):
    """Retain the first confirmed XI observed in the registered T-90 window."""
    start = kickoff - timedelta(minutes=90)
    window = dict(old or {})
    stamp = live.get('observed_at')
    if stamp and start <= aware(stamp) < kickoff and (live.get('status')=='ok' or live.get('attempted')):
        if live['status']=='ok':
            window['last_successful_observation_at'] = stamp
            if live['facts'].get('both_elevens') and 'first_confirmed' not in window:
                window['first_confirmed'] = live
        elif 'last_failure' not in window or aware(stamp) > aware(window['last_failure']['observed_at']):
            window['last_failure'] = {'observed_at':stamp,'reason':live['reason']}
    if window.get('first_confirmed'):
        state = 'confirmed'
    elif through < start:
        state = 'pending'
    elif window.get('last_successful_observation_at'):
        state = 'queried_not_published'
    elif window.get('last_failure'):
        state = 'provider_unavailable'
    else:
        state = 'window_not_sampled' if through>=kickoff else 'awaiting_observation'
    window['state'] = state
    return window


def quality_counts(rows):
    counts = {stage:{key:sum(r['quality'][stage][key] for r in rows) for key in rows[0]['quality'][stage]}
              for stage in ('original_first_issue','latest_observation')}
    states = ('pending','confirmed','queried_not_published','provider_unavailable','window_not_sampled','awaiting_observation')
    counts['lineup_window'] = {k:sum(r['quality']['lineup_window']==k for r in rows) for k in states}
    return counts


def persist(out, evidence, stamp):
    (out/'runs').mkdir(exist_ok=True)
    path = out/'runs'/(stamp.strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    if path.exists():
        raise ValueError('duplicate observation timestamp')
    atomic_json(path,evidence)
    summary = {'latest':path.relative_to(out).as_posix(),'observed_through':evidence['observed_through'],
               'snapshot_sha256':sha(path.read_bytes()),'registered':len(evidence['fixtures']),
               'coverage_counts':evidence['coverage_counts'],'status':evidence['status']}
    atomic_json(out/'latest.json',summary)
    return summary


def terminal_report(out, prior, stamp):
    if prior['status']=='batch_closed':
        return read(out/'latest.json')
    rows = prior['fixtures']
    for row in rows:
        row.setdefault('market_cutoff_at',prior['started_at'])
        window = lineup_window(aware(row['kickoff_at']),stamp,{},row.get('lineup_window_evidence'))
        row['lineup_window_evidence'] = window
        row['quality']['lineup_window'] = window['state']
    evidence = dict(prior,status='batch_closed',started_at=stamp.isoformat(),observed_through=stamp.isoformat(),
                    source_requests={},fixtures=rows,coverage_counts=quality_counts(rows))
    return persist(out,evidence,stamp)


def collect(data, out, *, clock=now):
    data, out = Path(data).resolve(), Path(out)
    out.mkdir(parents=True,exist_ok=True)
    with exclusive_lock(out/'.collect.lock'):
        stamp = clock()
        plan, fixtures, prior, membership, first = original_inputs(data)
        previous = read(out/read(out/'latest.json')['latest']) if (out/'latest.json').exists() else {}
        previous_rows = {r['match_id']:r for r in previous.get('fixtures',[])}
        all_closed = all(aware(f['kickoff_at']) <= stamp for f in fixtures)
        if all_closed and previous:
            return terminal_report(out,previous,stamp)
        if all_closed:
            return {'status':'batch_closed_without_prematch_observations','registered':len(fixtures)}
        sources = {name:Source(out,name) for name in ('espn','openfootball','bsd')}
        with ThreadPoolExecutor(max_workers=3) as pool:
            espn = pool.submit(espn_job,sources['espn'],fixtures,prior,stamp)
            textdata = pool.submit(public_job,sources['openfootball'],fixtures)
            bsd = pool.submit(bsd_job,sources['bsd'],prior)
            quote = markets(data,{f['match_id'] for f in fixtures},stamp)
            current, shots = espn.result()
            openfile, xg = textdata.result(), bsd.result()
        rows = []
        done = clock()
        for f in fixtures:
            mid = f['match_id'];ko = aware(f['kickoff_at']);live = current.get(mid,{})
            old = previous_rows.get(mid,{})
            if ko <= stamp and old:
                row = dict(old)
                row.setdefault('market_cutoff_at',previous['started_at'])
                window = lineup_window(ko,done,{},old.get('lineup_window_evidence'))
                row['lineup_window_evidence'] = window
                row['quality']['lineup_window'] = window['state']
                rows.append(row)
                continue
            lineup = live.get('facts',{}).get('both_elevens',False)
            window = lineup_window(ko,done,live,old.get('lineup_window_evidence'))
            reported_xg = lambda event: event.get('status')=='ok' and all(any(event['facts']['teams'][s][key] is not None for key in ('expected_goals','xg_actual')) for s in SIDES)
            history_ids = membership[mid]
            paired_shots = all(all(shots.get(history_ids.get(s),{}).get('facts',{}).get('statistics',{}).get(side,{}).get('totalShots') is not None for side in SIDES) for s in SIDES)
            quality = {'original_first_issue':{'confirmed_lineups':first[mid]['facts']['both_elevens'],
                           'timed_h2h_and_total':bool(plan['payload']['markets'][mid])},
                       'latest_observation':{'confirmed_lineups':lineup,'fresh_h2h_and_total':mid in quote,
                           'both_prior_games_reported_xg':all(reported_xg(xg.get(history_ids.get(s),{})) for s in SIDES),
                           'both_prior_games_shots':paired_shots,'both_prior_games_xg':all(has_real_xg(xg.get(history_ids.get(s),{})) for s in SIDES),
                           'open_file_date_and_teams':openfile.get(mid,{}).get('date_and_sides_verified',False)},
                       'lineup_window':window['state']}
            rows.append({'match_id':mid,'kickoff_at':f['kickoff_at'],'history_ids':history_ids,'quality':quality,'lineup_window_evidence':window,
                         'market_cutoff_at':stamp.isoformat(),'first_cutoff_at':f['cutoff_at'],'original_lineup':first[mid],'current_lineup':live,'openfootball':openfile.get(mid),
                         'latest_valid_market':quote.get(mid),'original_market':plan['payload']['markets'][mid]})
        quality = quality_counts(rows)
        evidence = {'schema':'WCPP-BRAZIL-SUPPLEMENT-1','status':'prematch_observation','started_at':stamp.isoformat(),'observed_through':done.isoformat(),
                    'registered':len(rows),'registered_plan':plan['record_sha256'],'coverage_counts':quality,
                    'source_requests':{k:s.calls for k,s in sources.items()},'fixtures':rows,'history':prior,
                    'previous_games_espn':shots,'previous_games_bsd':xg,
                    'history_selection':'Most recent completed Brazilian Serie A match for each side from the original ESPN last-five list; repeated event IDs counted once.',
                    'asof':'Supplemental fields first observed after original issue belong only to later research observations. Provider quote time, capture time and observation times are retained separately.',
                    'rights':'OpenFootball plain text is CC0-1.0. ESPN and BSD coverage stores factual extracts and response hashes, excluding media, commentary and full provider responses.'}
        return persist(out,evidence,stamp)


def verify_only(data, out):
    """Offline validation against source evidence, the original enrollment and quality denominators."""
    data, out = Path(data).resolve(), Path(out).resolve()
    index = read(out/'latest.json')
    path = (out/index['latest']).resolve()
    if path.parent!=out/'runs' or sha(path.read_bytes())!=index['snapshot_sha256']:
        raise ValueError('quality snapshot digest or location differs')
    report = read(path)
    plan, fixtures, history, membership, first = original_inputs(data)
    rows = {r['match_id']:r for r in report['fixtures']}
    if (set(rows)!={f['match_id'] for f in fixtures} or len(rows)!=len(report['fixtures'])
            or report['registered_plan']!=plan['record_sha256'] or report['history']!=history):
        raise ValueError('supplemental population differs from enrollment')
    objects = {}
    for file in (out/'cache').rglob('*.json'):
        r = read(file)
        if r.get('status')=='ok':
            objects[(r['url'],r['source_sha256'])] = r
    def verify_source(ref):
        if ref and ref.get('status')=='ok':
            cached = objects.get((ref['url'],ref['source_sha256']))
            if not cached or ('facts' in ref and cached['facts']!=ref['facts']):
                raise ValueError('provider projection differs from cached evidence')
            if ref.get('first_observed_at') and aware(ref['first_observed_at'])>aware(ref['observed_at']):
                raise ValueError('invalid observation timestamps')
            if aware(ref['observed_at'])>aware(report['observed_through']):
                raise ValueError('source first seen after this report')
            return cached
        return None
    for mid, f in history.items():
        verify_source(report['previous_games_espn'][mid])
        bsd = report['previous_games_bsd'][mid]
        if verify_source(bsd):
            day = aware(f['kickoff_at']).date().isoformat()
            url = 'https://sports.bzzoiro.com/api/v2/events/?league_id=9&limit=100&date='+day
            directory = objects[(url,bsd['directory'])]
            if bsd_event(directory['facts'],f)!=bsd['facts']['provider_event_id']:
                raise ValueError('BSD identity differs from retained provider directory')
    for f in fixtures:
        mid = f['match_id']
        r = rows[mid]
        if (r['original_lineup']!=first[mid] or r['original_market']!=plan['payload']['markets'][mid]
                or r['history_ids']!=membership[mid] or r['kickoff_at']!=f['kickoff_at']):
            raise ValueError('first issue or prior events differ')
        ko=aware(f['kickoff_at'])
        live = verify_source(r['current_lineup'])
        if live and aware(r['current_lineup']['observed_at'])>=ko:
            raise ValueError('current lineup first seen after kickoff')
        window = r['lineup_window_evidence']
        confirmed = window.get('first_confirmed')
        if confirmed:
            verify_source(confirmed)
            if not ko-timedelta(minutes=90)<=aware(confirmed['observed_at'])<ko or not confirmed['facts']['both_elevens']:
                raise ValueError('invalid window lineup')
        openref = r['openfootball']
        o = verify_source(openref)
        if o:
            licensed = objects[(OPEN_BASE+'LICENSE.md',openref['license_sha256'])]
            schedule = o['facts'].encode()
            listing = open_rows(schedule)
            ratified = [ko.astimezone(ZoneInfo('America/Sao_Paulo')).date().isoformat(),
                        OPEN_TEAMS[f['home'][5:]],OPEN_TEAMS[f['away'][5:]]]
            if (sha(schedule)!=openref['source_sha256'] or not licensed['facts'].startswith('CC0 1.0 Universal')
                    or sha(licensed['facts'].encode())!=openref['license_sha256'] or openref['registered_identity']!=ratified
                    or (listing.count(ratified)==1)!=openref['date_and_sides_verified']):
                raise ValueError('public file evidence differs')
        quote = r.get('latest_valid_market')
        if quote:
            root=(data/quote['archive']).resolve()
            if not root.is_relative_to(data/'market/runs'):
                raise ValueError('quote outside market archive')
            meta=read(root/'source-metadata.json')
            # Retain the quote cutoff of the actual prematch observation when
            # a later terminal review carries that fixture forward.
            cutoff = r.get('market_cutoff_at', report['started_at'])
            feed=market_snapshot.build(root,meta['odds'],meta['identity'],competition='bra.1',
                                       cutoff=cutoff,now=aware(cutoff),market_max_age_seconds=3600)
            matched=[market_pair(s) for s in feed['snapshots'] if s['target']['match_id']==mid]
            if len(matched)!=1 or matched[0]!={k:v for k,v in quote.items() if k!='archive'}:
                raise ValueError('quote differs from original market evidence')
        sides_history=r['history_ids']
        xg=[report['previous_games_bsd'].get(sides_history.get(s),{}) for s in SIDES]
        proc=[report['previous_games_espn'].get(sides_history.get(s),{}) for s in SIDES]
        reported=all(entry.get('status')=='ok' and all(any(entry['facts']['teams'][s][key] is not None for key in ('expected_goals','xg_actual')) for s in SIDES) for entry in xg)
        shots=all(all(entry.get('facts',{}).get('statistics',{}).get(s,{}).get('totalShots') is not None for s in SIDES) for entry in proc)
        if report['status']!='batch_closed' and aware(r['kickoff_at'])>aware(report['started_at']):
            expected = {'confirmed_lineups':r['current_lineup'].get('facts',{}).get('both_elevens',False),
                        'fresh_h2h_and_total':quote is not None,'both_prior_games_reported_xg':reported,
                        'both_prior_games_xg':all(has_real_xg(v) for v in xg),'both_prior_games_shots':shots,
                        'open_file_date_and_teams':bool(openref and openref['date_and_sides_verified'])}
            if r['quality']['latest_observation']!=expected:
                raise ValueError('field availability differs from retained values')
    counts = quality_counts(list(rows.values()))
    if counts!=report['coverage_counts'] or counts!=index['coverage_counts']:
        raise ValueError('coverage denominators differ')
    return {'status':'VERIFIED','quality_snapshot':index['latest'],'registered':len(rows),
            'prior_game_count':len(history),'quality_snapshot_sha256':index['snapshot_sha256']}


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--verify-only',action='store_true')
    args=parser.parse_args()
    operation = verify_only if args.verify_only else collect
    print(json.dumps(operation(args.data_root,args.out),sort_keys=True))
