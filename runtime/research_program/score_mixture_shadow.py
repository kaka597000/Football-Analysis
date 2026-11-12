"""Inactive, three-part chronological selection of retained Poisson mixtures.

Weights combine probability distributions, not rates. Statistical modes are
never labelled comprehensive Primary, and tail events are not betting markets.
"""
from __future__ import annotations

from collections import defaultdict
import math
import numpy as np
from scipy.special import logsumexp
from scipy.stats import poisson, skellam

BASELINE = ('goals_only', 0.)
EXPERTS = ('fresh_missingness', 'opponent_residual')
ALPHAS = (0., .25, .5, .75, 1.)
QUALITY = ('score_log_loss', 'wdl_brier')


def _rates(model):
    rates = np.asarray([model['lambda_home'], model['lambda_away']], dtype=float)
    if not np.isfinite(rates).all() or (rates <= 0).any() or (rates > 50).any():
        raise ValueError('finite positive rates <= 50 required; do not clip')
    return rates


def distribution(base, expert, alpha):
    """Infinite-support marginal probabilities; finite exact mode search.

    Beyond ceil(max lambda), both Poisson components decrease in each axis,
    so the global mixture mode is inside this rectangle. No tail renormalizing.
    """
    if not math.isfinite(alpha) or not 0 <= alpha <= 1:
        raise ValueError('alpha must be in [0, 1]')
    rates = [_rates(base), _rates(expert)]
    weights = np.asarray([1. - alpha, alpha])
    selected = [(r, float(w)) for r, w in zip(rates, weights) if w > 0]
    bounds = np.ceil(np.max([r for r, _ in selected], axis=0)).astype(int)
    matrix = sum(w * np.outer(poisson.pmf(np.arange(bounds[0] + 1), r[0]),
                              poisson.pmf(np.arange(bounds[1] + 1), r[1]))
                 for r, w in selected)
    # Stable lower-score tie breaking matches the retained Poisson convention.
    indices = np.argwhere(np.isclose(matrix, matrix.max(), rtol=1e-12, atol=0.))
    score = indices[0].astype(int).tolist()
    wdl = sum(w * np.array([skellam.sf(0, *r), skellam.pmf(0, *r),
                            skellam.cdf(-1, *r)]) for r, w in selected)
    if not np.isfinite(wdl).all() or (wdl < 0).any() or abs(wdl.sum() - 1) > 1e-10:
        raise ValueError('invalid infinite-support WDL probability')
    return {'statistical_top1': score, 'wdl_probs': wdl.tolist(),
            'wdl_pick': int(np.argmax(wdl)),
            'total_ge4_probability': float(sum(w * poisson.sf(3, r.sum()) for r, w in selected)),
            'zero_probabilities': sum(w * np.exp(-r) for r, w in selected).tolist(),
            'expected_goals': sum(w * r for r, w in selected).tolist(),
            'component_rates': [r.tolist() for r in rates], 'component_weights': weights.tolist()}


def score(prediction, actual):
    if (not isinstance(actual, (list, tuple)) or len(actual) != 2
            or any(type(v) is not int or v < 0 for v in actual)):
        raise ValueError('90 minute nonnegative integer score required')
    p = np.asarray(prediction['wdl_probs'])
    y_class = 0 if actual[0] > actual[1] else 1 if actual[0] == actual[1] else 2
    y = np.eye(3)[y_class]
    log_p = logsumexp([math.log(w) + sum(poisson.logpmf(actual, rates))
                      for rates, w in zip(prediction['component_rates'], prediction['component_weights']) if w > 0])
    return {'top1_hit': prediction['statistical_top1'] == list(actual),
            'wdl_hit': prediction['wdl_pick'] == y_class,
            'score_log_loss': float(-log_p),
            'wdl_log_loss': float(-math.log(max(p[y_class], 1e-300))),
            'wdl_brier': float(np.square(p-y).sum()),
            'wdl_rps': float(np.square(np.cumsum(p)[:-1]-np.cumsum(y)[:-1]).mean()),
            'total_ge4_brier': (prediction['total_ge4_probability'] - int(sum(actual) >= 4)) ** 2,
            'zero_brier': float(np.square(np.asarray(prediction['zero_probabilities'])
                                         - (np.asarray(actual) == 0)).mean())}


def summarize(rows):
    if not rows:
        raise ValueError('empty evaluation partition')
    n = len(rows)
    return {'n': n, 'statistical_top1_hits': sum(r['top1_hit'] for r in rows),
            'statistical_top1_rate': sum(r['top1_hit'] for r in rows) / n,
            'wdl_hits': sum(r['wdl_hit'] for r in rows),
            'wdl_rate': sum(r['wdl_hit'] for r in rows) / n,
            **{key: float(np.mean([r[key] for r in rows])) for key in
               ('score_log_loss', 'wdl_log_loss', 'wdl_brier', 'wdl_rps', 'total_ge4_brier', 'zero_brier')}}


def noninferior(candidate, baseline, tolerance=1e-12):
    if candidate['n'] != baseline['n']:
        raise ValueError('selection requires exactly matched denominators')
    return (candidate['statistical_top1_hits'] >= baseline['statistical_top1_hits']
            and candidate['wdl_hits'] >= baseline['wdl_hits']
            and all(candidate[k] <= baseline[k] + tolerance for k in QUALITY))


def select(selection_metrics):
    """Only weight-selection outcomes may be passed here; deterministic ties."""
    baseline = selection_metrics[BASELINE]
    eligible = [key for key, value in selection_metrics.items() if noninferior(value, baseline)]
    return min(eligible, key=lambda key: (-selection_metrics[key]['statistical_top1_hits'],
                selection_metrics[key]['score_log_loss'], key[1], key[0]))


def gate(selected, selected_metrics, baseline_metrics):
    """One pass; failing the gate falls back, never tries the runner-up."""
    return selected if noninferior(selected_metrics, baseline_metrics) else BASELINE


def split(rows, boundary):
    first, second = boundary['weight_selection_end_exclusive'], boundary['gate_end_exclusive']
    if first >= second:
        raise ValueError('chronological boundaries are not increasing')
    keys = [(r['cohort'], r['competition'], r['stage']) for r in rows]
    if len(set(keys)) != 1:
        raise ValueError('exactly one independent cohort/competition/stage required')
    ids = [r['match_id'] for r in rows]
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate match identity')
    return ([r for r in rows if r['date'] < first],
            [r for r in rows if first <= r['date'] < second],
            [r for r in rows if r['date'] >= second])


def paired(records, candidate='gated_candidate', repetitions=2000, seed=20261112):
    """Date-cluster interval, conditional on this fixed development protocol."""
    groups = defaultdict(lambda: np.zeros(3, dtype=float))
    for row in records:
        b, c = row['metrics']['baseline'], row['metrics'][candidate]
        groups[row['date']] += [int(c['top1_hit']) - int(b['top1_hit']),
                                int(c['wdl_hit']) - int(b['wdl_hit']), 1]
    values = np.array(list(groups.values()))
    if not len(values):
        raise ValueError('no paired diagnostic records')
    rng = np.random.default_rng(seed)
    samples = np.array([values[rng.integers(0, len(values), len(values))].sum(axis=0)
                        for _ in range(repetitions)])
    totals = values.sum(axis=0)
    return {'n': int(totals[2]), 'date_clusters': len(values),
            'statistical_top1_net_hits': int(totals[0]), 'wdl_net_hits': int(totals[1]),
            'statistical_top1_net_rate95': np.quantile(samples[:, 0] / samples[:, 2], [.025, .975]).tolist(),
            'wdl_net_rate95': np.quantile(samples[:, 1] / samples[:, 2], [.025, .975]).tolist(),
            'notice': 'Reused-history diagnostic interval; not fresh holdout or future Primary evidence.'}
