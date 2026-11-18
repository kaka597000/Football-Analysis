"""Portable, bounded execution of already locked future-validation plans.

Frozen record bytes never change. Only unfrozen receipt path metadata is resolved
inside a content-addressed private bundle. Capture assets retain their original
absolute paths and are never overwritten when a different file is present.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import time
from urllib.request import Request, build_opener

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ..research.analysis_freeze import digest, verify, aware
from ..research_program import future_validation as fv
from ..research_program.future_identity import validate_plan_packages
from ..publication import atomic_json
from ..storage_lock import exclusive_lock
from .result_queue import ResultQueue

# Keep the existing bundle imports available to CLI and Python callers.
from .prospective_bundle import (
    SCHEMA, UCL_SCHEMA, check_bundle, export_bundle, export_ucl_bundle, hydrate,
    load_receipt, read, resolve, restore_domestic, restore_ucl, sha,
)


def _recover_registration(state, protocol, fixtures):
    """Recover a durable plan when a process stopped before its receipt file."""
    identities = [{k:f[k] for k in ('match_id','cohort','stage','kickoff_at','home','away')} for f in fixtures]
    expected = protocol['cohorts'][fixtures[0]['cohort']]
    matches = []
    for path in (state/'freezes').glob('*/000001.json'):
        record = verify(read(path)); payload = record['payload']
        if (record['track'] != 'research-preregistration'
                or payload.get('protocol_sha256') != protocol['protocol_sha256']
                or payload.get('fixtures_identity') != identities):
            continue
        if any(payload.get(k) != expected[k] for k in
               ('candidate_artifact_sha256','candidate_version','current_artifact_sha256','current_version','stage')):
            raise ValueError('recovered plan locked identity conflict')
        matches.append({'path':str(path), 'sha256':record['record_sha256'],
                        'freeze_id':record['freeze_id'], 'recorded_at':record['recorded_at'],
                        'evaluation_slot':record['evaluation_slot']})
    if len(matches)>1: raise ValueError('duplicate recovered registration')
    return matches


def _recover_package(root, manifest, path, plan, match_id, state):
    package = read(path)
    if package.get('match_id') != match_id or package.get('plan_sha256') != plan['record_sha256']:
        raise ValueError('recovered package plan binding')
    for role in ('current','candidate'):
        package[role+'_receipt'], _ = load_receipt(root, manifest, package[role+'_receipt'])
    # Reuse all existing first-slot, matrix, artifact and chronology checks.
    validate_plan_packages(plan,[package])
    fv.review(plan,[package],[],state)
    return package


@contextmanager
def _locked_state(root, directory):
    manifest = root / 'manifest.json'
    if not manifest.is_file():
        raise FileNotFoundError(manifest)
    state = root.parent / directory
    state.mkdir(exist_ok=True)
    with exclusive_lock(state / '.cycle.lock'):
        yield state


def cycle(bundle, *, max_captures=3, max_result_requests=6, fetcher=None, now=None, max_seconds=45,
          market_dir=None, market_providers=(), market_bookmakers=()):
    root = Path(bundle).resolve()
    with _locked_state(root, 'state') as state:
        return _cycle(root, state, max_captures=max_captures, max_result_requests=max_result_requests,
                      fetcher=fetcher, now=now, max_seconds=max_seconds, market_dir=market_dir,
                      market_providers=market_providers, market_bookmakers=market_bookmakers)


def _cycle(root, state, *, max_captures=3, max_result_requests=6, fetcher=None, now=None, max_seconds=45,
           market_dir=None, market_providers=(), market_bookmakers=()):
    """Capture explicit queued targets and review every retained registered ID.

    No fixture discovery, qualitative recommendation, active-model switch or
    promotion is implied. Queue population is supplied and registered before
    capture. Source acquisition is fixed ESPN GET and bounded.
    """
    if not 0<=max_captures<=10 or not 0<=max_result_requests<=10: raise ValueError('bounded cycle required')
    if not 10 <= max_seconds <= 120: raise ValueError('cycle time budget required')
    started = time.monotonic()
    m = check_bundle(root)
    now = now or datetime.now(timezone.utc)
    plans = list(m['plans']['plans']); packages = list(m['packages'])
    extra = read(state/'state.json') if (state/'state.json').exists() else {'plans':[], 'packages':[]}
    plans += extra['plans']; packages += extra['packages']
    registered = {}
    for receipt in plans:
        _, rec = load_receipt(root, m, receipt)
        for identity in rec['payload']['fixtures_identity']:
            mid = identity['match_id']
            if mid in registered: raise ValueError('duplicate registered population')
            registered[mid] = (identity, receipt)
    captured = {p['match_id'] for p in packages}
    protected = {p['match_id'] for p in m['protected_first_slots']}
    failures, new_captures, hydration = [], [], False
    from .collector import ESPN, NoRedirect
    def bounded_fetch(url):
        if not url.startswith(ESPN): raise ValueError('fixed ESPN endpoint required')
        with build_opener(NoRedirect()).open(Request(url,headers={'User-Agent':'WCPP-research'}),timeout=8) as response:
            body=response.read(8_000_001)
            if len(body)>8_000_000: raise ValueError('response too large')
            return body
    fetcher = fetcher or bounded_fetch
    queue = sorted(m['queue'],key=lambda f:(f['kickoff_at'],f['match_id']))
    # Entire explicit new population is registered before the first inference.
    new_targets=[f for f in queue if f['match_id'] not in registered and f['match_id'] not in protected and aware(f['kickoff_at'])>now]
    if new_targets:
        restore_domestic(root, m); hydration = True
        groups={}
        for target in new_targets: groups.setdefault(target['cohort'],[]).append(target)
        # Finish registration of every supplied cohort before any inference.
        # Checkpoint each durable cohort so interruption cannot orphan a plan.
        for targets in groups.values():
            out=state/('registration-'+digest(targets)+'.json')
            if out.exists():
                value=read(out)
                if value['protocol_sha256']!=m['protocol']['protocol_sha256']:
                    raise ValueError('registration output protocol conflict')
                receipts=value['plans']
            else:
                receipts=_recover_registration(state,m['protocol'],targets)
                if not receipts: receipts=fv.register(m['protocol'],targets,out,freeze_root=state/'freezes')
            plans+=receipts;extra['plans']+=receipts
            for receipt in receipts:
                _, plan = load_receipt(root, m, receipt)
                expected=[{k:f[k]for k in ('match_id','cohort','stage','kickoff_at','home','away')}for f in targets]
                if plan['payload']['fixtures_identity']!=expected:
                    raise ValueError('registration output population conflict')
                for identity in plan['payload']['fixtures_identity']:registered[identity['match_id']]=(identity,receipt)
            atomic_json(state/'state.json',extra)
    for fixture in queue:
        mid = fixture['match_id']
        if mid in captured or mid in protected: continue
        out=state/('capture-'+mid.replace(':','_')+'.json')
        # A completed capture may precede a process stop, including a stop
        # between writing the paired receipt and checkpointing cycle state.
        # Recover it even after kickoff; never run inference for this recovery.
        if out.exists() and mid in registered:
            try:
                _, plan = load_receipt(root, m, registered[mid][1])
                package=_recover_package(root,m,out,plan,mid,state)
                packages.append(package);extra['packages'].append(package);captured.add(mid)
                atomic_json(state/'state.json',extra)
                continue
            except (ValueError,KeyError,OSError,TypeError) as exc:
                failures.append({'match_id':mid,'status':'RECOVERY_BLOCKED','error_type':type(exc).__name__})
                continue
        if aware(fixture['kickoff_at'])<=now:
            failures.append({'match_id':mid,'status':'NOT_CAPTURED_BEFORE_KICKOFF'});continue
        if len(new_captures)>=max_captures or time.monotonic()-started>max_seconds-8: break
        if not hydration:
            restore_domestic(root, m); hydration = True
        f = dict(fixture); f['cutoff_at'] = datetime.now(timezone.utc).isoformat()
        try:
            url=ESPN+f['cohort'].split(':')[0]+'/summary?event='+mid.split(':')[1]
            raw=fetcher(url);body=json.loads(raw) if isinstance(raw,bytes) else raw
            c=body['header']['competitions'][0];teams={t['homeAway']:t for t in c['competitors']}
            if (str(body['header']['id'])!=mid.split(':')[1] or aware(c['date'])!=aware(f['kickoff_at'])
                    or 'espn:'+str(teams['home']['team']['id'])!=f['home'] or 'espn:'+str(teams['away']['team']['id'])!=f['away']
                    or c['status']['type']['state']!='pre' or type(c.get('neutralSite'))is not bool):
                raise ValueError('fresh prematch identity/status/neutral required')
            capture_time=datetime.now(timezone.utc).isoformat()
            rawpath=state/'raw'/(mid.replace(':','_')+'-prematch-'+capture_time.replace(':','')+'.json');rawpath.parent.mkdir(exist_ok=True)
            rawpath.write_bytes(raw if isinstance(raw,bytes) else json.dumps(body).encode())
            f['neutral']=c['neutralSite'];f['cutoff_at']=capture_time
            f['sources']=[{'source_url':url,'query_time':capture_time,'source_tier':'L2','response_sha256':sha(rawpath),'supports_field':['identity','neutral','format']}]
            journal=out.with_name(out.name+'.capture.json')
            # Existing complete/partial captures keep their original inputs.
            # Only a new unissued forecast may attach a retained source quote.
            if market_dir is not None and not journal.exists():
                from .market_bridge import attach_total_quote
                f,_=attach_total_quote(f,market_dir,provider_order=market_providers,
                                       bookmaker_order=market_bookmakers)
            if not fv.quote_audit(f)['eligible']:f.pop('total_quote',None)
            _, plan = load_receipt(root, m, registered[mid][1])
            package = fv.capture(m['protocol'],plan,f,out,freeze_root=state/'freezes',resume=journal.exists())
            packages.append(package); extra['packages'].append(package);captured.add(mid);new_captures.append(mid)
            atomic_json(state/'state.json',extra)
        except (ValueError,KeyError,OSError,TypeError) as exc:
            failures.append({'match_id':mid,'status':'CAPTURE_BLOCKED','error_type':type(exc).__name__})
    # Persist first even if a later source or review rejects a result.
    atomic_json(state/'state.json',extra)
    results=read(state/'results.json') if (state/'results.json').exists() else []; result_calls=0
    result_ids={r['match_id'] for r in results}
    polling = ResultQueue(state)
    pending = [identity for mid,(identity,_) in registered.items()
               if mid not in result_ids and mid in captured and mid.startswith('espn:')
               and aware(identity['kickoff_at'])+timedelta(hours=2)<=now]
    for identity in polling.order(pending):
        mid = identity['match_id']
        if result_calls>=max_result_requests or time.monotonic()-started>max_seconds-8: break
        code=identity['cohort'].split(':')[0];url=ESPN+code+'/summary?event='+mid.split(':')[1]
        polling.mark(mid)
        try:
            result_calls+=1;raw=fetcher(url);body=json.loads(raw) if isinstance(raw,bytes) else raw
            record_time=datetime.now(timezone.utc).isoformat();c=body['header']['competitions'][0]
            if c['status']['type']['name']!='STATUS_FULL_TIME': continue
            ts={t['homeAway']:t for t in c['competitors']}
            if (str(body['header']['id'])!=mid.split(':')[1] or 'espn:'+str(ts['home']['team']['id'])!=identity['home']
                or 'espn:'+str(ts['away']['team']['id'])!=identity['away'] or aware(c['date'])!=aware(identity['kickoff_at'])):
                raise ValueError('result identity')
            rawpath=state/'raw'/(mid.replace(':','_')+'-'+record_time.replace(':','')+'.json');rawpath.parent.mkdir(exist_ok=True)
            rawpath.write_bytes(raw if isinstance(raw,bytes) else json.dumps(body).encode())
            result={'match_id':mid,'event_status':'FT','score_basis':'regulation_90','home_goals':int(ts['home']['score']),
                    'away_goals':int(ts['away']['score']),'observed_at':record_time,
                    'evidence':{'file':str(rawpath.relative_to(state)),'sha256':sha(rawpath),'url':url,'query_time':record_time,'identity_verified':True}}
            from ..research_program.replay import _finished_espn_label
            _finished_espn_label(dict(result,kickoff_at=identity['kickoff_at'],home=identity['home'],away=identity['away'],cohort=identity['cohort']),result['evidence'],rawpath)
            results.append(result);result_ids.add(mid)
            atomic_json(state/'results.json',results)
        except (ValueError,KeyError,OSError,TypeError) as exc:
            failures.append({'match_id':mid,'status':'RESULT_NOT_CERTIFIED','error_type':type(exc).__name__})
    atomic_json(state/'results.json',results)
    reviews=[]
    for receipt in plans:
        _, plan = load_receipt(root, m, receipt)
        ids=set(plan['payload']['eligible_match_ids']);ps=[]
        for package in packages:
            if package['match_id']in ids:
                p=dict(package)
                for role in ('current', 'candidate'):
                    p[role+'_receipt'], _ = load_receipt(root, m, p[role+'_receipt'])
                ps.append(p)
        validate_plan_packages(plan,ps)
        reviews.append(fv.review(plan,ps,[r for r in results if r['match_id']in ids],state))
    value={'schema':'WCPP-AUTOMATED-FUTURE-CYCLE-1','reviewed_at':datetime.now(timezone.utc).isoformat(),
           'bundle_sha256':m['manifest_sha256'],'population':len(registered),'captured_population':len(captured),
           'new_captures':new_captures,'result_requests':result_calls,'result_attempted_ids':polling.attempted,
           'completed':sum(r['completed']for r in reviews),
           'pending':sum(len(r['pending_results'])for r in reviews),'missing_first_pairs':sum(len(r['missing_first_pairs'])for r in reviews),
           'failures':failures,'cohort_reviews':reviews,'automatic_promotion':False,'active_model_modified':False}
    runs=state/'runs';runs.mkdir(exist_ok=True);out=runs/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.json')
    if out.exists(): raise FileExistsError(out)
    atomic_json(out,value);return value


def ucl_cycle(bundle, *, max_result_requests=2, max_seconds=20, fetcher=None, now=None):
    """Review all already captured UCL pairs; no new inference or activation."""
    if not 0<=max_result_requests<=10 or not 10<=max_seconds<=120:raise ValueError('bounded UCL review required')
    from ..research_program import ucl_refresh_review as reviewer
    from ..research_program.replay import _finished_espn_label
    from .collector import ESPN, NoRedirect
    root = Path(bundle).resolve()
    with _locked_state(root, 'ucl-state') as state:
        started = time.monotonic()
        m = check_bundle(root, schema=UCL_SCHEMA)
        now = now or datetime.now(timezone.utc)
        restore_ucl(root, m)
        _, plan = load_receipt(root, m, m['plan'])
        packages = []
        for original in m['packages']:
            p=dict(original)
            for role in ('baseline', 'refreshed'):
                p[role], _ = load_receipt(root, m, p[role])
            packages.append(p)
        # Validate frozen pairs before accepting any post-match source.
        reviewer.review(m['protocol'],plan,packages,[])
        results=read(state/'results.json')if(state/'results.json').exists()else[]
        result_ids={r['match_id']for r in results};captured={p['match_id']for p in packages};failures=[];calls=0
        def bounded_fetch(url):
            if not url.startswith(ESPN+'uefa.champions/summary?event='):raise ValueError('fixed UCL endpoint required')
            with build_opener(NoRedirect()).open(Request(url,headers={'User-Agent':'WCPP-research'}),timeout=8)as response:
                body=response.read(8_000_001)
                if len(body)>8_000_000:raise ValueError('response too large')
                return body
        fetcher=fetcher or bounded_fetch
        polling = ResultQueue(state)
        pending = [identity for identity in plan['payload']['fixtures_identity']
                   if identity['match_id'] in captured and identity['match_id'] not in result_ids
                   and aware(identity['kickoff_at'])+timedelta(hours=2)<=now]
        for identity in polling.order(pending):
            mid=identity['match_id']
            if calls>=max_result_requests or time.monotonic()-started>max_seconds-8:break
            url=ESPN+'uefa.champions/summary?event='+mid.removeprefix('espn:')
            polling.mark(mid)
            try:
                calls+=1;raw=fetcher(url);body=json.loads(raw)if isinstance(raw,bytes)else raw
                c=body['header']['competitions'][0];teams={v['homeAway']:v for v in c['competitors']}
                if c['status']['type']['name']!='STATUS_FULL_TIME':continue
                if (str(body['header']['id'])!=mid.removeprefix('espn:')or aware(c['date'])!=aware(identity['kickoff_at'])
                    or 'espn:'+str(teams['home']['team']['id'])!=identity['home']
                    or 'espn:'+str(teams['away']['team']['id'])!=identity['away']):raise ValueError('UCL result identity')
                stamp=datetime.now(timezone.utc).isoformat();path=state/'raw'/(mid.replace(':','_')+'-'+stamp.replace(':','')+'.json');path.parent.mkdir(exist_ok=True)
                path.write_bytes(raw if isinstance(raw,bytes)else json.dumps(body).encode())
                result={'match_id':mid,'event_status':'FT','score_basis':'regulation_90',
                        'home_goals':int(teams['home']['score']),'away_goals':int(teams['away']['score']),'observed_at':stamp,
                        'evidence':{'file':str(path.relative_to(state)),'sha256':sha(path),'url':url,'query_time':stamp,'identity_verified':True}}
                _finished_espn_label(dict(result,kickoff_at=identity['kickoff_at'],home=identity['home'],away=identity['away'],cohort=identity['cohort']),result['evidence'],path)
                results.append(result);result_ids.add(mid);atomic_json(state/'results.json',results)
            except(ValueError,KeyError,OSError,TypeError)as exc:failures.append({'match_id':mid,'status':'RESULT_NOT_CERTIFIED','error_type':type(exc).__name__})
        value=reviewer.review(m['protocol'],plan,packages,results,state)
        value.update(reviewed_at=datetime.now(timezone.utc).isoformat(),result_requests=calls,
                     result_attempted_ids=polling.attempted,failures=failures,
                     bundle_sha256=m['manifest_sha256'],mode='REVIEW_ONLY_NO_NEW_INFERENCE')
        runs=state/'runs';runs.mkdir(exist_ok=True)
        atomic_json(runs/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.json'),value)
        return value


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    e=sub.add_parser('export');e.add_argument('--out',required=True);e.add_argument('--protocol',required=True);e.add_argument('--plans',required=True);e.add_argument('--packages',required=True);e.add_argument('--queue');e.add_argument('--dependencies-manifest')
    c=sub.add_parser('cycle');c.add_argument('--bundle',required=True);c.add_argument('--max-captures',type=int,default=3);c.add_argument('--max-result-requests',type=int,default=6);c.add_argument('--max-seconds',type=int,default=45)
    c.add_argument('--market-dir');c.add_argument('--market-provider',action='append',default=[]);c.add_argument('--market-bookmaker',action='append',default=[])
    u=sub.add_parser('export-ucl');u.add_argument('--out',required=True);u.add_argument('--protocol',required=True);u.add_argument('--plan',required=True);u.add_argument('--packages',required=True)
    v=sub.add_parser('review-ucl');v.add_argument('--bundle',required=True);v.add_argument('--max-result-requests',type=int,default=2);v.add_argument('--max-seconds',type=int,default=20)
    a=p.parse_args()
    if a.command=='export':
        m=export_bundle(a.out,a.protocol,a.plans,a.packages,queue_path=a.queue,dependencies_manifest=a.dependencies_manifest);print(json.dumps({'objects':len(m['objects']),'manifest_sha256':m['manifest_sha256']}))
    elif a.command=='export-ucl':
        m=export_ucl_bundle(a.out,a.protocol,a.plan,a.packages);print(json.dumps({'objects':len(m['objects']),'manifest_sha256':m['manifest_sha256']}))
    elif a.command=='review-ucl':
        m=ucl_cycle(a.bundle,max_result_requests=a.max_result_requests,max_seconds=a.max_seconds);print(json.dumps({k:m[k]for k in ('population','first_paired','completed','pending_results','missing_first_pairs')}))
    else:
        m=cycle(a.bundle,max_captures=a.max_captures,max_result_requests=a.max_result_requests,max_seconds=a.max_seconds,market_dir=a.market_dir,market_providers=a.market_provider,market_bookmakers=a.market_bookmaker);print(json.dumps({k:m[k]for k in ('population','captured_population','new_captures','completed','pending','missing_first_pairs')}))

if __name__=='__main__':main()
