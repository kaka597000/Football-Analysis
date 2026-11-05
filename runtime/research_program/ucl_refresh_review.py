"""Review real first UCL refresh pairs without altering their frozen records.

This reports the two preregistered comparisons independently. It never activates
an artifact; independent source/market audit remains necessary for promotion.
"""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from scipy.stats import binomtest

from research.analysis_freeze import aware, digest, verify
from research_program import ucl_refresh_forward as forward
from research_program.future_validation import check_matrix, quote_audit, write_new
from research_program.replay import _finished_espn_label
from research_program.promotion_review import _primary_calibration
from v2_test.data_contract import public_url
from v2_test.evaluation import evaluate

ROLES = ('active_current', 'refreshed_current', 'active_test', 'refreshed_test')
COMPARISONS = {'active_current_vs_refreshed_current': ('active_current', 'refreshed_current'),
               'active_test_vs_refreshed_test': ('active_test', 'refreshed_test')}


def _packages(protocol, plan_record, packages):
    plan = plan_record['payload']; expected = set(plan['eligible_match_ids']); found = {}
    identities = plan['fixtures_identity']
    for package in packages:
        mid = package['match_id']
        if (mid in found or mid not in expected or package['plan_sha256'] != plan_record['record_sha256']
                or package['protocol_sha256'] != protocol['protocol_sha256']):
            raise ValueError('unique registered protocol-bound pair required')
        records = {}; fixture = None; predictions = {}
        for pair_key, track, registry_key in [('baseline', 'current-test-pair', 'active_registry'),
                                              ('refreshed', 'ucl-refresh-current-test', 'inactive_registry')]:
            receipt = package[pair_key]; record = verify(forward.read(receipt['path']))
            if (record['record_sha256'] != receipt['sha256'] or record['match_id'] != mid
                    or record['stage'] != 'prematch' or record['track'] != track
                    or record['evaluation_slot'] != 'FIRST_PREMATCH'):
                raise ValueError('actual first-prematch frozen pair required')
            f = record['fixture']
            identity = {k:f[k] for k in ('match_id','cohort','stage','kickoff_at','home','away')}
            if identity not in identities or (fixture is not None and f != fixture):
                raise ValueError('registered same-fixture same-cutoff comparison required')
            if (f['cohort'] != forward.COHORT or f['stage'] != forward.STAGE
                    or f.get('competition') != 'uefa.champions' or f.get('identity_verified') is not True
                    or type(f.get('neutral')) is not bool or f.get('phase') != 'PreMatch'
                    or f.get('age') != 'senior' or f.get('gender') not in ('men','male')):
                raise ValueError('verified UCL league-phase scope required')
            route = forward.read(protocol[registry_key])['models'][forward.COHORT]
            for key, engine_role, hash_key in [('current_model','current','artifact_sha256'),
                                                ('test_model','test','test_artifact_sha256')]:
                p = record['payload'][key]; check_matrix(p)
                if (p.get('input') != f or record['payload']['execution_audit'][engine_role]['artifact_sha256'] != route[hash_key]
                        or any(v is not None and v != digest(f) for v in
                               (p.get('input_sha256'), record['payload'].get('execution_audit',{}).get('fixture_sha256')))):
                    raise ValueError('locked actual model/input identity required')
                if not (aware(plan_record['recorded_at']) < aware(f['cutoff_at']) <= aware(p['generated_at'])
                        <= aware(record['recorded_at']) < aware(f['kickoff_at'])):
                    raise ValueError('actual preregistered prematch chronology required')
                role = ('active_' if pair_key == 'baseline' else 'refreshed_') + engine_role
                predictions[role] = p
            fixture = f; records[pair_key] = record
        found[mid] = {'fixture':fixture, 'predictions':predictions, 'records':records}
    return found


def _actual(expected, pairs, results, evidence_root):
    actual = {}; seen = set(); unscored = []
    for result in results:
        mid = result['match_id']
        if mid in seen or mid not in expected:
            raise ValueError('unique registered result identity required')
        seen.add(mid)
        if result.get('event_status') != 'FT' or result.get('score_basis') != 'regulation_90':
            unscored.append({'match_id':mid,'reason':'NON_FINAL_OR_NON_REGULATION_RESULT'}); continue
        if mid not in pairs:
            unscored.append({'match_id':mid,'reason':'MISSING_FIRST_PAIR'}); continue
        f = pairs[mid]['fixture']
        if (any(type(result.get(k)) is not int or not 0 <= result[k] <= 40 for k in ('home_goals','away_goals'))
                or not aware(f['kickoff_at']) + timedelta(minutes=90) <= aware(result['observed_at']) <= datetime.now(timezone.utc)):
            raise ValueError('real completed regulation result and observation time required')
        evidence = result.get('evidence')
        if evidence_root is None or not evidence:
            raise ValueError('retained result response evidence required')
        base = Path(evidence_root).resolve(); path = (base / evidence['file']).resolve()
        public_url(evidence['url'])
        if (not path.is_relative_to(base) or forward.sha(path) != evidence['sha256']
                or evidence.get('identity_verified') is not True
                or aware(evidence['query_time']) != aware(result['observed_at'])):
            raise ValueError('result source checksum/identity/time required')
        _finished_espn_label(dict(result, kickoff_at=f['kickoff_at'],home=f['home'],away=f['away'],cohort=f['cohort']), evidence,path)
        actual[mid] = result
    return actual, unscored


def review(protocol, plan_record, packages, results, evidence_root=None):
    forward.check(protocol); plan_record = verify(plan_record); plan = plan_record['payload']
    if (plan_record['track'] != 'research-preregistration' or plan_record['stage'] != 'review'
            or plan['protocol_sha256'] != protocol['protocol_sha256']
            or plan.get('cohort') != forward.COHORT or plan.get('stage') != forward.STAGE):
        raise ValueError('actual locked UCL preregistration required')
    universe = plan['eligible_match_ids']
    if not universe or len(set(universe)) != len(universe):
        raise ValueError('unique complete preregistered population required')
    pairs = _packages(protocol, plan_record, packages)
    actual, unscored = _actual(set(universe),pairs,results,evidence_root)
    evaluations = {role:[] for role in ROLES}; denominators = {role:Counter({k:0 for k in ('settled_population','primary_n','primary_hits','wdl_n','wdl_hits','actual_line_n')}) for role in ROLES}
    confidence = {role:[] for role in ROLES}; rows = []
    for mid in universe:
        if mid not in actual: continue
        pair = pairs[mid]; f = pair['fixture']; result = actual[mid]; h,a = result['home_goals'],result['away_goals']
        wd = '主胜' if h>a else '客胜' if h<a else '平局'; values = {}; qa = quote_audit(f)
        for role,p in pair['predictions'].items():
            matrix = np.asarray(p['matrix'],float); primary = p.get('primary'); score = list(map(int,primary.split('-'))) if primary is not None else None
            hit = primary == f'{h}-{a}' if primary is not None else None
            evaluations[role].append({'matrix':matrix,'home_goals':h,'away_goals':a,'primary_score':score})
            if score is not None:confidence[role].append((float(matrix[tuple(score)]),hit))
            direction = p.get('wdl_reference'); wdl_hit = direction == wd if direction in ('主胜','平局','客胜') else None
            total = p.get('total'); settlement = None
            same_quote = total and all(total.get('quote',{}).get(k)==f.get('total_quote',{}).get(k) for k in
                                      ('match_id','bookmaker','market','line','quoted_at','query_time','over_odds','under_odds','source_url'))
            if qa['eligible'] and same_quote and total.get('reference_direction') in ('over','under'):
                from prediction_engine import legacy
                point = np.zeros((41,41)); point[h,a]=1.
                settlement = next(k for k,v in legacy.total_settlement(point,f['total_quote']['line'],total['reference_direction']).items() if np.isclose(v,1.))
            d = denominators[role]; d['settled_population']+=1; d['primary_n']+=primary is not None; d['primary_hits']+=hit is True
            d['wdl_n']+=wdl_hit is not None; d['wdl_hits']+=wdl_hit is True
            if settlement is not None:d['actual_line_n']+=1; d['actual_line_'+settlement]+=1
            values[role]={'primary':primary,'primary_hit':hit,'wdl_reference':direction,'wdl_hit':wdl_hit,'total_settlement':settlement}
        rows.append({'match_id':mid,'home_goals':h,'away_goals':a,'models':values})
    metrics = {role:evaluate(v) if v else None for role,v in evaluations.items()}
    calibration = {role:_primary_calibration(v) for role,v in confidence.items()}; comparisons = {}
    missing = sorted(set(universe)-set(pairs)); pending = sorted(set(pairs)-set(actual))
    for name,(old,new) in COMPARISONS.items():
        reasons = ['INDEPENDENT_RESULT_AND_SOURCE_ASOF_AUDIT_REQUIRED','INDEPENDENT_REAL_LINE_AUDIT_REQUIRED']
        if len(rows)<protocol['minimum_completed_per_comparison']:reasons.append('INSUFFICIENT_PREDECLARED_SAMPLE')
        if len(rows)!=len(universe):reasons.append('INCOMPLETE_PREDECLARED_POPULATION')
        gain = sum(r['models'][new]['primary_hit'] is True and r['models'][old]['primary_hit'] is not True for r in rows)
        lost = sum(r['models'][old]['primary_hit'] is True and r['models'][new]['primary_hit'] is not True for r in rows)
        pvalue = float(binomtest(gain,gain+lost,.5,alternative='greater').pvalue) if gain+lost else 1.
        if gain<=lost or pvalue>protocol['alpha']:reasons.append('PRIMARY_NET_GAIN_NOT_ESTABLISHED')
        if rows:
            if denominators[new]['primary_n']!=len(rows):reasons.append('INCOMPLETE_PRIMARY_COVERAGE')
            for metric in ('score_log_loss','score_brier','wdl_brier','wdl_rps'):
                if metrics[new]['metrics'][metric]>metrics[old]['metrics'][metric]:reasons.append('QUALITY_REGRESSION:'+metric)
            if denominators[new]['wdl_n']!=denominators[old]['wdl_n'] or denominators[new]['wdl_hits']<denominators[old]['wdl_hits']:
                reasons.append('WDL_DIRECTION_REGRESSION_OR_COVERAGE_CHANGE')
            if any(c['ece'] is None for c in (calibration[old],calibration[new])):reasons.append('PRIMARY_CALIBRATION_UNAVAILABLE')
            elif calibration[new]['ece']>calibration[old]['ece']:reasons.append('PRIMARY_CONFIDENCE_CALIBRATION_REGRESSION')
            for tail in metrics[old]['tail']:
                if metrics[new]['tail'][tail]['brier']>metrics[old]['tail'][tail]['brier']:reasons.append('TAIL_CALIBRATION_REGRESSION:'+tail)
        comparisons[name]={'n':len(rows),'changed_right':gain,'changed_wrong':lost,'net_primary_hits':gain-lost,
                           'paired_one_sided_pvalue':pvalue,'status':'BLOCKED_PENDING_INDEPENDENT_REVIEW','reasons':reasons}
    return {'schema':'WCPP-UCL-REFRESH-REVIEW-1','protocol_sha256':protocol['protocol_sha256'],'population':len(universe),
            'first_paired':len(pairs),'completed':len(rows),'pending_results':pending,'missing_first_pairs':missing,
            'unscored_observations':unscored,'independent_denominators':{k:dict(v,actual_line_five_way={name:v['actual_line_'+name] for name in ('win','half_win','push','half_loss','loss') if v['actual_line_'+name]}) for k,v in denominators.items()},
            'probability_metrics':metrics,'primary_calibration':calibration,'comparisons':comparisons,'rows':rows,
            'automatic_promotion':False,'active_assets_modified':False,
            'notice':'Real source evidence only. No single comparison or software gate changes the active registry.'}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('protocol','plan','packages','results','out'):p.add_argument('--'+name,required=True)
    p.add_argument('--evidence-root');a=p.parse_args()
    value=review(forward.read(a.protocol),forward.read(a.plan),forward.read(a.packages),forward.read(a.results),a.evidence_root)
    write_new(a.out,value)

if __name__=='__main__':main()
