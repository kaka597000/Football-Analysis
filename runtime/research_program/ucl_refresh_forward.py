"""First-prematch active-vs-inactive UCL refresh pairs, with immutable identities."""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from publication import atomic_json
from storage_lock import exclusive_lock
from research.analysis_freeze import save, verify, aware, digest, DEFAULT_ROOT
from research_program.future_validation import check_matrix, write_new

COHORT = 'uefa.champions:NEW_FORMAT:league_phase'
STAGE = 'NEW_FORMAT:league_phase'


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def lock(registry_path, current_path, test_path, out):
    """Lock fixed refits before identifying their future validation population."""
    paths = [Path(p).resolve() for p in (registry_path, current_path, test_path)]
    registry, current, test = map(read, paths)
    route = registry['models'][COHORT]
    for path_key, hash_key in [('artifact_path','artifact_sha256'),('test_artifact_path','test_artifact_sha256')]:
        if sha(route[path_key]) != route[hash_key]:
            raise ValueError('active artifact hash mismatch')
    for artifact in (current, test):
        lineage = artifact['refresh_experiment']
        if (artifact['cohort'] != COHORT or lineage['active'] is not False
                or lineage['status'] != 'INACTIVE_AWAITING_PROSPECTIVE_VALIDATION'
                or lineage['historical_asof_certified'] is not False
                or aware(artifact['built_at']) > datetime.now(timezone.utc)):
            raise ValueError('inactive fixed-refit identity required')
    if current['refresh_experiment'] != test['refresh_experiment']:
        raise ValueError('paired fixed-refit lineage required')
    out = Path(out).resolve()
    shadow_path = out.with_name(out.stem+'-inactive-registry.json')
    shadow = copy.deepcopy(registry)
    shadow['models'] = {COHORT: dict(route, artifact_path=str(paths[1]), artifact_sha256=sha(paths[1]),
                                    test_artifact_path=str(paths[2]), test_artifact_sha256=sha(paths[2]))}
    shadow['refresh_experiment_inactive'] = True
    shadow['notice'] = 'Local inference manifest only; never installed as the active registry.'
    write_new(shadow_path, shadow)
    tracked = {str(p): sha(p) for p in paths}
    tracked.update({route['artifact_path']:route['artifact_sha256'],
                    route['test_artifact_path']:route['test_artifact_sha256'], str(shadow_path):sha(shadow_path)})
    source_root = Path(__file__).resolve().parents[1]
    for p in [Path(__file__), source_root/'prediction_engine.py', source_root/'prediction_service.py',
              source_root/'continental/ucl_ml.py', source_root/'research/ucl_fixed_refresh.py']:
        tracked[str(p)] = sha(p)
    value = {'schema':'WCPP-UCL-REFRESH-FORWARD-1','locked_at':datetime.now(timezone.utc).isoformat(),
             'cohort':COHORT,'stage':STAGE,'active_registry':str(paths[0]),'inactive_registry':str(shadow_path),
             'fixed_refresh_id':current['refresh_experiment']['id'],'files_sha256':tracked,
             'minimum_completed_per_comparison':30,'alpha':.05,'quality_tolerances':'NO_REGRESSION',
             'comparisons':['active_current_vs_refreshed_current','active_test_vs_refreshed_test'],
             'population_rule':'ENTIRE_PREREGISTERED_UNSTARTED_UNLABELED_BATCH; FIRST_PAIRS; ALL_MISSING_RETAINED',
             'primary_rule':'INDEPENDENT_STATISTICAL_PRIMARY_NOT_COMPREHENSIVE',
             'automatic_promotion':False,'active_assets_modified':False}
    value['protocol_sha256'] = digest(value)
    write_new(out, value)
    return value


def check(protocol):
    if digest({k:v for k,v in protocol.items() if k!='protocol_sha256'}) != protocol['protocol_sha256']:
        raise ValueError('protocol checksum')
    if any(sha(path)!=expected for path,expected in protocol['files_sha256'].items()):
        raise ValueError('locked code or artifact changed')


def validate_fixture(fixture, now=None):
    now = now or datetime.now(timezone.utc)
    if (fixture.get('cohort') != COHORT or fixture.get('stage') != STAGE
            or fixture.get('competition') != 'uefa.champions'
            or fixture.get('phase') != 'PreMatch' or fixture.get('age') != 'senior'
            or fixture.get('gender') not in ('men','male') or fixture.get('identity_verified') is not True
            or type(fixture.get('neutral')) is not bool or aware(fixture['kickoff_at']) <= now
            or any(k in fixture for k in ('home_goals','away_goals','actual','result','date'))):
        raise ValueError('verified future unlabelled UCL league-phase fixture required')


def register(protocol, fixtures, out, freeze_root=DEFAULT_ROOT):
    check(protocol)
    if not fixtures:
        raise ValueError('real future population required')
    keys = []
    for f in fixtures:
        validate_fixture(f)
        keys.append((f['match_id'],f['kickoff_at'],f['home'],f['away']))
    if len({k[0] for k in keys}) != len(keys) or len({k[1:] for k in keys}) != len(keys):
        raise ValueError('duplicate future population')
    now = datetime.now(timezone.utc).isoformat()
    payload = {'protocol_sha256':protocol['protocol_sha256'],'cohort':COHORT,'stage':STAGE,
               'eligible_match_ids':[f['match_id'] for f in fixtures],
               'fixtures_identity':[{k:f[k] for k in ('match_id','cohort','stage','kickoff_at','home','away')} for f in fixtures],
               'minimum_completed_per_comparison':30,'automatic_promotion':False}
    shell = {'match_id':'ucl-refresh-plan:'+digest(payload),'cohort':COHORT,'cutoff_at':now,
             'kickoff_at':min(f['kickoff_at'] for f in fixtures)}
    receipt = save(payload,shell,stage='review',track='research-preregistration',root=freeze_root,
                   idempotency_key='ucl-refresh-plan:'+digest(payload))
    write_new(out,receipt)
    return receipt


def _capture(protocol, plan, fixture, out, freeze_root=DEFAULT_ROOT, runner=None, resume=False):
    check(protocol);plan=verify(plan)
    if plan['track']!='research-preregistration' or plan['payload']['protocol_sha256']!=protocol['protocol_sha256']:
        raise ValueError('actual registered refresh plan required')
    out=Path(out).resolve();out.parent.mkdir(parents=True,exist_ok=True);journal=out.with_name(out.name+'.capture.json')
    if resume:
        state=read(journal)
        if (state['plan_sha256']!=plan['record_sha256']
                or digest({k:v for k,v in state.items() if k!='state_sha256'})!=state['state_sha256']):
            raise ValueError('capture resume plan mismatch')
        fixture=state['fixture']
    else:
        if out.exists() or journal.exists():raise FileExistsError(out)
        validate_fixture(fixture)
        identity={k:fixture[k] for k in ('match_id','cohort','stage','kickoff_at','home','away')}
        if identity not in plan['payload']['fixtures_identity']:
            raise ValueError('fixture outside registered population')
        if not aware(plan['recorded_at'])<aware(fixture['cutoff_at'])<=datetime.now(timezone.utc):
            raise ValueError('actual cutoff after preregistration required')
        for p in Path(freeze_root).glob('*/000001.json'):
            old=verify(read(p))
            if old['match_id']==fixture['match_id'] and old['stage']=='prematch' and old['track'] in ('current-test-pair','ucl-refresh-current-test'):
                raise ValueError('existing first prediction cannot be recreated')
        if runner is None:
            from prediction_service import run
            runner=run
        baseline=runner(fixture,protocol['active_registry']);fresh=runner(fixture,protocol['inactive_registry'])
        for pair,registry_path in [(baseline,protocol['active_registry']),(fresh,protocol['inactive_registry'])]:
            route=read(registry_path)['models'][COHORT]
            for key,role,hash_key in [('current_model','current','artifact_sha256'),('test_model','test','test_artifact_sha256')]:
                check_matrix(pair[key])
                if pair[key].get('input')!=fixture or pair['execution_audit'][role]['artifact_sha256']!=route[hash_key]:
                    raise ValueError('actual pair input or locked artifact identity mismatch')
        state={'plan_sha256':plan['record_sha256'],'fixture':fixture,'baseline':baseline,'refreshed':fresh}
        state['state_sha256']=digest(state)
        atomic_json(journal,state)
    receipts={}
    for name,track in [('baseline','current-test-pair'),('refreshed','ucl-refresh-current-test')]:
        receipts[name]=save(state[name],fixture,stage='prematch',track=track,root=freeze_root,
                            idempotency_key=state['plan_sha256']+':'+name+':'+fixture['match_id'])
        if receipts[name]['evaluation_slot']!='FIRST_PREMATCH':
            raise ValueError('concurrent first forecast prevents first-pair eligibility')
    result={'match_id':fixture['match_id'],'plan_sha256':plan['record_sha256'],'protocol_sha256':protocol['protocol_sha256'],**receipts}
    write_new(out,result)
    return result


def capture(protocol, plan, fixture, out, freeze_root=DEFAULT_ROOT, runner=None, resume=False):
    out=Path(out).resolve();out.parent.mkdir(parents=True,exist_ok=True)
    with exclusive_lock(str(out)+'.lock'):
        return _capture(protocol,plan,fixture,out,freeze_root,runner,resume)


def status(plan, packages):
    plan=verify(plan);expected=set(plan['payload']['eligible_match_ids']);seen=set()
    for package in packages:
        if package['match_id'] not in expected or package['match_id'] in seen:
            raise ValueError('package outside unique registered universe')
        if package['plan_sha256']!=plan['record_sha256']:
            raise ValueError('package plan mismatch')
        records=[]
        for key,track in [('baseline','current-test-pair'),('refreshed','ucl-refresh-current-test')]:
            receipt=package[key];record=verify(read(receipt['path']))
            if (record['record_sha256']!=receipt['sha256'] or record['track']!=track
                    or record['evaluation_slot']!='FIRST_PREMATCH' or record['match_id']!=package['match_id']):
                raise ValueError('actual first-pair integrity')
            for model in ('current_model','test_model'):check_matrix(record['payload'][model])
            records.append(record)
        if records[0]['fixture']!=records[1]['fixture']:
            raise ValueError('same-cutoff comparison required')
        seen.add(package['match_id'])
    return {'schema':'WCPP-UCL-REFRESH-PENDING-1','population':len(expected),'first_paired':len(seen),
            'missing_first_pairs':sorted(expected-seen),'completed':0,'pending_results':sorted(expected),
            'minimum_completed_per_comparison':30,'status':'AWAITING_REAL_RESULTS_AND_INDEPENDENT_AUDITS',
            'automatic_promotion':False,'active_assets_modified':False,
            'notice':'This pending audit does not fetch or fabricate results; domestic dynamic-pair evidence does not validate this UCL refresh.'}


def main():
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True)
    s=sub.add_parser('lock')
    for x in ('registry','current','test','out'):s.add_argument('--'+x,required=True)
    s=sub.add_parser('register')
    for x in ('protocol','fixtures','out'):s.add_argument('--'+x,required=True)
    s=sub.add_parser('capture')
    for x in ('protocol','plan','fixture','out'):s.add_argument('--'+x,required=True)
    s.add_argument('--resume',action='store_true')
    a=p.parse_args()
    if a.command=='lock':lock(a.registry,a.current,a.test,a.out)
    elif a.command=='register':register(read(a.protocol),read(a.fixtures),a.out)
    else:capture(read(a.protocol),read(a.plan),read(a.fixture),a.out,resume=a.resume)

if __name__=='__main__':main()
