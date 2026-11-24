"""Calibrate a single score on each signed goal-difference diagonal."""
from functools import lru_cache

import numpy as np
from scipy.optimize import brentq

from research_program.three_arm_model import _matrix


@lru_cache(maxsize=16)
def geometry(shape):
    home, away = np.indices(shape)
    return home - away + shape[1] - 1, np.minimum(home, away) == 1


def conditional_mass(matrix):
    m = _matrix(matrix)
    difference, common_one = geometry(m.shape)
    size = sum(m.shape) - 1
    mass = np.bincount(difference.ravel(), weights=m.ravel(), minlength=size)
    one = np.bincount(difference[common_one], weights=m[common_one], minlength=size)
    return np.divide(one, mass, out=np.zeros_like(one), where=mass > 0)


def predict(matrix, beta):
    """Use all possible margins at inference. No actual margin or result input."""
    m = _matrix(matrix)
    if not np.isfinite(beta):
        raise ValueError("finite signed-margin coefficient required")
    if beta == 0:
        return m.copy()
    difference, common_one = geometry(m.shape)
    addition = np.expm1(beta)
    denominator = 1.0 + addition * conditional_mass(m)
    return m * np.where(common_one, 1.0 + addition, 1.0) / denominator[difference]


def fit(probabilities, indicators):
    p, y = np.asarray(probabilities, dtype=float), np.asarray(indicators, dtype=float)
    if (not p.size or p.shape != y.shape or not np.all((p >= 0) & (p <= 1))
            or not np.all((y == 0) | (y == 1))
            or np.any(((p == 0) & (y == 1)) | ((p == 1) & (y == 0)))):
        raise ValueError("training outcomes must have baseline support")

    def gradient(beta):
        ratio = np.exp(beta)
        calibrated = ratio * p / (1.0 + (ratio - 1.0) * p)
        return float(np.sum(calibrated - y) + 5.0*beta)

    bound = float(np.log(3.))
    if gradient(-bound) >= 0:
        return -bound
    if gradient(bound) <= 0:
        return bound
    return float(brentq(gradient, -bound, bound))
