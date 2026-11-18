"""Read-only inventory of existing experiments; no new arms, fitting or promotion."""
import argparse
from collections import defaultdict
import gzip
import hashlib
import json
from pathlib import Path

from research.analysis_freeze import verify


def audit(data):
    experiments = []
    cutoffs = defaultdict(dict)
    for name, folder in [('three-arm', 'batch'), ('sequence', 'forward'), ('transformer', 'forward')]:
        root = data / ('workspace-updates/20261118-' + name) / folder
        plan = verify(json.loads((root / 'plan.json').read_text()))
        fixtures = plan['payload']['fixtures']
        records = [verify(json.loads(p.read_text())) for p in sorted((root / 'freezes').glob('*.json'))]
        disagreements = defaultdict(int)
        for record in records:
            f = record['fixture']
            cutoffs[f['match_id']][name] = f['cutoff_at']
            arms = record['payload']['arms']
            baseline = {'three-arm':'current', 'sequence':'market_baseline', 'transformer':'market_baseline'}[name]
            if baseline not in arms:
                raise ValueError('missing fixed baseline: ' + name)
            for arm, forecast in arms.items():
                if arm != baseline:
                    disagreements[arm] += forecast['primary'] != arms[baseline]['primary']
        experiments.append({'id': name, 'plan_sha256': plan['record_sha256'],
                            'population': len(fixtures), 'frozen': len(records),
                            'top1_disagreements_with_baseline': dict(disagreements),
                            'decision': 'complete_existing_frozen_validation'})
    history = data / 'workspace-updates/20261118-transformer/transfer/europe-2627-predictions.jsonl.gz'
    rows = [json.loads(line) for line in gzip.open(history, 'rt')]
    if len({r['match_id'] for r in rows}) != len(rows):
        raise ValueError('duplicate historical match')
    summaries = {}
    for arm in ('baseline', 'lightgbm', 'lstm', 'transformer'):
        hits = gained = lost = 0
        brier = 0.
        for row in rows:
            h, a = row['actual']
            actual = 'home' if h > a else 'away' if h < a else 'draw'
            f, base = row['forecasts'][arm], row['forecasts']['baseline']
            right, base_right = f['primary'] == f'{h}-{a}', base['primary'] == f'{h}-{a}'
            hits += right
            gained += right and not base_right
            lost += base_right and not right
            brier += sum((p - (side == actual)) ** 2 for side, p in f['wdl_probabilities'].items())
        summaries[arm] = {'top1_hits': hits, 'population': len(rows), 'gained_vs_baseline': gained,
                          'lost_vs_baseline': lost, 'wdl_brier': brier / len(rows),
                          'stored_score_log_loss_mean': sum(r['outcomes'][arm]['score_log_loss'] for r in rows) / len(rows)}
    return {'schema': 'WCPP-CONSOLIDATION-AUDIT-1', 'frozen_experiments': experiments,
            'cross_experiment_cutoffs': dict(cutoffs),
            'same_cutoff_across_experiments': bool(cutoffs) and all(len(set(c.values())) == 1 for c in cutoffs.values()),
            'cross_experiment_pooled_comparison_allowed': False,
            'historical_evidence': {'kind': 'previously_inspected_development_evidence_not_future_acceptance',
                                    'path': history.relative_to(data).as_posix(),
                                    'sha256': hashlib.sha256(history.read_bytes()).hexdigest(), 'arms': summaries},
            'priority': 'reuse_existing_market_plus_lightgbm_component; league_calibration_optional_off',
            'new_arms_created': 0, 'automatic_promotion': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.data), ensure_ascii=False, indent=2))
