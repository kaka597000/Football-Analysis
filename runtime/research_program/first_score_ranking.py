"""Estimate how often an already chosen first score is right, using prematch inputs."""
from datetime import date
import math

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit, logit

from .paired_kpi_inference import SCORE


VERSION = 'R16-R7-first-score-seat-order-1'
FEATURES = ('intercept', 'other_two_mass', 'implied_wdl_probability', 'draw_first', 'r6_agrees', 'market_agrees')
PENALTY = 16.


def _probability(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError('finite probability in [0, 1] required')
    return float(value)


def _agreement(primary, other):
    if other is None:
        return 0.
    if not isinstance(other, str) or not SCORE.fullmatch(other):
        raise ValueError('invalid reference first score')
    return float(primary == other) - .5


def inputs(primary, p_first, p_three, wdl, r6_first, market_first):
    """Only information present in both the historical and live first-issue schemas."""
    if not isinstance(primary, str) or not SCORE.fullmatch(primary):
        raise ValueError('invalid candidate first score')
    first, three = map(_probability, (p_first, p_three))
    weights = list(map(_probability, wdl))
    if first > three + 1e-10 or len(weights) != 3 or not math.isclose(sum(weights), 1., abs_tol=1e-7):
        raise ValueError('invalid first, trio or WDL marginals')
    home, away = map(int, primary.split('-'))
    side = 0 if home > away else 1 if home == away else 2
    features = np.array([1., (three - first - .20) / .15, (weights[side] - .40) / .25,
                         float(home == away) - .5, _agreement(primary, r6_first), _agreement(primary, market_first)])
    return dict(p_first=first, features=np.clip(features, -2., 2.).tolist())


def from_first(forecasts, quality):
    primary = forecasts['candidate']['primary']
    other = [(forecasts.get(arm) or {}).get('primary') for arm in ('r6', 'market')]
    return inputs(primary, quality['p_first'], quality['p_three'], quality['wdl'], *other)


def fit(rows):
    """Each row contains one prematch design and its observed 0/1 first-score label."""
    n = len(rows)
    if any(type(hit) is not bool for _, hit in rows):
        raise ValueError('training needs observed first-score hits')
    y = np.asarray([hit for _, hit in rows], dtype=float)
    beta = np.zeros(len(FEATURES))
    status = 'FITTED'
    if n < 50 or len(set(y)) < 2:
        status = 'INSUFFICIENT_PRIOR_FIXTURES_OR_CLASSES'
    else:
        offsets = logit(np.clip([v['p_first'] for v, _ in rows], 1e-6, 1-1e-6))
        matrix = np.array([v['features'] for v, _ in rows], dtype=float)

        def objective(weights):
            logits = offsets + matrix @ weights
            loss = np.sum(np.logaddexp(0., logits) - y * logits) + .5 * PENALTY * (weights @ weights)
            grad = matrix.T @ (expit(logits) - y) + PENALTY * weights
            return loss, grad

        result = minimize(objective, beta, jac=True, method='L-BFGS-B', bounds=[(-2., 2.)] * len(beta),
                          options=dict(ftol=1e-14, gtol=1e-9, maxiter=400))
        if not result.success or not np.isfinite(result.x).all():
            raise ValueError('first-score calibration failed: ' + str(result.message))
        beta = result.x
    return dict(version=VERSION, features=list(FEATURES), coefficients=beta.tolist(), penalty=PENALTY,
                status=status, training_rows=n, training_hits=int(y.sum()))


def predict(design, recipe):
    if recipe['version'] != VERSION or recipe['features'] != list(FEATURES):
        raise ValueError('first-score calibration feature order differs')
    offset = logit(np.clip(design['p_first'], 1e-6, 1-1e-6))
    return float(expit(offset + np.dot(design['features'], recipe['coefficients'])))


def select(fixtures, rule):
    """Fix seats using only identifiers and the named probability, in source-ID tie order."""
    if rule not in ('raw', 'calibrated') or len({v['id'] for v in fixtures}) != len(fixtures):
        raise ValueError('unique fixtures and preregistered ranking rule required')
    return [v['id'] for v in sorted(fixtures, key=lambda v: (-_probability(v[rule]), v['id']))[:3]]


def day(value):
    normalized = date.fromisoformat(value).isoformat()
    if normalized != value:
        raise ValueError('expected a canonical source calendar date')
    return normalized
