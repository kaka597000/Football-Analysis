"""Prematch goal distribution obtained by integrating over unobserved halftime scores."""
from functools import lru_cache

import numpy as np

from . import all_scope_model as base


VERSION = 'R20-J1-latent-halftime-1'


def rates(matrix):
    return np.array([(matrix * base.H).sum(), (matrix * base.A).sum()])


def fit(anchors, observations):
    """Estimate halftime shares and second-half scoring rates from earlier completed matches."""
    if len(anchors) != len(observations):
        raise ValueError('one prior per historical fixture required')
    first, second, means = [], [], []
    for prior, halves in zip(anchors, observations, strict=True):
        if halves is not None:
            a = np.asarray(halves)
            if a.shape != (2, 2) or a.dtype.kind not in 'iu' or np.any(a < 0):
                raise ValueError('two checked regulation half scores required')
            first.append(a[0]); second.append(a[1]); means.append(rates(prior))
    if not first:
        return dict(version=VERSION, training_observed=0, shares=[.45, .45], leading=1., trailing=1., exposure={})
    first, second, means = (np.asarray(x) for x in (first, second, means))
    shares = (first.sum(axis=0) + 40 * .45) / ((first + second).sum(axis=0) + 40)
    expected = means * (1 - shares)
    lead = np.sign(first - first[:, ::-1])
    result = dict(version=VERSION, training_observed=len(first), shares=shares.tolist(), exposure={})
    for name, state in (('leading', 1), ('trailing', -1)):
        eligible = lead == state
        goals, baseline = int(second[eligible].sum()), float(expected[eligible].sum())
        result[name] = float(np.clip((goals + 20) / (baseline + 20), .5, 2.))
        result['exposure'][name] = dict(team_halves=int(eligible.sum()), goals=goals, expected=baseline)
    return result


@lru_cache(maxsize=1)
def _support():
    counts = np.arange(base.SIZE)
    differences = counts[:, None] - counts[None, :]
    return counts, np.maximum(differences, 0), differences >= 0


def _poisson(mean):
    counts = _support()[0]
    steps = np.full(base.SIZE, mean, dtype=float)
    steps[0] = np.exp(-mean)
    steps[1:] /= counts[1:]
    return np.cumprod(steps)


def _transition(mean):
    _, differences, possible = _support()
    return _poisson(mean)[differences] * possible


def adjusted(prior, fit):
    """Transfer only the learned game-state effect to the complete original R7 lattice."""
    prior = np.asarray(prior, dtype=float)
    if (prior.shape != (base.SIZE, base.SIZE) or not np.all(np.isfinite(prior))
            or np.any(prior < 0) or not np.isclose(prior.sum(), 1., atol=1e-10, rtol=0)):
        raise ValueError('normalized R7 score probabilities required')
    shares = np.asarray(fit['shares'], dtype=float)
    leading, trailing = fit['leading'], fit['trailing']
    if (fit.get('version') != VERSION or shares.shape != (2,) or not np.all((0 < shares) & (shares < 1))
            or not .5 <= leading <= 2 or not .5 <= trailing <= 2):
        raise ValueError('invalid registered halftime-state parameters')
    if leading == trailing == 1.:
        return prior
    means = rates(prior)
    halftime = np.outer(*(_poisson(m) for m in means * shares))
    second = means * (1 - shares)
    alternate = np.zeros_like(prior)
    for region, multipliers in ((base.H > base.A, (leading, trailing)), (base.H < base.A, (trailing, leading)),
                                (base.H == base.A, (1., 1.))):
        home, away = second * multipliers
        alternate += _transition(home) @ (halftime * region) @ _transition(away).T
    neutral = np.outer(*(_poisson(m) for m in means))
    ratio = np.divide(alternate, neutral, out=np.ones_like(alternate), where=neutral > 0)
    corrected = prior * ratio
    return corrected / corrected.sum()
