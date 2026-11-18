"""Roll the Nations League Elo trial forward against actual fixtures and recent senior internationals."""
import argparse
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from nations_league import elo_reference as elo
from publication import atomic_json, deliver
from research.analysis_freeze import aware, digest, save, verify
from research_program.nations_phase import classify_source_phase, resolve_phase_evidence
from research_program.three_arm_model import score_picks


ESPN = 'https://site.api.espn.com/apis/site/v2/sports/soccer/'
WORLD = 'https://api.github.com/repos/martj42/international_results/commits'
SOURCE_FAILURE = (OSError, URLError, ValueError, KeyError, TypeError, IndexError)


def now():
    return datetime.now(timezone.utc)


def fetch(url):
    request = Request(url, headers={'User-Agent':'football-analysis-nations-elo/1.0', 'Accept':'*/*'})
    with urlopen(request, timeout=15) as response:
        raw = response.read()
    return raw, {'url':url, 'captured_at':now().isoformat(), 'sha256':hashlib.sha256(raw).hexdigest()}


def retain(store, raw, meta):
    # Gzip headers are reproducible, while source identity is the uncompressed hash.
    path = store/'sources'/(meta['sha256']+'.gz')
    path.parent.mkdir(parents=True, exist_ok=True)
    deliver(path,gzip.compress(raw,mtime=0))
    return {**meta,'object':str(path.relative_to(store))}


def months(first, end):
    month = first.replace(day=1)
    while month <= end:
        yield month.strftime('%Y%m')
        month = (month.replace(day=28)+timedelta(days=4)).replace(day=1)


def sides(event):
    comp, = event['competitions']
    sides = {r['homeAway']:r for r in comp['competitors']}
    if set(sides) != {'home','away'} or len(comp['competitors']) != 2:
        raise ValueError('unresolved opponents')
    return sides


def team_names(event):
    return {s:elo.ALIASES.get(r['team']['displayName'],r['team']['displayName']) for s,r in sides(event).items()}


def existing(store, mid, track):
    scope = dict(match_id=mid,stage='prematch',track=track)
    path = store/'freezes'/digest(scope)/'000001.json'
    if path.exists():
        return verify(json.loads(path.read_bytes()))
    return None


def first(store, track, fixture, payload):
    # A repeated collection must never create another first forecast at a later cutoff.
    prior = existing(store,fixture['match_id'],track)
    if prior:
        return prior['record_sha256']
    receipt = save(payload,fixture,stage='prematch',track=track,root=store/'freezes',
                   idempotency_key=track+':'+fixture['match_id'])
    return receipt['sha256']


def coverage(teams, finished, world_games, at):
    """Compare the latest reported outcomes by named sides; world dates are local calendar dates."""
    pairs = {}
    for row in world_games:
        if row['home'] in teams.values() or row['away'] in teams.values():
            pairs.setdefault(frozenset((row['home'],row['away'])),[]).append(row)
    evidence = {}
    for side, name in teams.items():
        outstanding, compared = [], []
        events = []
        for event in finished[side]:
            c, = event['competitions']
            if c['status']['type']['state'] == 'post' and c['status']['type']['completed'] and aware(event['date']) < at:
                events.append(event)
        for event in sorted({str(e['id']):e for e in events}.values(),key=lambda e:e['date'],reverse=True)[:5]:
            names = team_names(event)
            if name not in names.values():
                raise ValueError('unrelated opponent result')
            points = {}
            for orientation, competitor in sides(event).items():
                score = competitor['score']
                points[names[orientation]] = int(float(score['value'] if isinstance(score,dict) else score))
            day = aware(event['date']).date()
            candidates = [r for r in pairs.get(frozenset(names.values()),[])
                          if abs((datetime.fromisoformat(r['date']).date()-day).days)<=1
                          and points[r['home']]==r['score'][0] and points[r['away']]==r['score'][1]]
            ref = {'event':str(event['id']), 'kickoff_at':event['date'], 'score_by_team':points}
            if len(candidates)==1 and candidates[0]['date'] < at.date().isoformat():
                compared.append({**ref,'world_date':candidates[0]['date']})
            else:
                outstanding.append(ref)
        evidence[side]={'team':name,'confirmed':compared,'unmatched':outstanding}
    return evidence


def world_snapshot(store, at):
    q=urlencode({'path':'results.csv','per_page':1,'until':at.isoformat()})
    raw, lookup=fetch(WORLD+'?'+q)
    lookup=retain(store,raw,lookup)
    ref=json.loads(raw)[0]
    sha=ref['sha']
    if not re.fullmatch('[a-f0-9]{40}',sha) or aware(ref['commit']['committer']['date'])>at:
        raise ValueError('unavailable Git vintage')
    raw, source=fetch('https://raw.githubusercontent.com/martj42/international_results/'+sha+'/results.csv')
    source=retain(store,raw,source)
    yesterday=(at.date()-timedelta(days=1)).isoformat()
    games=elo.international_matches(raw,yesterday)
    return games, {'commit':sha,'commit_date':ref['commit']['committer']['date'],'lookup':lookup,'data':source}


def candidate(event, month_raw, month_meta, study, store, window, cache):
    mid='espn:'+str(event['id'])
    preliminary=classify_source_phase(event)
    if preliminary['stage'] and preliminary['stage']!='league-phase' and not preliminary['phase_conflict']:
        return {'match_id':mid,'status':'OUTSIDE_LEAGUE_PHASE','phase':preliminary['stage']}
    if not str(event['id']).isdigit():
        raise ValueError('invalid event ID')
    raw,source=fetch(ESPN+'uefa.nations/summary?'+urlencode({'event':event['id']}))
    known=retain(store,raw,source)
    scoreboard=retain(store,month_raw,month_meta)
    data=json.loads(raw)
    header=data['header']
    comp,=header['competitions']
    if (header['league']['slug']!='uefa.nations' or str(header['id'])!=str(event['id'])
            or comp['status']['type']['state']!='pre' or comp['status']['type']['completed'] is not False):
        return {'match_id':mid,'status':'DEFERRED_UNCONFIRMED_PREMATCH','source':known}
    kickoff=aware(comp['date'])
    cutoff=now()
    supplement={'source_path':str((store/scoreboard['object']).resolve()),
                'source_sha256':hashlib.sha256((store/scoreboard['object']).read_bytes()).hexdigest(),
                'source_url':month_meta['url'],'query_time':month_meta['captured_at']}
    phase=resolve_phase_evidence(data,supplement,{'cutoff_at':cutoff.isoformat()})
    if not phase['complete'] or phase['stage']!='league-phase' or phase['league_level'] not in ('A','B','C','D'):
        return {'match_id':mid,'status':'DEFERRED_UNCONFIRMED_GRADE' if not phase['complete'] else 'OUTSIDE_LEAGUE_PHASE',
                'phase':phase['stage'],'level':phase['league_level'],'summary':known,'schedule':scoreboard}
    opponents=sides(header)
    names=team_names(header)
    if len(set(names.values()))!=2 or any(not str(v['team']['id']).isdigit() for v in opponents.values()):
        raise ValueError('opponents not yet identified')
    if type(comp.get('neutralSite')) is not bool:
        return {'match_id':mid,'status':'DEFERRED_UNKNOWN_VENUE','summary':known}
    fixture={'match_id':mid,'competition':'uefa.nations','stage':'league-phase','league_level':phase['league_level'],
             'home':names['home'],'away':names['away'],'neutral':comp['neutralSite'],'kickoff_at':kickoff.isoformat(),
             'cutoff_at':cutoff.isoformat()}
    registration=first(store,'nations-elo-registration',fixture,{'schedule':scoreboard,'summary':known,'automatic_promotion':False})
    result={'match_id':mid,'registration_sha256':registration,'kickoff_at':fixture['kickoff_at']}
    original=existing(store,mid,'nations-elo-independent')
    if original:
        return dict(result,status='ALREADY_ISSUED',freeze_sha256=original['record_sha256'])
    if kickoff<=cutoff or kickoff>cutoff+timedelta(hours=window):
        return dict(result,status='REGISTERED_WAIT_FOR_FORECAST_WINDOW')
    if 'world' not in cache:
        cache['world']=world_snapshot(store,cutoff)
    games,vintage=cache['world']
    finished={}
    sources={}
    for side,opponent in opponents.items():
        tid=str(opponent['team']['id'])
        url=ESPN+'all/teams/'+tid+'/schedule?'+urlencode({'season':cutoff.year,'limit':100})
        if url not in cache:
            raw,meta=fetch(url)
            cache[url]=(json.loads(raw),retain(store,raw,meta))
        page,sources[side]=cache[url]
        if str(page['team']['id'])!=tid:
            raise ValueError('national fixture history team identity changed')
        # The all-competition schedule can lag the Nations scoreboard by one match.
        recent=[e for e in cache['recent_nations'] if any(str(r['team']['id'])==tid for r in sides(e).values())]
        finished[side]=page['events']+recent
    checked=coverage(names,finished,games,cutoff)
    if any(v['unmatched'] or not v['confirmed'] for v in checked.values()):
        return dict(result,status='DEFERRED_INCOMPLETE_NATIONAL_RESULTS',coverage=checked,
                    history_sources=sources,world_source=vintage)
    day=cutoff.date().isoformat()
    ratings=elo.dated_ratings(games,[day])[day]
    if not set(names.values())<=ratings.keys():
        return dict(result,status='DEFERRED_UNRATED_TEAM',teams=names)
    report_raw=(study/'report.json').read_bytes()
    fit=json.loads(report_raw)['campaigns']['2026']['fits'][fixture['league_level']]['elo']
    rates=[ratings[names[s]] for s in ('home','away')]
    pmf=elo.forecast(fit,rates,fixture['neutral'])
    picks=score_picks(pmf,line=2.5)
    fixture['cutoff_at']=now().isoformat()
    body={'fixture_registration':registration,'summary':known,'schedule':scoreboard,'rating_sources':vintage,
          'history_sources':sources,'recent_coverage':checked,'pre_match_elo':rates,
          'artifact_report_sha256':hashlib.sha256(report_raw).hexdigest(),'calibration':fit,
          'method':'independent international Elo; fixed 2.5 research line','matrix':pmf.tolist(),'picks':picks,
          'actual_market_line':None, 'automatic_promotion':False}
    checksum=first(store,'nations-elo-independent',fixture,body)
    return dict(result,status='ISSUED',freeze_sha256=checksum)


def monitor(study,horizon=90,window=48):
    start=now()
    end=start.date()+timedelta(days=horizon)
    store=study/'forward'
    store.mkdir(parents=True,exist_ok=True)
    scans,rows,errors=[],[],[]
    future={}
    cache={'recent_nations':[]}
    for month in months(start.date(),end):
        url=ESPN+'uefa.nations/scoreboard?'+urlencode({'dates':month,'limit':1000})
        try:
            raw,meta=fetch(url)
            data=json.loads(raw)
            if {l['slug'] for l in data['leagues']}!={'uefa.nations'}:raise ValueError('wrong competition')
            events=data['events']
            scans.append({**meta,'events':len(events)})
            for event in events:
                status=event['competitions'][0]['status']['type']['state']
                if status=='post':cache['recent_nations'].append(event)
                if status=='pre' and start<aware(event['date']) and aware(event['date']).date()<=end:
                    future[str(event['id'])]=(event,raw,meta)
        except SOURCE_FAILURE as exc:
            errors.append({'url':url,'error':str(exc)})
    for mid,(event,raw,meta) in sorted(future.items()):
        try:
            rows.append(candidate(event,raw,meta,study,store,window,cache))
        except SOURCE_FAILURE as exc:
            rows.append({'match_id':'espn:'+mid,'status':'DEFERRED_SOURCE_OR_IDENTITY','detail':str(exc)})
    report={'as_of':now().isoformat(),'horizon_end':end.isoformat(),'status':'PARTIAL_DISCOVERY' if errors else 'COMPLETE',
            'forecast_hours':window,'schedule_sources':scans,'discovery_errors':errors,'scheduled':len(future),
            'registered':sum('registration_sha256' in row for row in rows),
            'issued_or_original':sum(row['status'] in ('ISSUED','ALREADY_ISSUED') for row in rows),'fixtures':rows}
    atomic_json(store/'latest.json',report)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study',type=Path,required=True)
    args=parser.parse_args()
    run=monitor(args.study)
    print(json.dumps({k:run[k] for k in ('as_of','horizon_end','status','scheduled','registered','issued_or_original')}))
