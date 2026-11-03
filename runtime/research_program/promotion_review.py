"""Pre-review gate over first frozen future pairs; never changes activation."""
from datetime import datetime,timezone
import math
import re
import numpy as np
from scipy.stats import binomtest
from competition_scope import require
from research.analysis_freeze import verify,aware
from v2_test.data_contract import digest,public_url
from v2_test.evaluation import evaluate


def _primary_calibration(values):
    bins=[[] for _ in range(10)]
    for probability,hit in values:bins[min(int(probability*10),9)].append((probability,hit))
    return {'n':len(values),'ece':sum(len(b)*abs(np.mean([v[0] for v in b])-np.mean([v[1] for v in b])) for b in bins if b)/len(values) if values else None,
            'bins':[{'n':len(b),'probability':float(np.mean([v[0] for v in b])),'hit_rate':float(np.mean([v[1] for v in b]))} for b in bins if b]}


def _audit_receipts(plan_record,rows,receipts):
    expected=digest(sorted(plan_record['payload']['eligible_match_ids']))
    package_hash=digest(sorted(rows,key=lambda row:row['match_id']))
    latest=max((aware(r['result']['observed_at']) for r in rows),default=aware(plan_record['recorded_at']))
    tracks={'actual-line-validation-audit':'INDEPENDENT_ACTUAL_LINE_FIVE_WAY_AUDIT_REQUIRED',
            'asof-source-validation-audit':'INDEPENDENT_SOURCE_ASOF_AUDIT_REQUIRED'}
    valid=set()
    for receipt in receipts or []:
        receipt=verify(receipt);body=receipt['payload'];track=receipt['track']
        if track not in tracks or track in valid:raise ValueError('unique independent audit types required')
        if (receipt.get('stage')!='review' or body.get('plan_record_sha256')!=plan_record['record_sha256']
                or body.get('validation_ids_sha256')!=expected or body.get('validation_package_sha256')!=package_hash or body.get('decision')!='PASSED'
                or body.get('cohort')!=plan_record['payload']['cohort'] or body.get('stage')!=plan_record['payload']['stage']
                or not latest<=aware(receipt['recorded_at'])<=datetime.now(timezone.utc)):
            raise ValueError('post-validation audit binding or chronology conflict')
        valid.add(track)
    return [reason for track,reason in tracks.items() if track not in valid]


def assess(plan_record,rows,audit_receipts=None):
    blocked=[];summary={};metrics={}
    if plan_record is None:
        return {'status':'BLOCKED','reasons':['NO_IMMUTABLE_PREREGISTRATION'],'automatic_promotion':False}
    plan_record=verify(plan_record);plan=plan_record['payload'];registered=aware(plan_record['recorded_at'])
    require({'cohort':plan['cohort']})
    if plan_record['track']!='research-preregistration' or not plan.get('stage'):raise ValueError('explicit preregistered phase required')
    for role in ('current','candidate'):
        if not re.fullmatch('[0-9a-f]{64}',plan[role+'_artifact_sha256']):raise ValueError('locked artifact SHA256 required')
    universe=plan['eligible_match_ids']
    if len(set(universe))!=len(universe) or not universe:raise ValueError('unique full population required')
    minimum=plan['minimum_sample'];alpha=plan['alpha']
    if type(minimum) is not int or minimum<1 or type(alpha) not in (int,float) or not 0<alpha<=.05:raise ValueError('predeclared sample and significance required')
    if len(rows)<minimum:blocked.append('INSUFFICIENT_PREDECLARED_SAMPLE')
    ids=[r['match_id'] for r in rows]
    if len(set(ids))!=len(ids):raise ValueError('duplicate validation match')
    if set(ids)!=set(universe):blocked.append('INCOMPLETE_PREDECLARED_POPULATION')
    evaluated={'current':[],'candidate':[]};hits=[];selected=0;fixture_keys=set();primary_confidence={'current':[],'candidate':[]}
    for row in rows:
        fixture=None;predictions={}
        for role in ('current','candidate'):
            record=verify(row[role+'_record']);f=record['fixture'];p=record['payload']
            if role=='current':p=p['current_model']
            expected_track='current-test-pair' if role=='current' else 'challenger-independent'
            if record.get('track')!=expected_track:raise ValueError('independent frozen tracks required')
            if (record['stage']!='prematch' or record['evaluation_slot']!='FIRST_PREMATCH'
                    or f['match_id']!=row['match_id'] or f['cohort']!=plan['cohort'] or f.get('stage')!=plan['stage']):
                raise ValueError('first prematch scope/phase required')
            if fixture is not None and fixture!=f:raise ValueError('same fixture and cutoff required')
            if p.get('input')!=f:raise ValueError('actual prediction input does not match frozen fixture')
            for value in (p.get('input_sha256'),record['payload'].get('execution_audit',{}).get('fixture_sha256')):
                if value is not None and value!=digest(p['input']):raise ValueError('prediction input checksum mismatch')
            require(f)
            if f.get('identity_verified') is not True or f.get('age')!='senior' or f.get('gender') not in ('men','male'):
                raise ValueError('verified adult male identity required')
            fixture=f;cutoff,kickoff=aware(f['cutoff_at']),aware(f['kickoff_at'])
            if not registered<cutoff<=aware(p['generated_at'])<=aware(record['recorded_at'])<kickoff:
                raise ValueError('preregistration, capture or freeze chronology conflict')
            if p['version']!=plan[role+'_version']:raise ValueError('locked model version required')
            artifact=(record['payload'].get('execution_audit',{}).get('current',{}).get('artifact_sha256') if role=='current' else p.get('artifact_sha256'))
            if artifact!=plan[role+'_artifact_sha256']:raise ValueError('locked model artifact required')
            if digest(p['matrix'])!=p['matrix_hash']:raise ValueError('matrix checksum mismatch')
            predictions[role]=p
        key=(fixture['cohort'],fixture['stage'],kickoff,fixture['home'],fixture['away'])
        if key in fixture_keys:raise ValueError('duplicate fixture under different IDs')
        fixture_keys.add(key)
        result=row['result'];public_url(result['source_url'])
        if result.get('status') not in ('completed','FT') and result.get('event_status')!='FT':
            raise ValueError('explicit completed regulation result required')
        if result['match_id']!=row['match_id'] or result.get('score_basis')!='regulation_90':raise ValueError('result identity')
        if not kickoff<=aware(result['observed_at'])<=datetime.now(timezone.utc):raise ValueError('result capture')
        from datetime import timedelta
        if aware(result['observed_at'])<kickoff+timedelta(minutes=90):raise ValueError('regulation not complete')
        h,a=result['home_goals'],result['away_goals']
        if any(type(x) is not int or not 0<=x<=40 for x in (h,a)):raise ValueError('regulation score')
        actual=f'{h}-{a}';pair=[]
        for role,p in predictions.items():
            evaluation_row={'matrix':p['matrix'],'home_goals':h,'away_goals':a}
            hit=p.get('primary')==actual;pair.append(hit)
            if p.get('primary') is not None:
                matched=re.fullmatch(r'(\d{1,2})-(\d{1,2})',p['primary'])
                if not matched:raise ValueError('unique Primary required')
                ph,pa=map(int,matched.groups());matrix=np.asarray(p['matrix'],float)
                if p['primary']!=f'{ph}-{pa}':raise ValueError('canonical Primary score required')
                if matrix.ndim!=2 or ph>=matrix.shape[0] or pa>=matrix.shape[1]:raise ValueError('Primary outside support')
                probability=float(matrix[ph,pa])
                if not 0<=probability<=1:raise ValueError('Primary probability')
                if p.get('primary_probability') is not None and not math.isclose(p['primary_probability'],probability,abs_tol=1e-9):raise ValueError('Primary probability mismatch')
                primary_confidence[role].append((probability,hit))
                evaluation_row['primary_score']=[ph,pa]
            evaluated[role].append(evaluation_row)
            if role=='candidate':selected+=p.get('primary') is not None
        hits.append(pair)
    if rows:
        metrics={role:evaluate(values) for role,values in evaluated.items()}
        changed_right=sum(b and not a for a,b in hits);changed_wrong=sum(a and not b for a,b in hits)
        pvalue=binomtest(changed_right,changed_right+changed_wrong,.5,alternative='greater').pvalue if changed_right+changed_wrong else 1.
        coverage=selected/len(rows)
        summary={'n':len(rows),'coverage':coverage,'changed_right':changed_right,'changed_wrong':changed_wrong,
                 'net_primary_hits':changed_right-changed_wrong,'paired_one_sided_pvalue':pvalue}
        if changed_right<=changed_wrong or pvalue>alpha:blocked.append('PRIMARY_NET_GAIN_NOT_ESTABLISHED')
        if coverage<plan['minimum_primary_coverage']:blocked.append('PRIMARY_COVERAGE_BELOW_PLAN')
        for key in ('score_log_loss','score_brier','wdl_brier','wdl_rps'):
            tolerance=plan['quality_tolerances'][key]
            if type(tolerance) not in (int,float) or not math.isfinite(tolerance) or tolerance<0:raise ValueError('locked finite quality tolerance')
            if metrics['candidate']['metrics'][key]>metrics['current']['metrics'][key]+tolerance:blocked.append('QUALITY_REGRESSION:'+key)
        if metrics['candidate']['metrics']['wdl_accuracy']<metrics['current']['metrics']['wdl_accuracy']:
            blocked.append('WDL_DIRECTION_REGRESSION')
        calibration={role:_primary_calibration(values) for role,values in primary_confidence.items()}
        if any(v['ece'] is None for v in calibration.values()):blocked.append('PRIMARY_CALIBRATION_UNAVAILABLE')
        elif calibration['candidate']['ece']>calibration['current']['ece']+plan['ece_tolerance']:
            blocked.append('PRIMARY_CONFIDENCE_CALIBRATION_REGRESSION')
        for tail in metrics['current']['tail']:
            if metrics['candidate']['tail'][tail]['brier']>metrics['current']['tail'][tail]['brier']+plan['tail_brier_tolerance']:
                blocked.append('TAIL_CALIBRATION_REGRESSION:'+tail)
    blocked.extend(_audit_receipts(plan_record,rows,audit_receipts))
    return {'schema':'WCPP-PROSPECTIVE-REVIEW-GATE-1','status':'BLOCKED' if blocked else 'READY_FOR_INDEPENDENT_REVIEW',
            'reasons':blocked,'paired_primary':summary,'probability_metrics':metrics,
            'actual_primary_calibration':{role:_primary_calibration(values) for role,values in primary_confidence.items()},
            'automatic_promotion':False,'activation_modified':False,
            'notice':'Receipt references and supplied source facts require independent review. Passing this software gate never certifies truth or authorizes a registry switch.'}
