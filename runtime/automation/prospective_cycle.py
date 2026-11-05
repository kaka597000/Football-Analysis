"""Portable, bounded execution of already locked future-validation plans.

Frozen record bytes never change. Only unfrozen receipt path metadata is resolved
inside a content-addressed private bundle. Capture assets retain their original
absolute paths and are never overwritten when a different file is present.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time
from urllib.request import Request, build_opener

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ..research.analysis_freeze import digest, verify, aware
from ..research_program import future_validation as fv
from ..publication import atomic_json
from ..storage_lock import exclusive_lock

SCHEMA = 'WCPP-PORTABLE-FUTURE-BUNDLE-1'
UCL_SCHEMA = 'WCPP-PORTABLE-UCL-REVIEW-BUNDLE-1'


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _allowed_original(path):
    p = Path(path)
    if not p.is_absolute() or '..' in p.parts:
        raise ValueError('absolute normalized original path required')
    allowed = ('/workspace/WCPP/', '/workspace/recovered-workspace-',
               '/workspace/scope18-audit-', '/workspace/Football-Analysis/')
    if not str(p).startswith(allowed):
        raise ValueError('original capture path outside allowlist')
    return p


def export_bundle(dest, protocol_path, plans_path, packages_path, *, queue_path=None,
                  dependencies_manifest=None, freeze_root=fv.DEFAULT_ROOT):
    dest = Path(dest).resolve()
    if dest.exists():
        raise FileExistsError(dest)
    protocol = read(protocol_path); fv.check_protocol(protocol)
    plans = read(plans_path); packages = read(packages_path)
    if plans['protocol_sha256'] != protocol['protocol_sha256']:
        raise ValueError('plan protocol mismatch')
    dest.mkdir(parents=True)
    (dest/'objects').mkdir()
    objects, aliases, assets = {}, {}, []

    def add(path, asset=False):
        path = Path(path); body = path.read_bytes(); checksum = hashlib.sha256(body).hexdigest()
        obj = dest/'objects'/checksum
        if not obj.exists(): obj.write_bytes(body)
        objects[checksum] = {'path':'objects/'+checksum, 'bytes':len(body), 'sha256':checksum}
        for alias in (str(path), str(path.resolve())):
            if alias in aliases and aliases[alias] != checksum: raise ValueError('alias content conflict')
            aliases[alias] = checksum
            if asset:
                _allowed_original(alias)
                if alias not in assets: assets.append(alias)
        return checksum

    add(protocol['registry_path'], True)
    registry = read(protocol['registry_path'])
    for cohort, entry in protocol['cohorts'].items():
        add(entry['candidate_path'], True)
        for key in ('artifact_path','test_artifact_path'):
            path = registry['models'][cohort].get(key)
            if path: add(path, True)
    for path in protocol['source_files_sha256']: add(path, True)
    if dependencies_manifest:
        dm = read(dependencies_manifest); base = Path(dependencies_manifest).parent.parent
        for entry in dm['entries']:
            # The original paths must exist and match. Never trust an archive's
            # arbitrary restoration destination or silently replace active bytes.
            original = Path(entry['original_path'])
            if not original.exists() or sha(original) != entry['sha256']:
                raise ValueError('dependency original unavailable or mismatched')
            add(original, entry['kind'] not in ('ACTUAL_FREEZE', 'SOURCE_EVIDENCE'))
    for receipt in plans['plans']:
        rec = verify(read(receipt['path']))
        if rec['record_sha256'] != receipt['sha256']: raise ValueError('plan receipt checksum')
        add(receipt['path'])
    for package in packages:
        for role in ('current','candidate'):
            receipt = package[role+'_receipt']; rec = verify(read(receipt['path']))
            if rec['record_sha256'] != receipt['sha256']: raise ValueError('pair receipt checksum')
            add(receipt['path'])
    # Preserve all known first slots, even when they lack a paired candidate.
    protected = []
    for path in Path(freeze_root).glob('*/000001.json'):
        rec = verify(read(path))
        if rec['stage'] == 'prematch' and rec['track'] in ('current-test-pair','challenger-independent'):
            protected.append({'match_id':rec['match_id'], 'record_sha256':rec['record_sha256']})
    queue = read(queue_path) if queue_path else []
    if not isinstance(queue,list): raise ValueError('fixture queue must be a list')
    manifest = {'schema':SCHEMA, 'protocol':protocol, 'plans':plans, 'packages':packages,
                'queue':queue, 'objects':objects, 'aliases':aliases, 'capture_assets':assets,
                'protected_first_slots':protected, 'created_at':datetime.now(timezone.utc).isoformat(),
                'automatic_promotion':False, 'historical_asof_certified':False}
    manifest['manifest_sha256'] = digest(manifest)
    atomic_json(dest/'manifest.json',manifest)
    return manifest


def check_bundle(root, *, schema=SCHEMA):
    root = Path(root).resolve(); m = read(root/'manifest.json')
    if m.get('schema') != schema or digest({k:v for k,v in m.items() if k!='manifest_sha256'}) != m['manifest_sha256']:
        raise ValueError('bundle manifest integrity')
    for checksum, entry in m['objects'].items():
        p = (root/entry['path']).resolve()
        if not p.is_relative_to(root) or p.is_symlink() or p.stat().st_size != entry['bytes'] or sha(p) != checksum:
            raise ValueError('bundle object integrity')
    return m


def resolve(root, manifest, original):
    checksum = manifest['aliases'].get(str(original))
    if checksum is None:
        # Records created by this cycle use a private /future/state path; their
        # receipt metadata is relocatable, while the actual record stays intact.
        state_name=manifest.get('state_directory','state')
        if state_name not in ('state','ucl-state'):raise ValueError('unsupported private state directory')
        marker = '/future/'+state_name+'/'
        if marker in str(original):
            suffix = str(original).split(marker,1)[1]
            p = (Path(root).parent/state_name/suffix).resolve()
            if p.is_relative_to((Path(root).parent/state_name).resolve()) and p.is_file(): return p
        raise ValueError('reference missing from bundle')
    return Path(root)/manifest['objects'][checksum]['path']


def hydrate(root):
    """Prepare old absolute capture paths; existing mismatched bytes are fatal."""
    root = Path(root).resolve(); m = check_bundle(root)
    for alias, expected in m['protocol']['source_files_sha256'].items():
        if '/runtime/' not in alias: raise ValueError('unsupported executable source path')
        live = Path(__file__).resolve().parents[2]/'runtime'/alias.split('/runtime/',1)[1]
        if not live.is_file() or sha(live)!=expected: raise ValueError('live executable differs from protocol')
    for alias in m['capture_assets']:
        dest = _allowed_original(alias); src = resolve(root,m,alias)
        if dest.exists():
            if not dest.is_file() or sha(dest) != sha(src): raise ValueError('capture destination conflict')
            continue
        dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(src,dest)
    fv.check_protocol(m['protocol'])
    return m


def _receipt(root, m, value):
    out = dict(value); out['path'] = str(resolve(root,m,value['path']))
    rec = verify(read(out['path']))
    if rec['record_sha256'] != value['sha256']: raise ValueError('resolved receipt checksum')
    return out


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
        package[role+'_receipt'] = _receipt(root,manifest,package[role+'_receipt'])
    # Reuse all existing first-slot, matrix, artifact and chronology checks.
    fv.review(plan,[package],[],state)
    return package


def cycle(bundle, *, max_captures=3, max_result_requests=6, fetcher=None, now=None, max_seconds=45):
    root=Path(bundle).resolve();check_bundle(root)
    state=root.parent/'state';state.mkdir(exist_ok=True)
    with exclusive_lock(state/'.cycle.lock'):
        return _cycle(root,max_captures=max_captures,max_result_requests=max_result_requests,
                      fetcher=fetcher,now=now,max_seconds=max_seconds)


def _cycle(bundle, *, max_captures=3, max_result_requests=6, fetcher=None, now=None, max_seconds=45):
    """Capture explicit queued targets and review every retained registered ID.

    No fixture discovery, qualitative recommendation, active-model switch or
    promotion is implied. Queue population is supplied and registered before
    capture. Source acquisition is fixed ESPN GET and bounded.
    """
    if not 0<=max_captures<=10 or not 0<=max_result_requests<=10: raise ValueError('bounded cycle required')
    if not 10 <= max_seconds <= 120: raise ValueError('cycle time budget required')
    started = time.monotonic()
    root = Path(bundle).resolve(); m = check_bundle(root)
    state = root.parent/'state'; state.mkdir(exist_ok=True)
    now = now or datetime.now(timezone.utc)
    plans = list(m['plans']['plans']); packages = list(m['packages'])
    extra = read(state/'state.json') if (state/'state.json').exists() else {'plans':[], 'packages':[]}
    plans += extra['plans']; packages += extra['packages']
    registered = {}
    for receipt in plans:
        rec = verify(read(_receipt(root,m,receipt)['path']))
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
        hydrate(root);hydration=True
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
                plan=verify(read(_receipt(root,m,receipt)['path']))
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
                plan=verify(read(_receipt(root,m,registered[mid][1])['path']))
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
            hydrate(root); hydration = True
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
            if not fv.quote_audit(f)['eligible']:f.pop('total_quote',None)
            plan = verify(read(_receipt(root,m,registered[mid][1])['path']))
            package = fv.capture(m['protocol'],plan,f,out,freeze_root=state/'freezes',resume=out.with_name(out.name+'.capture.json').exists())
            packages.append(package); extra['packages'].append(package);captured.add(mid);new_captures.append(mid)
            atomic_json(state/'state.json',extra)
        except (ValueError,KeyError,OSError,TypeError) as exc:
            failures.append({'match_id':mid,'status':'CAPTURE_BLOCKED','error_type':type(exc).__name__})
    # Persist first even if a later source or review rejects a result.
    atomic_json(state/'state.json',extra)
    results=read(state/'results.json') if (state/'results.json').exists() else []; result_calls=0
    result_ids={r['match_id'] for r in results}
    for mid,(identity,_) in sorted(registered.items()):
        if mid in result_ids or mid not in captured or aware(identity['kickoff_at'])+timedelta(hours=2)>now: continue
        if result_calls>=max_result_requests or time.monotonic()-started>max_seconds-8: break
        if not mid.startswith('espn:'): continue
        code=identity['cohort'].split(':')[0];url=ESPN+code+'/summary?event='+mid.split(':')[1]
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
        plan=verify(read(_receipt(root,m,receipt)['path']));ids=set(plan['payload']['eligible_match_ids']);ps=[]
        for package in packages:
            if package['match_id']in ids:
                p=dict(package)
                for role in ('current','candidate'):p[role+'_receipt']=_receipt(root,m,p[role+'_receipt'])
                ps.append(p)
        reviews.append(fv.review(plan,ps,[r for r in results if r['match_id']in ids],state))
    value={'schema':'WCPP-AUTOMATED-FUTURE-CYCLE-1','reviewed_at':datetime.now(timezone.utc).isoformat(),
           'bundle_sha256':m['manifest_sha256'],'population':len(registered),'captured_population':len(captured),
           'new_captures':new_captures,'result_requests':result_calls,'completed':sum(r['completed']for r in reviews),
           'pending':sum(len(r['pending_results'])for r in reviews),'missing_first_pairs':sum(len(r['missing_first_pairs'])for r in reviews),
           'failures':failures,'cohort_reviews':reviews,'automatic_promotion':False,'active_model_modified':False}
    runs=state/'runs';runs.mkdir(exist_ok=True);out=runs/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.json')
    if out.exists(): raise FileExistsError(out)
    atomic_json(out,value);return value


def export_ucl_bundle(dest, protocol_path, plan_path, packages_path):
    """Archive locked UCL pairs for review; never change the protocol bytes."""
    from ..research_program import ucl_refresh_forward as forward
    from ..research_program import ucl_refresh_review as reviewer
    protocol=read(protocol_path);forward.check(protocol)
    plan_receipt=read(plan_path);plan=verify(read(plan_receipt['path']))
    if plan['record_sha256']!=plan_receipt['sha256']:raise ValueError('UCL plan receipt binding')
    packages=read(packages_path)
    reviewer.review(protocol,plan,packages,[])
    dest=Path(dest).resolve()
    if dest.exists():raise FileExistsError(dest)
    dest.mkdir(parents=True);(dest/'objects').mkdir()
    objects={};aliases={};assets=[]
    def add(path,asset=False):
        path=Path(path);body=path.read_bytes();checksum=hashlib.sha256(body).hexdigest()
        obj=dest/'objects'/checksum
        if not obj.exists():obj.write_bytes(body)
        objects[checksum]={'path':'objects/'+checksum,'bytes':len(body),'sha256':checksum}
        for alias in (str(path),str(path.resolve())):
            if alias in aliases and aliases[alias]!=checksum:raise ValueError('UCL alias conflict')
            aliases[alias]=checksum
            if asset:
                _allowed_original(alias)
                if alias not in assets:assets.append(alias)
    for path in protocol['files_sha256']:add(path,True)
    add(plan_receipt['path'])
    for package in packages:
        for role in ('baseline','refreshed'):add(package[role]['path'])
    m={'schema':UCL_SCHEMA,'protocol':protocol,'plan':plan_receipt,'packages':packages,
       'objects':objects,'aliases':aliases,'capture_assets':assets,'state_directory':'ucl-state',
       'review_source_sha256':sha(reviewer.__file__),'automatic_promotion':False,
       'created_at':datetime.now(timezone.utc).isoformat()}
    m['manifest_sha256']=digest(m);atomic_json(dest/'manifest.json',m);return m


def _hydrate_ucl(root, manifest):
    from ..research_program import ucl_refresh_review as reviewer
    if sha(reviewer.__file__)!=manifest['review_source_sha256']:
        raise ValueError('live UCL review source changed')
    for alias,expected in manifest['protocol']['files_sha256'].items():
        if alias.startswith('/workspace/Football-Analysis/runtime/'):
            live=Path(__file__).resolve().parents[2]/'runtime'/alias.split('/runtime/',1)[1]
            if not live.is_file()or sha(live)!=expected:raise ValueError('live UCL source differs from protocol')
    for alias in manifest['capture_assets']:
        dest=_allowed_original(alias);src=resolve(root,manifest,alias)
        if dest.exists():
            if not dest.is_file()or sha(dest)!=sha(src):raise ValueError('UCL destination conflict')
        else:
            dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dest)


def ucl_cycle(bundle, *, max_result_requests=2, max_seconds=20, fetcher=None, now=None):
    """Review all already captured UCL pairs; no new inference or activation."""
    if not 0<=max_result_requests<=10 or not 10<=max_seconds<=120:raise ValueError('bounded UCL review required')
    from ..research_program import ucl_refresh_review as reviewer
    from ..research_program.replay import _finished_espn_label
    from .collector import ESPN, NoRedirect
    root=Path(bundle).resolve();m=check_bundle(root,schema=UCL_SCHEMA)
    state=root.parent/'ucl-state';state.mkdir(exist_ok=True)
    with exclusive_lock(state/'.cycle.lock'):
        started=time.monotonic();now=now or datetime.now(timezone.utc);_hydrate_ucl(root,m)
        plan=verify(read(_receipt(root,m,m['plan'])['path']));packages=[]
        for original in m['packages']:
            p=dict(original)
            for role in ('baseline','refreshed'):p[role]=_receipt(root,m,p[role])
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
        for identity in plan['payload']['fixtures_identity']:
            mid=identity['match_id']
            if mid not in captured or mid in result_ids or aware(identity['kickoff_at'])+timedelta(hours=2)>now:continue
            if calls>=max_result_requests or time.monotonic()-started>max_seconds-8:break
            url=ESPN+'uefa.champions/summary?event='+mid.removeprefix('espn:')
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
        value.update(reviewed_at=datetime.now(timezone.utc).isoformat(),result_requests=calls,failures=failures,
                     bundle_sha256=m['manifest_sha256'],mode='REVIEW_ONLY_NO_NEW_INFERENCE')
        runs=state/'runs';runs.mkdir(exist_ok=True)
        atomic_json(runs/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.json'),value)
        return value


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    e=sub.add_parser('export');e.add_argument('--out',required=True);e.add_argument('--protocol',required=True);e.add_argument('--plans',required=True);e.add_argument('--packages',required=True);e.add_argument('--queue');e.add_argument('--dependencies-manifest')
    c=sub.add_parser('cycle');c.add_argument('--bundle',required=True);c.add_argument('--max-captures',type=int,default=3);c.add_argument('--max-result-requests',type=int,default=6);c.add_argument('--max-seconds',type=int,default=45)
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
        m=cycle(a.bundle,max_captures=a.max_captures,max_result_requests=a.max_result_requests,max_seconds=a.max_seconds);print(json.dumps({k:m[k]for k in ('population','captured_population','new_captures','completed','pending','missing_first_pairs')}))

if __name__=='__main__':main()
