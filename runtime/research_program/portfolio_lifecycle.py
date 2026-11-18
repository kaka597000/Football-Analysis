"""Close registered candidate rounds without modifying forecasts or activation.

Readers reuse the original freeze, identity and source-certified result checks.
Each experiment is assessed separately; different cutoffs are never pooled.
"""
import argparse
import hashlib
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path

import numpy as np
from scipy.stats import binomtest
from . import three_arm_review as review
from research.analysis_freeze import verify

EXPERIMENTS = {
    'three-arm': ('workspace-updates/20261118-three-arm/batch', 'current'),
    'sequence': ('workspace-updates/20261118-sequence/forward', 'market_baseline'),
    'transformer': ('workspace-updates/20261118-transformer/forward', 'market_baseline'),
}


def decide(rows, names, baseline, *, alpha=.05):
    """Rows include every registered match, even unavailable forecasts/results."""
    if not rows or baseline not in names or len(set(names)) != len(names):
        raise ValueError('unique arms, baseline and registered population required')
    if len({r['match_id'] for r in rows}) != len(rows):
        raise ValueError('duplicate registered match')
    population = len(rows)
    pending = sum(r['outcomes'] is None for r in rows)
    decisions = {}
    for name in names:
        if name == baseline:
            continue
        paired = [r for r in rows if r['outcomes'] is not None
                  and name in r['outcomes'] and baseline in r['outcomes']]
        gained = sum(r['outcomes'][name]['score_top1'] and not r['outcomes'][baseline]['score_top1'] for r in paired)
        lost = sum(r['outcomes'][baseline]['score_top1'] and not r['outcomes'][name]['score_top1'] for r in paired)
        pvalue = binomtest(gained, gained + lost, .5, alternative='greater').pvalue if gained + lost else 1.
        quality = {key: float(np.mean([r['outcomes'][name][key] - r['outcomes'][baseline][key] for r in paired]))
                   if paired else None for key in ('score_log_loss', 'wdl_brier')}
        days = len({r['matchday'] for r in paired})
        if pending:
            status = 'PENDING_ORIGINAL_VALIDATION'
        elif len(paired) != population:
            status = 'CLOSED_INCOMPLETE_COVERAGE'
        elif gained <= lost:
            status = 'CLOSED_NO_TOP1_ADVANTAGE'
        elif any(v > 1e-12 for v in quality.values()):
            status = 'CLOSED_QUALITY_REGRESSION'
        elif days < 2 or pvalue > alpha / (len(names) - 1):
            status = 'CLOSED_INCONCLUSIVE'
        else:
            status = 'ELIGIBLE_FOR_INDEPENDENT_REVIEW'
        decisions[name] = {'status': status, 'population': population, 'paired': len(paired),
                           'coverage': len(paired) / population, 'pending': pending, 'matchdays': days,
                           'gained': gained, 'lost': lost, 'net_top1_hits': gained - lost,
                           'paired_one_sided_pvalue': pvalue, 'family_adjusted_alpha': alpha / (len(names) - 1),
                           'quality_delta': quality,
                           'candidate_settlements': dict(Counter(r['outcomes'][name]['total_settlement'] for r in paired)),
                           'baseline_settlements': dict(Counter(r['outcomes'][baseline]['total_settlement'] for r in paired))}
    eligible = [k for k, v in decisions.items() if v['status'] == 'ELIGIBLE_FOR_INDEPENDENT_REVIEW']
    selected = min(eligible, key=lambda k: (-decisions[k]['net_top1_hits'], decisions[k]['quality_delta']['score_log_loss'], k)) if eligible else None
    for k in eligible:
        if k != selected:
            decisions[k]['status'] = 'SUPERSEDED_FOR_NEXT_REVIEW'
    return {'baseline': baseline, 'decisions': decisions, 'one_review_slot': selected,
            'baseline_remains_active': True, 'automatic_promotion': False,
            'continuation': 'pending rounds finish unchanged; closed rounds do not automatically re-enroll',
            'promotion_gate': 'existing promotion_review.assess plus independent review; not bypassed by this report'}


def snapshot(data, policy_record):
    policy_record = verify(policy_record)
    policy = policy_record['payload']
    if policy_record['track'] != 'candidate-lifecycle-policy' or policy['automatic_promotion'] is not False:
        raise ValueError('frozen lifecycle-only policy required')
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != policy['lifecycle_source_sha256']:
        raise ValueError('lifecycle implementation differs from registered policy')
    outputs = {}
    for name, (relative, baseline) in EXPERIMENTS.items():
        batch = data / relative
        if name == 'three-arm':
            plan, fixtures, captures = review._predictions(batch)
        elif name == 'sequence':
            from .sequence_forward import inspect_batch
            plan, fixtures, captures = inspect_batch(batch)
        else:
            from .transformer_forward import records
            plan, fixtures, captures = records(batch)
        if plan['record_sha256'] != policy['plan_sha256'][name]:
            raise ValueError('original plan identity changed')
        if any(review.aware(f['kickoff_at']) <= review.aware(policy_record['recorded_at']) for f in fixtures.values()):
            raise ValueError('lifecycle policy must precede every target kickoff')
        results, issues = review._results(data.resolve(), fixtures, datetime.now(timezone.utc))
        names = sorted({k for record in captures.values() for k in record['payload']['arms']})
        if names != sorted(policy['arms'][name]):
            raise ValueError('frozen arm population changed')
        rows = []
        for mid, fixture in fixtures.items():
            arms = captures[mid]['payload']['arms'] if mid in captures else {}
            outcomes = {k: review._outcome(a, results[mid]) for k, a in arms.items()} if mid in results else None
            rows.append({'match_id': mid, **review._matchday(fixture),
                         'cutoff_at': fixture['cutoff_at'], 'outcomes': outcomes,
                         'source_issues': issues.get(mid, [])})
        outputs[name] = dict(decide(rows, names, baseline, alpha=policy['alpha']),
                             plan_sha256=plan['record_sha256'], rows=rows)
    return {'schema': 'WCPP-CANDIDATE-LIFECYCLE-1', 'policy_sha256': policy_record['record_sha256'],
            'experiments': outputs, 'cross_experiment_pooling': False, 'activation_modified': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--policy', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(snapshot(args.data, json.loads(args.policy.read_text())), indent=2))
