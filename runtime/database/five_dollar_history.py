"""Pinned, resumable seven-league evidence harvest and append-only database import."""
import datetime as dt
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'v4'))
from provider_feed import scrub
from registry import LEAGUES, normalize_name, season_for
from store import Store, now, digest

TARGETS = {
    'eng.1': (4160026622, 'England Premier League', 'GB-ENG'),
    'esp.1': (4212821298, 'Spain La Liga', 'ES'),
    'ger.1': (686337048, 'Germany Bundesliga I', 'DE'),
    'ita.1': (3405541143, 'Italy Serie A', 'IT'),
    'fra.1': (3614399544, 'France Ligue 1', 'FR'),
    'jpn.1': (3836571083, 'Japan J-League', 'JP'),
    'jpn.2': (3492701972, 'Japan J2-League', 'JP'),
}


class Client:
    def __init__(self, directory):
        self.directory = Path(directory); self.directory.mkdir(parents=True, exist_ok=True)
        self.key = os.environ.get('WCPP_5DOLLAR_API_KEY')
        if not self.key: raise ValueError('ENV_BINDING_MISSING')
        self.last_request = 0.

    def get(self, name, path):
        dest = self.directory/(name+'.json')
        if dest.exists(): return json.loads(dest.read_text())
        for attempt in range(4):
            time.sleep(max(0, 3.2-(time.monotonic()-self.last_request)))
            self.last_request = time.monotonic()
            wrapper = {'url':'https://api.5dollarfootballapi.com/v1'+path, 'query_time':now()}
            try:
                req = Request(wrapper['url'], headers={'Authorization':'Bearer '+self.key, 'User-Agent':'WCPP-history-research'})
                try:
                    with urlopen(req, timeout=30) as r: body=r.read(); status=r.status; headers=r.headers
                except HTTPError as exc: body=exc.read(); status=exc.code; headers=exc.headers
                wrapper['http_status']=status
                if status == 429 and attempt < 3:
                    try: delay=max(3.2,float(headers.get('Retry-After','60')))
                    except ValueError: delay=60.
                    print(json.dumps({'rate_limited':True,'retry_after_seconds':delay}),flush=True)
                    while delay>0:
                        step=min(30.,delay);time.sleep(step);delay-=step
                    continue
                data=scrub(json.loads(body),self.key)
                wrapper['response']=data
                wrapper['rate_limit_remaining']=headers.get('X-RateLimit-Remaining')
                if status >= 500 and attempt < 3: continue
                safe=json.dumps(wrapper,ensure_ascii=False,indent=2,allow_nan=False)
                if self.key in safe: raise ValueError('unsafe credential echo')
                with dest.open('x') as f: f.write(safe+'\n')
                return wrapper
            except (URLError, TimeoutError, OSError):
                if attempt == 3: raise
        raise ValueError('retry exhausted')


def harvest(directory,lookback_days=None):
    directory=Path(directory);client=Client(directory)
    state_path=directory/'harvest-state.json'
    if lookback_days is not None and (type(lookback_days) is not int or lookback_days<1):raise ValueError('positive lookback days required')
    end=int(dt.datetime.now(dt.timezone.utc).timestamp())
    state=json.loads(state_path.read_text()) if state_path.exists() else {
        'started_at':now(),'start_time':end-lookback_days*86400 if lookback_days else 1388534400,
        'end_time':end,'lookback_days':lookback_days,'leagues':{}}
    if lookback_days is not None and state.get('lookback_days')!=lookback_days:raise ValueError('resume window mismatch')
    status=client.get('harvest-account','/status')
    if status['http_status']!=200 or status['response'].get('success')!=1:
        raise ValueError('account verification failed')
    state['plan']=status['response']['data']['plan']
    for code,(lid,name,country) in TARGETS.items():
        ledger={'league_id':lid,'name':name,'pages':[],'complete':False,'unique_rows':0}
        seen=set();previous_time=None
        for page in range(1,1001):
            q=urlencode(dict(start_time=state['start_time'],end_time=state['end_time'],status='all',
                             per_page=50,page=page,order='asc',include='events,stats'))
            capture=client.get(code+'-page-'+str(page),f'/leagues/{lid}/fixtures?'+q)
            data=capture['response']
            if capture['http_status']!=200 or data.get('success')!=1:
                ledger['error']=data.get('error');break
            rows=data.get('data')
            pagination=data.get('pagination') or {}
            if not isinstance(rows,list) or pagination.get('page')!=page or type(pagination.get('has_more')) is not bool:
                raise ValueError('untrusted pagination for '+code)
            for row in rows:
                if row.get('league',{}).get('id')!=lid or row['id'] in seen:
                    raise ValueError('league mismatch or duplicate paged fixture')
                kickoff=dt.datetime.fromisoformat(row['kickoff_utc'])
                if kickoff.tzinfo is None or not state['start_time']<=kickoff.timestamp()<state['end_time']:
                    raise ValueError('fixture outside pinned window')
                if previous_time is not None and kickoff.timestamp()<previous_time:
                    raise ValueError('fixture order changed')
                previous_time=kickoff.timestamp();seen.add(row['id'])
            ledger['pages'].append(code+'-page-'+str(page)+'.json')
            ledger['unique_rows']=len(seen)
            print(json.dumps({'league':code,'page':page,'rows':len(rows),'total':len(seen),
                              'has_more':pagination['has_more']}),flush=True)
            if not pagination['has_more']:
                ledger['complete']=True;break
            if not rows: raise ValueError('empty nonterminal page')
        state['leagues'][code]=ledger
        state_path.write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n')
        if ledger['complete'] and ledger['unique_rows']:
            last=json.loads((directory/ledger['pages'][-1]).read_text())['response']['data'][-1]
            fixture_id=last['id']
            for label,path in [('odds',f'/fixtures/{fixture_id}/odds'),('standings',f'/standings?league={lid}')]:
                cap=client.get(code+'-'+label+'-check',path)
                ledger[label+'_check']={'http':cap['http_status'],'success':cap['response'].get('success')==1,
                                       'sample_fixture_id':fixture_id if label=='odds' else None}
            state_path.write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n')
    state['finished_at']=now();state_path.write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n')
    return state


def team_id(store,scope,team,sid):
    pid=str(team['id']);name=team['name']
    if not name or type(team['id']) is not int: raise ValueError('invalid provider team')
    saved=store.db.execute('SELECT team_id FROM provider_team_ids WHERE provider=? AND provider_id=?',('5dollar',pid)).fetchone()
    if saved:return store.resolve(saved[0])
    native='5dollar:'+pid
    if store.db.execute('SELECT 1 FROM teams WHERE team_id=?',(native,)).fetchone():tid=store.resolve(native)
    else:
        row=store.db.execute('SELECT team_id FROM aliases WHERE scope=? AND norm=?',(scope,normalize_name(name))).fetchone()
        ambiguous=store.db.execute('SELECT 1 FROM ambiguous_aliases WHERE scope=? AND norm=?',(scope,normalize_name(name))).fetchone()
        tid=store.resolve(row[0]) if row and not ambiguous else native
        store.db.execute('INSERT OR IGNORE INTO teams VALUES(?,?,?)',(tid,scope,name))
        if not row and not ambiguous:
            store.db.execute('INSERT OR IGNORE INTO aliases VALUES(?,?,?,?,?)',(scope,normalize_name(name),name,tid,'5Dollar explicit team identity'))
    store.db.execute('INSERT INTO provider_team_ids VALUES(?,?,?,?,?)',('5dollar',pid,tid,sid,'existing provider identity or unique exact scope/name alias; no fuzzy/score matching'))
    return tid


def ingest_page(store,path,code,league_id):
    capture=json.loads(Path(path).read_text())
    if capture['http_status']!=200 or capture['response'].get('success')!=1:raise ValueError('failed capture')
    sid=store.source(path,'5dollar',capture['url'],capture['query_time']);counts={'rows':0,'certified':0,'unknown_scope':0}
    store.db.execute('CREATE TABLE IF NOT EXISTS api_fixture_payloads(source_id TEXT REFERENCES sources,provider TEXT,fixture_id TEXT,declared_competition TEXT,observed_at TEXT,raw_json TEXT,PRIMARY KEY(source_id,provider,fixture_id))')
    store.db.execute('CREATE INDEX IF NOT EXISTS api_fixture_latest ON api_fixture_payloads(provider,fixture_id,observed_at)')
    for raw in capture['response']['data']:
        # A richer payload must survive even when the normalized score/identity is unchanged.
        store.db.execute('INSERT OR IGNORE INTO api_fixture_payloads VALUES(?,?,?,?,?,?)',
                         (sid,'5dollar',str(raw.get('id')),code,capture['query_time'],json.dumps(raw,ensure_ascii=False)))
        try:
            if raw['league']['id']!=league_id:raise ValueError('wrong league')
            dtm=dt.datetime.fromisoformat(raw['kickoff_utc'])
            if dtm.tzinfo is None:raise ValueError('timezone missing')
            date=dtm.astimezone(dt.timezone.utc).date().isoformat()
            scope=LEAGUES[code][1];teams=[team_id(store,scope,raw['teams'][s],sid) for s in ('home','away')]
            if teams[0]==teams[1]:raise ValueError('same team fixture')
            goals=raw.get('goals') or {};h,a=goals.get('home'),goals.get('away')
            finished=raw.get('status')=='finished' and raw.get('status_code')=='full'
            final_periods=[e.get('score') for e in (raw.get('events') or [])
                           if e.get('type')=='period_score' and e.get('period')=='second_half' and e.get('score')]
            internal_conflict=bool(finished and final_periods and
                                   (final_periods[-1].get('home'),final_periods[-1].get('away'))!=(h,a))
            regular=not (code.startswith('jpn.') and '2026-01-01'<=date<'2026-07-01')
            stage='regular' if regular else 'unknown'
            certified=finished and regular and not internal_conflict and all(type(v) is int and 0<=v<=30 for v in (h,a))
            counts['rows']+=1;counts['certified']+=certified;counts['unknown_scope']+=finished and not certified
            if not regular:
                # This feed's generic J-League id includes the 2026 transition.
                # Keep the full payload in the database, without creating phantom regular-league matches.
                store.issue('API_TRANSITION_CLASSIFICATION_REQUIRED',sid,raw['id'],
                            {'competition':code,'date':date,'reason':'2026 Japan transition needs separate fixture/format proof','raw':raw})
                continue
            store.add(sid,dict(source_key='5dollar:'+str(raw['id']),competition=code,season=season_for(code,date),
                date=date,kickoff_utc=raw['kickoff_utc'],home_id=teams[0],away_id=teams[1],orientation='home_away',
                status='unknown' if internal_conflict else 'completed' if finished else raw.get('status','unknown'),h90=h if certified else None,a90=a if certified else None,
                quality='single_source' if certified else 'regulation_unresolved' if finished else 'unverified',
                score_scope='regulation_90' if certified else 'unknown',stage=stage,provider='5dollar',
                observed_at=capture['query_time'],first_known_at=None,raw=raw))
            if finished and not certified:
                store.issue('API_REGULATION_UNRESOLVED',sid,raw['id'],{'competition':code,'date':date,'reason':'Japan 2026 transition or invalid goals; no assumed regulation score'})
            if internal_conflict:
                store.issue('API_SCORE_INTERNAL_CONFLICT',sid,raw['id'],{'goals':goals,'final_period_score':final_periods[-1],
                            'reason':'Feed finished goals disagree with its full-time period score; normalized outcome kept unknown'})
        except (KeyError,ValueError,TypeError) as exc:
            store.issue('INVALID_5DOLLAR_FIXTURE',sid,raw.get('id'),{'reason':str(exc)})
    store.db.commit();return counts


def bridge_provider_ids(store):
    """Two-date opponent/orientation proof; never use outcomes or fuzzy names."""
    for _ in range(6):
        buckets={}
        for r in store.db.execute("SELECT competition,date,home_id,away_id,provider FROM observations WHERE orientation='home_away'"):
            if r['competition'] not in TARGETS:continue
            h,a=store.resolve(r['home_id']),store.resolve(r['away_id'])
            for side,known,unknown in [('home',a,h),('away',h,a)]:
                buckets.setdefault((r['competition'],r['date'],side,known),set()).add((unknown,r['provider']))
        proofs={}
        for (code,date,side,known),values in buckets.items():
            ids={x[0] for x in values}
            api=[x for x in ids if x.startswith('5dollar:')]
            existing=[x for x in ids if x.startswith('espn:')]
            if len(ids)==2 and len(api)==len(existing)==1 and len({x[1] for x in values})>=2:
                proofs.setdefault((api[0],existing[0]),set()).add(date)
        options={}
        for (old,new),dates in proofs.items():
            if len(dates)>=2:options.setdefault(old,[]).append((new,dates))
        changed=0
        for old,targets in options.items():
            if len(targets)!=1:
                store.issue('AMBIGUOUS_5DOLLAR_ID_BRIDGE',None,old,[t[0] for t in targets]);continue
            new,dates=targets[0]
            store.merge(old,new,json.dumps({'rule':'5dollar_espn_two_fixture_opponent_orientation',
                                            'dates':sorted(dates),'no_scores_used':True}));changed+=1
        if not changed:break
    store.db.commit()


def import_history(database,directory):
    import fcntl,sqlite3
    database=Path(database);directory=Path(directory)
    if not database.is_file():raise ValueError('existing unified database required')
    lock=(database.parent/'writer.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    backup=directory/'database-before.sqlite'
    if backup.exists():raise ValueError('use a fresh import directory; previous database backup preserved')
    with sqlite3.connect(database) as source,sqlite3.connect(backup) as target:source.backup(target)
    store=Store(database);before=store.audit()
    before_ids={r[0] for r in store.db.execute('SELECT match_id FROM matches')}
    store.db.execute('CREATE TABLE IF NOT EXISTS provider_team_ids(provider TEXT,provider_id TEXT,team_id TEXT REFERENCES teams,source_id TEXT REFERENCES sources,evidence TEXT,PRIMARY KEY(provider,provider_id))')
    state=json.loads((directory/'harvest-state.json').read_text());counts={}
    for code,ledger in state['leagues'].items():
        counts[code]={'rows':0,'certified':0,'unknown_scope':0}
        for name in ledger['pages']:
            stats=ingest_page(store,directory/name,code,ledger['league_id'])
            for k,v in stats.items():counts[code][k]+=v
        season_rows=store.db.execute("SELECT season,count(DISTINCT source_key) FROM observations WHERE competition=? AND provider='5dollar' AND status='completed' GROUP BY season",(code,)).fetchall()
        for season,n in season_rows:
            store.db.execute('INSERT OR REPLACE INTO coverage VALUES(?,?,?,?,?,?,?)',(code,season,'5dollar',n,None,
                'PLAN_WINDOW_PAGINATED' if ledger['complete'] else 'PARTIAL_FETCH',
                'Current Community history restricted to last 12 months; page completion is not complete season/2014 history certification'))
    store.db.execute('CREATE TABLE IF NOT EXISTS provider_payloads(source_id TEXT PRIMARY KEY REFERENCES sources,object_type TEXT,object_key TEXT,observed_at TEXT,payload_json TEXT)')
    for path in list(directory.glob('*-odds-check.json'))+list(directory.glob('*-standings-check.json')):
        capture=json.loads(path.read_text());sid=store.source(path,'5dollar',capture['url'],capture['query_time'])
        obj=capture['response'].get('data') or {}
        store.db.execute('INSERT OR IGNORE INTO provider_payloads VALUES(?,?,?,?,?)',
                         (sid,'odds' if '-odds-' in path.name else 'standings',str(obj.get('fixture_id') or obj.get('league_id')),
                          capture['query_time'],json.dumps(obj,ensure_ascii=False)))
    store.db.commit();store.curated_aliases();store.bridge();bridge_provider_ids(store);store.bridge();store.project();after=store.audit()
    new_ids=[r[0] for r in store.db.execute('SELECT match_id FROM matches') if r[0] not in before_ids]
    report={'database':str(database),'backup':str(backup),'before':before,'after':after,'import_counts':counts,
            'new_projected_match_ids':new_ids,'formal_kpi_added':0,'first_known_at_backfilled':False}
    (directory/'import-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    run_id=digest(['5dollar-seven-league-import',state['started_at']])
    store.db.execute('INSERT INTO runs VALUES(?,?,?,?)',(run_id,state['started_at'],now(),json.dumps(report,ensure_ascii=False)))
    store.db.commit()
    store.db.close();lock.close()
    if after['integrity']!='ok' or after['foreign_key_violations']:raise ValueError('database integrity failed')
    return report


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('command',choices=('harvest','import'))
    p.add_argument('--directory',required=True);p.add_argument('--database',default='/workspace/WCPP/data/unified/WCPP_统一赛事库.sqlite')
    p.add_argument('--lookback-days',type=int)
    a=p.parse_args()
    if a.command=='harvest':value=harvest(a.directory,a.lookback_days);print(json.dumps({c:v['unique_rows'] for c,v in value['leagues'].items()}))
    else:
        value=import_history(a.database,a.directory)
        print(json.dumps({'before_matches':value['before']['matches'],'after_matches':value['after']['matches'],'integrity':value['after']['integrity']}))
