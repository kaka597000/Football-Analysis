"""Bounded free-source collection into a private, append-only research state.

Unknown identity, phase or regulation score is quarantined, never guessed.
No credential is required or read. Raw responses belong in private storage.
"""
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
import time
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.parse import urlencode

ESPN = 'https://site.api.espn.com/apis/site/v2/sports/soccer/'
ASIAN = 'https://asianodds.org/d/events?sport=fb'
MAX_REQUESTS = 100
REGULAR = {'eng.1','eng.2','esp.1','ger.1','fra.1','ita.1','ned.1','por.1',
           'bra.1','swe.1','ksa.1','nor.1','usa.1','aus.1','kor.1','jpn.1'}
CUP_STAGES = {'first-round':'first round','second-round':'second round',
              'third-round':'third round','fourth-round':'fourth round',
              'quarterfinals':'quarterfinal'}


def aware(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None: raise ValueError('timezone required')
    return result.astimezone(timezone.utc)


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()


def append(path, value):
    with path.open('a') as f: f.write(encoded(value).decode()+'\n')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None


def http_fetch(url, headers=None):
    """Fixed public sources only, TLS/proxy defaults preserved."""
    if not (url.startswith(ESPN) or url == ASIAN): raise ValueError('unsupported source')
    req = Request(url, headers={'User-Agent':'WCPP-research', **(headers or {})})
    with build_opener(NoRedirect()).open(req, timeout=25) as response:
        data = response.read(12_000_001)
        if len(data)>12_000_000: raise ValueError('response too large')
        return data


def normalize_scopes(scopes):
    if isinstance(scopes, dict):
        scopes = [dict(v, cohort=k) for k,v in scopes.items()]
    output=[]
    for scope in scopes:
        item=dict(scope)
        if not item.get('competition') or not item.get('cohort'): raise ValueError('scope identity required')
        if item['competition'] not in REGULAR | {'mex.1','eng.league_cup','afc.cup','afc.champions','uefa.champions'}:
            raise ValueError('competition has no registered unattended adapter')
        if item.get('gender', 'men') not in ('men','male') or item.get('age','senior')!='senior':
            raise ValueError('only registered adult male scopes supported')
        item['stages']=item.get('stages',item.get('forecast_scope',{}).get('stages',[]))
        output.append(item)
    return output


def phase_for(scope, event):
    """Use explicit native phase text; never map a knockout to a regular route."""
    comp=scope['competition'];cohort=scope['cohort'];season=event.get('season',{})
    text=' '.join(str(season.get(k,'')) for k in ('name','slug','type')).lower().replace('_',' ').replace('-',' ')
    stages=scope['stages']
    if comp=='eng.league_cup':
        for stage, needle in CUP_STAGES.items():
            if needle in text and stage in stages:return stage
        return None
    if comp in ('afc.cup','afc.champions','uefa.champions'):
        year=season.get('year')
        if type(year) is not int or year<2024:return None
        if comp=='afc.cup' and 'CHAMPIONS_TWO' in cohort and 'group' in text and 'group_stage' in stages:
            return 'group_stage'
        if comp in ('afc.champions','uefa.champions') and 'league phase' in text and 'league_phase' in stages:
            return 'league_phase'
        return None
    if comp=='mex.1':
        if ('apertura' in cohort and 'apertura' not in text) or ('clausura' in cohort and 'clausura' not in text):return None
        if any(w in text for w in ('playoff','play off','final','knockout','liguilla')):return None
        return 'regular' if 'regular' in stages else None
    if comp in REGULAR:
        if any(w in text for w in ('playoff','play off','relegation','championship group','knockout','100 year','centennial')):return None
        # 2026's Japanese transition competition must not enter J1 regular history.
        if comp=='jpn.1' and str(event.get('date','')).startswith('2026') and event.get('date','')<'2026-07-01':return None
        return next((s for s in stages if s in ('regular','regular-season')),None)
    return None


def regulation_row(scope, event, summary, captured_at, raw_ref):
    header=summary.get('header',{});eid=str(event.get('id'))
    if str(header.get('id'))!=eid:raise ValueError('summary fixture mismatch')
    c=header.get('competitions',[{}])[0]
    typ=c.get('status',{}).get('type',{})
    if typ.get('completed') is not True:raise ValueError('result not completed')
    if summary.get('format',{}).get('regulation',{}).get('periods')!=2:
        raise ValueError('regulation periods not verified')
    if aware(c['date'])!=aware(event.get('date',c['date'])):raise ValueError('kickoff conflict')
    if aware(c['date'])>=aware(captured_at):raise ValueError('future result')
    merged=dict(event,**header)
    stage=phase_for(scope,merged)
    if stage is None:raise ValueError('phase or era not compatible')
    teams={t.get('homeAway'):t for t in c.get('competitors',[])}
    if set(teams)!= {'home','away'}:raise ValueError('home away not verified')
    listed={t.get('homeAway'):str(t.get('team',{}).get('id')) for t in event.get('competitions',[{}])[0].get('competitors',[])}
    scores=[];ids=[]
    for side in ('home','away'):
        team=teams[side];tid=str(team.get('team',{}).get('id',''))
        if not tid.isdigit() or listed.get(side)!=tid:raise ValueError('team identity conflict')
        lines=team.get('linescores',[])
        if len(lines)<2:raise ValueError('90-minute period scores unavailable')
        values=[]
        for line in lines[:2]:
            value=line.get('value',line.get('displayValue'))
            if isinstance(value,bool):raise ValueError('invalid period score')
            number=float(value)
            if not number.is_integer() or number<0 or number>30:raise ValueError('invalid period score')
            values.append(int(number))
        scores.append(sum(values));ids.append('espn:'+tid)
    return {'match_id':'espn:'+eid,'cohort':scope['cohort'],'competition':scope['competition'],
        'stage':stage,'season':str(header.get('season',{}).get('year','UNKNOWN')),
        'date':aware(c['date']).date().isoformat(),'kickoff_at':aware(c['date']).isoformat(),
        'home':ids[0],'away':ids[1],'home_goals':scores[0],'away_goals':scores[1],
        'neutral':c.get('neutralSite') if type(c.get('neutralSite')) is bool else None,
        'gender':'male','age':'senior','score_basis':'regulation_90','known_at':captured_at,
        'known_at_policy':'ACTUAL_CAPTURE_NOT_HISTORICAL_FIRST_KNOWN',
        'source_url':ESPN+scope['competition']+'/summary?event='+eid,
        'provider_final_score':[str(teams[s].get('score')) if str(teams[s].get('score','')).isdigit() else None for s in ('home','away')],
        'raw_reference':raw_ref,'event_coverage':'NOT_CERTIFIED'}


def collect(state_dir, scopes, now=None, fetcher=None, lookback_days=2, max_seconds=300):
    """Collect bounded private snapshots; caller serializes whole automation runs.

    fetcher(url, headers=None) -> JSON-compatible object or raw UTF-8 JSON bytes.
    Returned rows are additive observations, never silent result replacements.
    """
    if type(lookback_days) is not int or not 0<=lookback_days<=6:raise ValueError('lookback 0..6 required')
    if isinstance(max_seconds,bool) or not isinstance(max_seconds,(int,float)) or not 0<max_seconds<=1200:
        raise ValueError('max_seconds must be positive and at most 1200')
    started_monotonic=time.monotonic();time_limit_reached=False
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None:raise ValueError('timezone-aware collection time required')
    now=now.astimezone(timezone.utc)
    scopes=normalize_scopes(scopes);root=Path(state_dir);root.mkdir(parents=True,exist_ok=True)
    (root/'raw').mkdir(exist_ok=True);stamp=now.isoformat();fetcher=fetcher or http_fetch
    live=fetcher is http_fetch
    calls=0;retry_used=False;successful=0;rows=[];quarantine=0;events_seen=0;cache={}
    existing=set();previous={}
    target=root/'normalized-results.jsonl'
    if target.exists():
        for line in target.read_text().splitlines():
            x=json.loads(line);existing.add((x['cohort'],x['match_id'],x['home_goals'],x['away_goals']));previous[(x['cohort'],x['match_id'])]=x
    def get(url,headers=None):
        nonlocal calls,retry_used,successful,time_limit_reached
        if url in cache:return cache[url]
        value=None
        for attempt in range(2):
            if calls>=MAX_REQUESTS or attempt and retry_used:break
            # Leave room for the fixed per-request timeout and low-frequency delay.
            # An in-flight request is bounded by http_fetch's 25-second timeout.
            if time.monotonic()-started_monotonic+25.2>=max_seconds:
                time_limit_reached=True;break
            if attempt:retry_used=True
            calls+=1
            if live:time.sleep(.2)
            started=datetime.now(timezone.utc).isoformat()
            try:
                content=fetcher(url,headers=headers)
                raw=content if isinstance(content,bytes) else encoded(content)
                body=json.loads(raw)
                if url.startswith(ESPN) and not isinstance(body,dict):raise ValueError('structured ESPN object required')
                digest=hashlib.sha256(raw).hexdigest();p=root/'raw'/(digest+'.json.gz')
                if not p.exists():p.write_bytes(gzip.compress(raw,mtime=0))
                captured=datetime.now(timezone.utc).isoformat()
                ref={'path':'raw/'+p.name,'sha256':digest,'captured_at':captured,'source_url':url}
                append(root/'requests.jsonl',{'source_url':url,'started_at':started,'captured_at':captured,'status':'OK','raw':ref})
                value=(body,ref);successful+=1;break
            except Exception as exc:
                append(root/'requests.jsonl',{'source_url':url,'started_at':started,'captured_at':stamp,'status':'FAILED','error_type':type(exc).__name__})
                if getattr(exc,'code',None) in (401,403,429):break
        cache[url]=value;return value
    by_comp={};coverage={}
    for scope in scopes:by_comp.setdefault(scope['competition'],[]).append(scope)
    for comp,group in sorted(by_comp.items()):
        coverage[comp]={'scoreboards_ok':0,'events_seen':0,'new_rows':0,'quarantined':0}
        if any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789._' for c in comp):raise ValueError('invalid competition code')
        for lag in range(lookback_days,-1,-1):
            day=(now-timedelta(days=lag)).strftime('%Y%m%d')
            url=ESPN+comp+'/scoreboard?'+urlencode({'dates':day,'limit':1000})
            data=get(url)
            if data is None:continue
            coverage[comp]['scoreboards_ok']+=1
            for event in data[0].get('events',[]):
                events_seen+=1
                coverage[comp]['events_seen']+=1
                c=event.get('competitions',[{}])[0]
                if c.get('status',event.get('status',{})).get('type',{}).get('completed') is not True:continue
                eid=str(event.get('id',''))
                if not eid.isdigit():continue
                listed={x.get('homeAway'):x for x in c.get('competitors',[])}
                signature=[str(listed.get(s,{}).get('score')) for s in ('home','away')]
                # An unchanged provider final score needs no repeated summary.
                # Revised scoreboards trigger fresh period verification and append
                # a distinct observation; no old result is overwritten.
                if all(v.isdigit() for v in signature) and all(
                    previous.get((s['cohort'],'espn:'+eid),{}).get('provider_final_score')==signature
                    for s in group):continue
                detail=get(ESPN+comp+'/summary?event='+eid)
                if detail is None:continue
                event_fields=[key for key in ('keyEvents','scoringPlays','commentary')
                              if isinstance(detail[0].get(key),list) and detail[0][key]]
                if event_fields:
                    append(root/'event-snapshots.jsonl',{'match_id':'espn:'+eid,
                        'competition':comp,'known_at':detail[1]['captured_at'],
                        'raw_reference':detail[1],'available_fields':event_fields,
                        'normalization_status':'RAW_SOURCE_FIELDS_ONLY',
                        'minute_coverage':'UNKNOWN_NOT_CERTIFIED',
                        'used_for_state_training':False})
                matched=False
                for scope in group:
                    try:
                        row=regulation_row(scope,event,detail[0],detail[1]['captured_at'],detail[1]);matched=True
                    except (ValueError,KeyError,TypeError,IndexError,OverflowError):continue
                    key=(row['cohort'],row['match_id'],row['home_goals'],row['away_goals'])
                    if key not in existing:
                        append(target,row);rows.append(row);existing.add(key)
                        coverage[comp]['new_rows']+=1
                if not matched:
                    quarantine+=1;coverage[comp]['quarantined']+=1
                    append(root/'quarantine.jsonl',{'competition':comp,'event_id':eid,'captured_at':stamp,'reason':'PHASE_IDENTITY_OR_90MIN_NOT_VERIFIED','raw_reference':detail[1]})
    market=get(ASIAN,{'X-AO':'1','Referer':'https://asianodds.org/'})
    if market:append(root/'market-snapshots.jsonl',{'source_url':ASIAN,'bookmaker':'BetInAsia via AsianOdds','captured_at':market[1]['captured_at'],'quote_time':None,'quote_time_status':'UNKNOWN_NOT_CAPTURE_TIME','raw_reference':market[1],'used_for_training':False})
    result={'schema':'WCPP-AUTOMATION-COLLECTION-1','captured_at':stamp,'request_count':calls,
        'successful_requests':successful,'max_requests':MAX_REQUESTS,'retry_used':retry_used,
        'events_seen':events_seen,'new_result_observations':len(rows),'quarantined':quarantine,
        'market_snapshot_saved':bool(market),'all_sources_free_public':True,
        'wallclock_limit_seconds':max_seconds,
        'budget_status':'TIME_LIMIT_REACHED' if time_limit_reached else 'REQUEST_LIMIT_REACHED' if calls>=MAX_REQUESTS else 'BOUNDED_SCAN_FINISHED',
        'coverage_complete':False,'coverage_by_competition':coverage,'new_rows':rows}
    append(root/'collection-runs.jsonl',{k:v for k,v in result.items() if k!='new_rows'})
    return result
