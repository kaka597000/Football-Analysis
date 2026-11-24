"""Research-only score tilt from lagged first-half performance, conditional on total goals."""

from collections import defaultdict, deque
from functools import lru_cache

import numpy as np
from scipy.optimize import brentq

from .market_baseline import quote_only_matrices
from .three_arm_model import score_picks, top_three_scores

MAX_BETA = .5
RIDGE = 10.
GOAL_DIFFERENCES = np.arange(-3., 4.)


def half_balance(half, full):
    if half is None or full is None or any(type(x) is not int or x < 0 for x in (*half, *full)):
        return None
    if len(half) != 2 or len(full) != 2 or any(h > f for h, f in zip(half, full)):
        return None
    return (half[0] - half[1]) - .5 * (full[0] - full[1])


class TeamHistory:
    """Remember exactly the last six observations, including incomplete match statistics."""

    def __init__(self):
        self.clubs = defaultdict(lambda: deque(maxlen=6))

    def observe(self, day, home, away, half, full):
        if home == away or any(self.clubs[t] and day <= self.clubs[t][-1][0] for t in (home, away)):
            raise ValueError('ambiguous club or unordered club match date')
        value = half_balance(half, full)
        self.clubs[home].append((day, value))
        self.clubs[away].append((day, -value if value is not None else None))

    def signal(self, day, home, away):
        if not home or not away or home == away:
            return 0., 'unresolved_club'
        means = []
        for team in (home, away):
            six = self.clubs.get(team, ())
            if len(six) != 6:
                return 0., 'less_than_six'
            if six[-1][0] >= day:
                raise ValueError('present or future match in prior feature')
            if (day - six[0][0]).days > 90:
                return 0., 'oldest_over_90_days'
            if any(value is None for _, value in six):
                return 0., 'incomplete_half_or_full'
            means.append(sum(value for _, value in six)/6.)
        return float(np.clip(means[0] - means[1], -2., 2.)), 'available'


@lru_cache(maxsize=8)
def coordinates(shape):
    home, away = np.indices(shape)
    return (home + away).ravel(), np.clip(home - away, -3, 3)


def baseline(market):
    return quote_only_matrices(market['h2h'], market['total'])[1]


def adjusted(matrix, signal, beta):
    if not np.isfinite(signal) or not -2 <= signal <= 2 or not -MAX_BETA <= beta <= MAX_BETA:
        raise ValueError('signal or coefficient outside registered bounds')
    if signal == 0 or beta == 0:
        return matrix
    totals, margin = coordinates(matrix.shape)
    weighted = matrix * np.exp(beta * signal * margin)
    original = np.bincount(totals, weights=matrix.ravel())
    tilted = np.bincount(totals, weights=weighted.ravel())
    scale = np.divide(original, tilted, out=np.ones_like(original), where=tilted>0)
    return weighted * scale[totals].reshape(matrix.shape)


def training_example(matrix, signal, score):
    """Only the observed total's conditional distribution enters the training likelihood."""
    h, a = score
    if h >= matrix.shape[0] or a >= matrix.shape[1]:
        raise ValueError('training label outside market support')
    indices = np.arange(max(0,h+a-matrix.shape[1]+1),min(h+a+1,matrix.shape[0]))
    margins = np.clip(2*indices-h-a, -3, 3)
    mass = np.bincount(margins+3, weights=matrix[indices,h+a-indices], minlength=7)
    return signal, mass / mass.sum(), int(np.clip(h-a,-3,3))


def fit(examples):
    if not examples:
        raise ValueError('no eligible training games')
    signal = np.asarray([e[0] for e in examples])
    mass = np.stack([e[1] for e in examples])
    observed = np.asarray([e[2] for e in examples])
    x = signal[:,None] * GOAL_DIFFERENCES
    def derivative(beta):
        p = mass * np.exp(beta * x)
        return float(np.sum((p*x).sum(axis=1)/p.sum(axis=1) - signal*observed) + RIDGE*beta)
    if derivative(-MAX_BETA) >= 0:
        return -MAX_BETA
    if derivative(MAX_BETA) <= 0:
        return MAX_BETA
    return float(brentq(derivative,-MAX_BETA,MAX_BETA))


def picks(matrix, line):
    values = score_picks(matrix, line=line)
    return {'first':values['primary'], 'top3':top_three_scores(matrix), 'wdl':values['wdl'],
            'total':values['total']['direction']}
