"""Calibrate score probabilities without moving WDL or Asian total settlement mass."""

from functools import lru_cache

import numpy as np
from scipy.optimize import minimize

from optimization.evaluate import settle_ou
from . import all_scope_model as base

VERSION = 'WCPP-R8-CONDITIONAL-SCORE-1'
PENALTY = 8.0
CELL = (np.minimum(base.H, 5) * 6 + np.minimum(base.A, 5)).ravel()
SETTLEMENTS = ('FULL_LOSS', 'HALF_LOSS', 'PUSH', 'HALF_WIN', 'FULL_WIN')


@lru_cache(maxsize=128)
def partitions(line):
    """Use the same Asian settlement routine as the result scorer."""
    if line is None:
        return base.RESULT.ravel()
    by_total = np.array([
        SETTLEMENTS.index(settle_ou(goals, line, 'over'))
        for goals in range(2 * base.SIZE - 1)
    ])
    return (5 * base.RESULT + by_total[base.H + base.A]).ravel()


def fit(matrices, scores, lines):
    """Fit within the observed score's partition using archived pre-match priors."""
    weights = []
    labels = []
    for matrix, (home, away), line in zip(matrices, scores, lines, strict=True):
        index = home * base.SIZE + away
        group = partitions(line)
        local = group == group[index]
        mass = np.bincount(CELL[local], weights=matrix.ravel()[local], minlength=36)
        if mass[CELL[index]] <= 0:
            raise ValueError('observed calibration score has zero prior probability')
        weights.append(mass / mass.sum())
        labels.append(CELL[index])
    if not labels:
        return np.zeros(36)

    priors = np.asarray(weights)
    observed = np.bincount(labels, minlength=36)

    def objective(offsets):
        posterior = priors * np.exp(offsets)
        normalizers = posterior.sum(axis=1)
        loss = np.log(normalizers).sum() - observed @ offsets
        gradient = (posterior / normalizers[:, None]).sum(axis=0) - observed
        return loss + PENALTY / 2 * (offsets @ offsets), gradient + PENALTY * offsets

    result = minimize(objective, np.zeros(36), method='L-BFGS-B', jac=True,
                      options={'ftol': 1e-11, 'gtol': 1e-7})
    if not result.success:
        raise ValueError('conditional score fit failed: ' + str(result.message))
    return result.x


def apply(matrix, offsets, line):
    """Multiply and renormalize inside each partition, including push and half wins."""
    group = partitions(line)
    prior = np.asarray(matrix).ravel()
    tilt = prior * np.exp(np.asarray(offsets)[CELL])
    base_mass = np.bincount(group, weights=prior)
    tilt_mass = np.bincount(group, weights=tilt)
    ratios = np.divide(base_mass, tilt_mass, out=np.zeros_like(base_mass), where=tilt_mass > 0)
    return (tilt * ratios[group]).reshape(base.SIZE, base.SIZE)
