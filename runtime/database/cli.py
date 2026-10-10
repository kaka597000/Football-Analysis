import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
from providers import canonical, emperor, local_files, fetch, supplemental, repair_cached, resolve_cup_periods
from store import Store, now, digest
from five_dollar import fetch_japan

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['migrate','bootstrap','refresh','repair','audit','export'])
    parser.add_argument('--root',default='/workspace/WCPP')
    parser.add_argument('--db')
    parser.add_argument('--years',nargs='+',type=int)
    parser.add_argument('--workers',type=int,default=6)
    parser.add_argument('--offline',action='store_true')
    args=parser.parse_args();root=Path(args.root)
    path=Path(args.db) if args.db else root/'data/unified/WCPP_统一赛事库.sqlite'
    # One writer across manual commands and the scheduled updater.
    import fcntl
    path.parent.mkdir(parents=True,exist_ok=True)
    lock=(path.parent/'writer.lock').open('a')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise SystemExit('Another database update is running; retry after it finishes.')
    store=Store(path);started=now();run_id=digest([started,args.command])
    original=root/'packages/core-2026-09-29/WCPP_赛事数据库_统一合并版_2026-09-29.sqlite'
    before=hashlib.sha256(original.read_bytes()).hexdigest() if original.exists() else None
    logs=[]
    if args.command=='migrate':
        local_files(store,root)
        canonical(store,original)
        emperor(store,root/'data/emperor-cup/WCPP_天皇杯扩展库.sqlite')
        store.db.commit()
        print('Local evidence migrated',flush=True)
    if args.command in ('migrate','bootstrap','refresh'):
        if not args.offline:
            logs=fetch(store,root/'data/unified/raw',args.years or (list(range(2023,dt.datetime.now().year+1)) if args.command in ('migrate','bootstrap') else [dt.datetime.now().year]),args.command in ('migrate','bootstrap'),args.workers)
            logs+=supplemental(store,root/'data/unified/raw',args.years or (list(range(2023,dt.datetime.now().year+1)) if args.command in ('migrate','bootstrap') else [dt.datetime.now().year]))
            logs+=fetch_japan(store,root/'data/unified/raw')
            logs+=resolve_cup_periods(store,root/'data/unified/raw')
        store.curated_aliases();store.bridge();store.project()
    if args.command=='repair':
        repair_cached(store);store.curated_aliases();store.bridge();store.project()
    if args.command=='export':
        import sys
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'v4'))
        from competition_router import classify
        rows=[]
        for r in store.db.execute('SELECT * FROM training_90 ORDER BY date,match_id'):
            cohort=r['competition']+(':'+r['stage'] if r['stage'] and r['stage'] not in ('regular','regular-season') else '')
            evidence=list(store.db.execute('SELECT o.obs_id,o.observed_at,s.url FROM match_evidence e JOIN observations o USING(obs_id) JOIN sources s USING(source_id) WHERE e.match_id=?',(r['match_id'],)))
            times=[e['observed_at'] for e in evidence if e['observed_at']]
            from store import timestamp
            rows.append(dict(match_id='unified:'+r['match_id'],cohort=cohort,competition=r['competition'],season=r['season'],stage=r['stage'],date=r['date'],kickoff_at=r['kickoff_utc'],home=r['home_id'],away=r['away_id'],home_name=r['home'],away_name=r['away'],home_goals=r['h90'],away_goals=r['a90'],score_basis='regulation_90',first_known_at=None,observed_at=max(times,key=timestamp) if times else None,source_url=next((e['url'] for e in evidence if e['url']),None),source_observation_ids=[e['obs_id'] for e in evidence]))
            route=classify(rows[-1])
            rows[-1]['source_stage']=rows[-1]['stage']
            if route['complete']:
                stage=route['classification']['stage']
                rows[-1]['stage']=stage
                rows[-1]['cohort']=r['competition']+(':'+stage if stage not in ('regular','friendly') else '')
            rows[-1]['competition_route']=route
            rows[-1]['classification_training_eligible']=route['complete']
            rows[-1]['neutral']=None  # No neutral-venue evidence is stored in this projection.
        dest=root/'data/unified/training-90.json';dest.write_text(json.dumps(rows,ensure_ascii=False,indent=2))
    audit=store.audit();audit.update(run_id=run_id,started_at=started,finished_at=now(),database=str(path),
                                    canonical_sha256_before=before,canonical_sha256_after=hashlib.sha256(original.read_bytes()).hexdigest() if original.exists() else None,
                                    requests=len(logs),request_failures=sum('error' in x for x in logs))
    out=root/'reports/database-unification';out.mkdir(parents=True,exist_ok=True)
    (out/(run_id[:16]+'-audit.json')).write_text(json.dumps(audit,ensure_ascii=False,indent=2))
    (out/'latest-audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2))
    if logs:(out/(run_id[:16]+'-fetch-log.json')).write_text(json.dumps(logs,ensure_ascii=False,indent=2))
    coverage=[dict(r) for r in store.db.execute('SELECT * FROM coverage ORDER BY code,season')]
    (out/'coverage.json').write_text(json.dumps(coverage,ensure_ascii=False,indent=2))
    store.db.execute('INSERT INTO runs VALUES(?,?,?,?)',(run_id,started,now(),json.dumps(audit,ensure_ascii=False)));store.db.commit()
    print(json.dumps({k:v for k,v in audit.items() if k not in ['competitions']},ensure_ascii=False))
    if audit['integrity']!='ok' or audit['foreign_key_violations']:raise SystemExit(1)

if __name__=='__main__':main()
