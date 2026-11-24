"""Offline ablation of one bookmaker's 1X2 margin allocation on the existing score grid."""

import numpy as np
from scipy.optimize import brentq

from .market_baseline import quote_only_matrices
from .three_arm_model import SIDES, _market_matrix, _quote_values


def price_probabilities(prices):
    odds = np.asarray([prices[side] for side in SIDES], dtype=float)
    if not np.isfinite(odds).all() or np.any(odds <= 1):
        raise ValueError("Three finite decimal odds above one required")
    inverse = 1 / odds
    proportional = inverse / inverse.sum()
    if abs(inverse.sum() - 1) < 1e-14:
        return proportional, proportional.copy()
    # At this upper bound even the largest of the three terms is at most 1/3.
    upper = np.log(3) / np.log(odds.min())
    exponent = brentq(lambda k: np.sum(inverse**k) - 1, 0, max(2, 2 * upper))
    power = inverse**exponent
    return proportional, power / power.sum()


def fit_weight(training):
    """Minimize pooled WDL log loss. Each item contains prices and the realized side."""
    support = []
    for prices, side in training:
        base, power = price_probabilities(prices)
        i = SIDES.index(side)
        support.append((base[i], power[i] - base[i]))
    if not support:
        raise ValueError("No eligible training fixtures")
    base, change = np.asarray(support).T

    def derivative(weight):
        return -np.mean(change / (base + weight * change))

    if derivative(0) >= 0:
        return 0.0
    if derivative(1) <= 0:
        return 1.0
    return float(brentq(derivative, 0, 1))


def paired_matrices(prices, total, weight):
    if not 0 <= weight <= 1:
        raise ValueError("Locked weight must be in [0,1]")
    plain, baseline = quote_only_matrices(prices, total)
    if weight == 0:
        return baseline, baseline.copy()
    base, power = price_probabilities(prices)
    _, line, over = _quote_values({"h2h": prices, "total": total}, None, proxy=True)
    target = (1 - weight) * base + weight * power
    return baseline, _market_matrix(plain, (target, line, over))
