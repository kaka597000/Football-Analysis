"""Paired retained xG experiment audit; no refitting, clipping or row deletion."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import numpy as np


def read(path):
    rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line]
    result = {r['match_id']: r for r in rows}
    if len(result) != len(rows):
        raise ValueError('duplicate match identity')
    return result


def calibration(probabilities, outcomes):
    p, y = np.asarray(probabilities), np.asarray(outcomes)
    bins = []
    for low in np.arange(0, 1, .1):
        mask = (p >= low) & ((p < low + .1) if low < .9 else (p <= 1))
        if mask.any():
            bins.append({'lower': float(low), 'n': int(mask.sum()),
                         'predicted': float(p[mask].mean()), 'observed': float(y[mask].mean())})
    return {'mean_probability': float(p.mean()), 'observed_rate': float(y.mean()),
            'brier': float(np.mean((p-y)**2)), 'bins': bins}


def summarize(rows, arm):
    models = [r['models'][arm] for r in rows]
    scores = [r['actual_score'] for r in rows]
    one = [m['statistical_top1'] == [1, 1] for m in models]
    rates = [max(m['lambda_home'], m['lambda_away']) for m in models]
    result = {'n': len(rows), 'top1_hits': sum(m['top1_hit'] for m in models),
              'one_one_picks': sum(one),
              'one_one_hits': sum(p and s == [1, 1] for p, s in zip(one, scores)),
              'lambda_gt10': sum(v > 10 for v in rates), 'maximum_lambda': max(rates),
              'zero': calibration([m[k] for m in models for k in ('home_zero_probability', 'away_zero_probability')],
                                  [v == 0 for s in scores for v in s]),
              'total_ge4': calibration([m['total_ge4_probability'] for m in models],
                                      [sum(s) >= 4 for s in scores])}
    for key in ('score_log_loss', 'wdl_log_loss', 'wdl_brier', 'wdl_rps'):
        result[key] = float(np.mean([m[key] for m in models]))
    return result


def audit(original, native):
    if set(original) != set(native):
        raise ValueError('paired population mismatch')
    keys = sorted(original); old = [original[k] for k in keys]; new = [native[k] for k in keys]
    for a, b in zip(old, new):
        for key in ('date', 'cohort', 'stage', 'actual_score', 'evaluation_cutoff', 'model_id'):
            if a[key] != b[key]:
                raise ValueError('paired identity/time/label mismatch: ' + key)
        for arm in ('goals_only', 'original_xg'):
            if a['models'][arm] != b['models'][arm]:
                raise ValueError('unchanged control differs')
    arms = {}
    for arm in old[0]['models']:
        dates = defaultdict(lambda: [0, 0])
        for a, b in zip(old, new):
            dates[a['date']][0] += int(b['models'][arm]['top1_hit']) - int(a['models'][arm]['top1_hit'])
            dates[a['date']][1] += 1
        values = np.array(list(dates.values())); rng = np.random.default_rng(20261112)
        boot = []
        for _ in range(2000):
            sample = values[rng.integers(0, len(values), len(values))].sum(axis=0)
            boot.append(sample[0] / sample[1])
        arms[arm] = {'old': summarize(old, arm), 'native': summarize(new, arm),
                     'net_hits': int(values[:, 0].sum()),
                     'date_cluster_bootstrap95_rate': np.quantile(boot, [.025, .975]).tolist()}
    cohorts = {}
    for cohort in sorted({r['cohort'] for r in old}):
        cohorts[cohort] = {arm: {'old': summarize([r for r in old if r['cohort'] == cohort], arm),
                               'native': summarize([r for r in new if r['cohort'] == cohort], arm)}
                           for arm in arms}
    return {'n': len(keys), 'matched_ids': keys, 'arms': arms, 'cohorts': cohorts,
            'outliers_removed': 0, 'controls_exactly_equal': True,
            'primary': {'status': 'NOT_AVAILABLE', 'reason': 'retained experiment has statistical modes, no independently frozen comprehensive selections'},
            'historical_asof_certified': False, 'new_unseen_holdout': False,
            'active_modified': False, 'promotion': False}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--original', required=True); p.add_argument('--native', required=True)
    p.add_argument('--out', required=True); a = p.parse_args()
    result = audit(read(a.original), read(a.native))
    result['inputs'] = {k: {'path': v, 'sha256': hashlib.sha256(Path(v).read_bytes()).hexdigest()}
                        for k, v in [('original', a.original), ('native', a.native)]}
    with Path(a.out).open('x') as f:
        json.dump(result, f, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({'n': result['n'], 'net_hits': {k: v['net_hits'] for k, v in result['arms'].items()}}))
