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


def assess(plan_record,rows):
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
    evaluated={'current':[],'candidate':[]};hits=[];selected=0
    for row in rows:
        fixture=None;predictions={}
        for role in ('current','candidate'):
            record=verify(row[role+'_record']);f=record['fixture'];p=record['payload']
            if role=='current':p=p['current_model']
            if (record['stage']!='prematch' or record['evaluation_slot']!='FIRST_PREMATCH'
                    or f['match_id']!=row['match_id'] or f['cohort']!=plan['cohort'] or f.get('stage')!=plan['stage']):
                raise ValueError('first prematch scope/phase required')
            if fixture is not None and fixture!=f:raise ValueError('same fixture and cutoff required')
            fixture=f;cutoff,kickoff=aware(f['cutoff_at']),aware(f['kickoff_at'])
            if not registered<cutoff<=aware(p['generated_at'])<=aware(record['recorded_at'])<kickoff:
                raise ValueError('preregistration, capture or freeze chronology conflict')
            if p['version']!=plan[role+'_version']:raise ValueError('locked model version required')
            artifact=(record['payload'].get('execution_audit',{}).get('current',{}).get('artifact_sha256') if role=='current' else p.get('artifact_sha256'))
            if artifact!=plan[role+'_artifact_sha256']:raise ValueError('locked model artifact required')
            if digest(p['matrix'])!=p['matrix_hash']:raise ValueError('matrix checksum mismatch')
            predictions[role]=p
        result=row['result'];public_url(result['source_url'])
        if result['match_id']!=row['match_id'] or result.get('score_basis')!='regulation_90':raise ValueError('result identity')
        if not kickoff<=aware(result['observed_at'])<=datetime.now(timezone.utc):raise ValueError('result capture')
        from datetime import timedelta
        if aware(result['observed_at'])<kickoff+timedelta(minutes=90):raise ValueError('regulation not complete')
        h,a=result['home_goals'],result['away_goals']
        if any(type(x) is not int or not 0<=x<=40 for x in (h,a)):raise ValueError('regulation score')
        actual=f'{h}-{a}';pair=[]
        for role,p in predictions.items():
            evaluated[role].append({'matrix':p['matrix'],'home_goals':h,'away_goals':a})
            pair.append(p.get('primary')==actual)
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
        def ece(report):
            return sum(b['n']*abs(b['mean_probability']-b['hit_rate']) for b in report['calibration_bins'])/report['n']
        if ece(metrics['candidate'])>ece(metrics['current'])+plan['ece_tolerance']:
            blocked.append('PRIMARY_CONFIDENCE_CALIBRATION_REGRESSION')
        for tail in metrics['current']['tail']:
            if metrics['candidate']['tail'][tail]['brier']>metrics['current']['tail'][tail]['brier']+plan['tail_brier_tolerance']:
                blocked.append('TAIL_CALIBRATION_REGRESSION:'+tail)
    # Never infer actual-line value from a score or a fixed 2.5 surrogate.
    if not re.fullmatch('[0-9a-f]{64}',plan.get('actual_line_audit_receipt_sha256') or ''):
        blocked.append('INDEPENDENT_ACTUAL_LINE_FIVE_WAY_AUDIT_REQUIRED')
    if not re.fullmatch('[0-9a-f]{64}',plan.get('asof_source_audit_receipt_sha256') or ''):
        blocked.append('INDEPENDENT_SOURCE_ASOF_AUDIT_REQUIRED')
    return {'schema':'WCPP-PROSPECTIVE-REVIEW-GATE-1','status':'BLOCKED' if blocked else 'READY_FOR_INDEPENDENT_REVIEW',
            'reasons':blocked,'paired_primary':summary,'probability_metrics':metrics,
            'automatic_promotion':False,'activation_modified':False,
            'notice':'Receipt references and supplied source facts require independent review. Passing this software gate never certifies truth or authorizes a registry switch.'}
