"""Freeze and review a complete prospective Nations repair comparison.

Five independently identified statistical tracks; no synthetic comprehensive
Primary, missing-quote substitution, retrospective selection or model promotion.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

import numpy as np

from . import repair, safe_cli, database
from research.analysis_freeze import save, verify, digest, aware
from research_program.future_validation import quote_audit, check_matrix
from v2.model import total_settlement

TRACKS=('old_current','old_test','fixed_current','fixed_test','challenger')
TRACK='nations-repair-comparison'


def attach_total(prediction):
    audit=quote_audit(prediction['input'])
    prediction['total_quote_audit']=audit
    prediction['total']=None
    if audit['eligible']:
        matrix=check_matrix(prediction);quote=prediction['input']['total_quote']
        outcomes={side:total_settlement(matrix,quote['line'],side) for side in ('over','under')}
        def balance(side):
            p=outcomes[side]
            return p['win']+.5*p['half_win']-.5*p['half_loss']-p['loss']
        prediction['total']={'quote':quote,**outcomes,
            'reference_direction':max(outcomes,key=balance),
            'direction_basis':'unit-odds settlement balance; not betting expected value'}
    return prediction


def old_prediction(ref,fixture,role):
    artifact=repair.read(ref['path'])
    if (repair.sha(ref['path'])!=ref['sha256'] or artifact['role']!=role
            or artifact['cohort']!=fixture['cohort']
            or artifact['engine_sha256']!=repair.sha(repair.legacy.__file__)):
        raise ValueError('original artifact role/cohort/executed core mismatch')
    for key in ('dataset','protocol'):
        if repair.sha(artifact[key+'_path'])!=artifact[key+'_sha256']:
            raise ValueError('original artifact dependency changed')
    if (artifact['train_end']>=fixture['date']
            or aware(artifact['created_at'])>aware(fixture['cutoff_at'])):
        raise ValueError('original artifact availability boundary')
    m,mu,missing=repair.legacy.distribution(artifact['core'],role,fixture)
    h,a=np.indices(m.shape);primary=repair.select_score(m)
    wdl=dict(zip(repair.WDL,map(float,(m[h>a].sum(),m[h==a].sum(),m[h<a].sum()))))
    return {'version':artifact['version'],'role':'old_'+role,'input':fixture,
        'status':'ORIGINAL_CANDIDATE_REPEATED_AT_NEW_COMPARISON_NODE',
        'generated_at':repair.legacy.now(),'matrix':m.tolist(),'matrix_hash':digest(m.tolist()),
        'primary':'-'.join(map(str,primary)),'primary_probability':float(m[tuple(primary)]),
        'wdl':wdl,'wdl_reference':max(wdl,key=wdl.get),'expected_goals':mu,
        'artifact_sha256':ref['sha256'],'training_data_cutoff':artifact['train_end'],
        'unseen_teams_using_fitted_global_prior':missing,'formal_kpi_eligible':False,
        'active_route':False,'primary_scope':'STATISTICAL_REFERENCE_NOT_COMPREHENSIVE_RECOMMENDATION'}


def freeze(index_path,fixtures,out,freeze_root='/workspace/WCPP/forecasts/issued-analyses'):
    index=repair.read(index_path);protocol=repair.read(index['protocol_path'])
    if index.get('schema')!='WCPP-NATIONS-REPAIR-INDEX-1':raise ValueError('repair index required')
    if repair.sha(index['protocol_path'])!=index['protocol_sha256']:
        raise ValueError('protocol changed')
    old=repair.read(protocol['old_index'])
    if repair.sha(protocol['old_index'])!=protocol['old_index_sha256']:
        raise ValueError('original index changed')
    if not fixtures or len({f['match_id'] for f in fixtures})!=len(fixtures):
        raise ValueError('nonempty unique complete fixture population required')
    out=Path(out)
    if out.exists():raise FileExistsError(out)
    prepared=[];source_identity=repair.dependency_identity();index_sha=repair.sha(index_path)
    for original in fixtures:
        fixture={**original,'cutoff_at':repair.legacy.now()}
        safe_cli.validate_future(fixture);level=fixture['league_level']
        predictions={}
        for role in ('current','test'):
            predictions['old_'+role]=attach_total(old_prediction(old['routes'][level][role],fixture,role))
        for role in ('fixed_current','fixed_test','challenger'):
            ref=index['routes'][level][role]
            if repair.sha(ref['path'])!=ref['sha256']:raise ValueError('repair index checksum')
            prediction=repair.predict(ref['path'],fixture)
            if prediction['role']!=role:raise ValueError('repair index role mismatch')
            predictions[role]=attach_total(prediction)
        prepared.append((fixture,predictions))
    # The entire population passes validation before the first new freeze.
    for fixture,_ in prepared:safe_cli.validate_future(fixture)
    if source_identity!=repair.dependency_identity() or repair.sha(index_path)!=index_sha:
        raise ValueError('execution identity changed during preflight')
    capture_track=TRACK+':'+index_sha[:12]
    plan={'schema':'WCPP-NATIONS-REPAIR-FORWARD-1','created_at':repair.legacy.now(),
          'capture_track':capture_track,
          'index_path':str(Path(index_path).resolve()),'index_sha256':index_sha,
          'routes':index['routes'],'old_routes':old['routes'],
          'eligible_match_ids':[f['match_id'] for f,_ in prepared],
          'fixtures_identity':[f for f,_ in prepared],'tracks':list(TRACKS),
          'minimum_completed_per_level':protocol['minimum_prospective_per_level'],
          'primary_scope':'STATISTICAL_EXPERIMENT_NOT_COMPREHENSIVE_RECOMMENDATION',
          'comparison_node':'new same-input first repair comparison; original prematch records untouched',
          'source_identities':source_identity,'forward_source_sha256':repair.sha(__file__),
          'first_version_only':True,'automatic_promotion':False}
    shell={'match_id':'nations-repair-forward:'+index_sha,'cutoff_at':repair.legacy.now(),
           'kickoff_at':min((f['kickoff_at'] for f,_ in prepared),key=aware)}
    out.mkdir(parents=True,exist_ok=False)
    plan_receipt=save(plan,shell,stage='review',track='research-preregistration',root=freeze_root)
    repair.write(out/'plan.json',verify(repair.read(plan_receipt['path'])))
    repair.write(out/'plan-receipt.json',plan_receipt)
    receipts=[]
    for fixture,predictions in prepared:
        payload={'predictions':predictions,'plan_sha256':plan_receipt['sha256'],
                 'formal_kpi_eligible':False,'active_model_promotion':False}
        receipt=save(payload,fixture,stage='prematch',track=capture_track,root=freeze_root,
            idempotency_key=plan_receipt['sha256']+':'+fixture['match_id'])
        record=verify(repair.read(receipt['path']))
        repair.write(out/'freezes'/(fixture['match_id'].replace(':','-')+'.json'),record)
        receipts.append(receipt)
    repair.write(out/'receipts.json',receipts)
    return {'population':len(prepared),'frozen':len(receipts),'plan':plan_receipt,
            'quoted_population':sum(p['fixed_test']['total'] is not None for _,p in prepared),
            'automatic_promotion':False}


def identity(fixture):
    return (fixture['match_id'],fixture['cohort'],fixture['stage'],fixture['home'],fixture['away'],aware(fixture['kickoff_at']))


def validate_result(result,fixture):
    if (identity(result)!=identity(fixture) or result.get('event_status')!='FT'
            or result.get('score_basis')!='regulation_90'):
        raise ValueError('completed regulation result identity required')
    capture={'event_id':result['match_id'].removeprefix('espn:'),'source_kind':'summary',
             'source_url':result['source_url'],'raw_path':result['source_path'],
             'raw_sha256':result['source_sha256'],'query_time':result['observed_at']}
    _,facts=database.normalize_capture(capture,str(Path(result['source_path']).parent))
    if (not facts['summary_verified'] or facts['status']!='STATUS_FULL_TIME'
            or facts['home_90']!=result['home_goals'] or facts['away_90']!=result['away_goals']
            or facts['home_id']!=fixture['home'] or facts['away_id']!=fixture['away']
            or aware(facts['kickoff_at'])!=aware(fixture['kickoff_at'])
            or facts['stage'] not in (None,fixture['stage'])
            or facts['league_level'] not in (None,fixture['league_level'])):
        raise ValueError('result differs from verified raw regulation evidence')


def review(bundle,results):
    """Use canonical paired settlement; bind pending and settled rows to plan."""
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'v5'))
    from v5.paired_review import review as paired_review
    bundle=Path(bundle);plan_record=verify(repair.read(bundle/'plan.json'));plan=plan_record['payload']
    expected={f['match_id']:f for f in plan['fixtures_identity']}
    if (plan['tracks']!=list(TRACKS) or len(expected)!=len(plan['fixtures_identity'])
            or set(expected)!=set(plan['eligible_match_ids'])):
        raise ValueError('forward plan population conflict')
    records={}
    for path in sorted((bundle/'freezes').glob('*.json')):
        record=verify(repair.read(path));mid=record['match_id']
        if (mid not in expected or mid in records or record['track']!=plan.get('capture_track',TRACK)
                or record['stage']!='prematch' or record['revision']!=1
                or record['payload']['plan_sha256']!=plan_record['record_sha256']
                or identity(record['fixture'])!=identity(expected[mid])):
            raise ValueError('frozen comparison identity/version mismatch')
        if not aware(record['fixture']['cutoff_at'])<=aware(record['recorded_at'])<aware(record['fixture']['kickoff_at']):
            raise ValueError('comparison was not frozen before kickoff')
        predictions=record['payload']['predictions']
        if set(predictions)!=set(TRACKS):raise ValueError('missing comparison track')
        level=record['fixture']['league_level']
        for name,p in predictions.items():
            check_matrix(p)
            ref=(plan['old_routes'][level][name.removeprefix('old_')] if name.startswith('old_') else plan['routes'][level][name])
            if (p['input']!=record['fixture'] or p['artifact_sha256']!=ref['sha256']
                    or not aware(record['fixture']['cutoff_at'])<=aware(p['generated_at'])<=aware(record['recorded_at'])):
                raise ValueError('prediction input/artifact/time mismatch')
        records[mid]=record
    seen_results=set()
    for result in results:
        mid=result['match_id']
        if mid not in expected or mid in seen_results:raise ValueError('unknown/duplicate result')
        seen_results.add(mid)
        validate_result(result,expected[mid])
    summaries={}
    for before,after in [('old_current','fixed_current'),('old_test','fixed_test'),('fixed_test','challenger')]:
        pairs=[]
        for mid,fixture in expected.items():
            if mid in records:
                record=records[mid];predictions=record['payload']['predictions']
                pair={'match_id':mid,'fixture':record['fixture'],
                      'current_model':predictions[before],'test_model':predictions[after]}
            else:
                empty={'status':'UNAVAILABLE_CAPTURE','input':fixture,'matrix':None,
                       'primary':None,'wdl_reference':None,'total':None,'version':'UNAVAILABLE'}
                pair={'match_id':mid,'fixture':fixture,'current_model':empty,'test_model':empty}
            pairs.append(pair)
        summaries[before+'_vs_'+after]=paired_review(pairs,results)
    probability={};paired_gains={}
    for level in sorted({f['league_level'] for f in expected.values()}):
        probability[level]={};outcomes={}
        for name in TRACKS:
            rows=[];outcomes[name]=[]
            for result in results:
                mid=result['match_id']
                if expected[mid]['league_level']!=level or mid not in records:continue
                p=records[mid]['payload']['predictions'][name]
                rows.append({'matrix':p['matrix'],'home_goals':result['home_goals'],
                             'away_goals':result['away_goals'],
                             'primary_score':list(map(int,p['primary'].split('-')))})
                h,a=result['home_goals'],result['away_goals']
                actual_wdl='主胜' if h>a else '客胜' if h<a else '平局'
                outcomes[name].append({'match_id':mid,'date':aware(expected[mid]['kickoff_at']).date().isoformat(),
                    'primary_hit':p['primary']==f'{h}-{a}',
                    'metrics':{'wdl_accuracy':int(p['wdl_reference']==actual_wdl)}})
            probability[level][name]=repair.evaluate(rows) if rows else {'n':0,'status':'PENDING'}
        paired_gains[level]={}
        for before,after in [('old_current','fixed_current'),('old_test','fixed_test'),('fixed_test','challenger')]:
            paired_gains[level][before+'_vs_'+after]={**repair.paired(outcomes[before],outcomes[after]),
                'scope':'prospective same-node frozen statistical comparison; few date clusters remain descriptive'}
    return {'schema':'WCPP-NATIONS-REPAIR-FORWARD-REVIEW-1','population':len(expected),
            'frozen':len(records),'missing_capture_ids':sorted(set(expected)-set(records)),
            'comparisons':summaries,'settled':len(results),'pending':sorted(set(expected)-seen_results),
            'probability_by_level_and_track':probability,
            'paired_gains_by_level':paired_gains,
            'automatic_promotion':False,'comprehensive_primary_evaluated':False}


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    f=sub.add_parser('freeze')
    for name in ('index','fixtures','out'):f.add_argument('--'+name,required=True)
    f.add_argument('--freeze-root',default='/workspace/WCPP/forecasts/issued-analyses')
    r=sub.add_parser('review')
    for name in ('bundle','results','out'):r.add_argument('--'+name,required=True)
    args=p.parse_args()
    if args.command=='freeze':result=freeze(args.index,repair.read(args.fixtures),args.out,args.freeze_root)
    else:
        result=review(args.bundle,repair.read(args.results));repair.write(args.out,result)
    print(json.dumps({'population':result['population'],'automatic_promotion':False}))


if __name__=='__main__':main()
