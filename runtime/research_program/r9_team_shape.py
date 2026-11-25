"""Prior-match team scoring and conceding distributions, relative to the match anchor."""

from collections import defaultdict
from datetime import date
from itertools import groupby

import numpy as np
from scipy.optimize import minimize

from . import all_scope_model as base
from .all_scope_study import half_life

VERSION = 'R9-team-goal-buckets-1'
PSEUDO = np.array([2., 2.5, 2., 1., .5])
LOG_LIMIT = np.log(3.)
PENALTY = 8.
DOSES = (0., .5, 1.)


def bucket_mass(matrix):
    return np.bincount(base.CELL, weights=matrix.ravel(), minlength=25).reshape(5, 5)


def previous(teams, team, day, decay_days):
    """Return discounted counts indexed by observed/expected, scored/conceded, bucket."""
    if team not in teams:
        return np.zeros((2, 2, 5))
    last, counts = teams[team]
    if day <= last:
        raise ValueError('team history must precede the full forecast date')
    return np.asarray(counts) * (.5 ** ((day - last) / decay_days))


def features(state, game, day, decay_days):
    teams = state.get(game['stage'], {})
    home, away = [previous(teams, game[side], day, decay_days) for side in ('home', 'away')]
    def residual(counts):
        return np.clip(np.log((counts[0] + PSEUDO) / (counts[1] + PSEUDO)), -LOG_LIMIT, LOG_LIMIT)
    h, a = residual(home), residual(away)
    attack = h[0, :, None] + a[0, None, :]
    defence = a[1, :, None] + h[1, None, :]
    exposures = [float(counts[0, 0].sum()) for counts in (home, away)]
    return np.stack((attack.ravel(), defence.ravel()), axis=1), exposures


def history(rows, anchors, code):
    """Emit all features for a date from one snapshot, then assimilate its results."""
    if len(rows) != len(anchors):
        raise ValueError('one chronological anchor is required per match')
    state, design, exposures = {}, [], []
    decay_days = half_life(code)
    for stamp, indices in groupby(range(len(rows)), key=lambda i: rows[i]['date']):
        day = date.fromisoformat(stamp).toordinal()
        changes = defaultdict(lambda: np.zeros((2, 2, 5)))
        for i in indices:
            game = rows[i]
            x, exposure = features(state, game, day, decay_days)
            design.append(x)
            exposures.append(exposure)
            mass = bucket_mass(anchors[i])
            actual = np.eye(5)[[min(game['home_goals'], 4), min(game['away_goals'], 4)]]
            update = np.stack((actual, np.stack((mass.sum(axis=1), mass.sum(axis=0)))))
            changes[game['stage'], game['home']] += update
            changes[game['stage'], game['away']] += update[:, ::-1]
        for (stage, team), change in changes.items():
            teams = state.setdefault(stage, {})
            counts = previous(teams, team, day, decay_days) + change
            teams[team] = [day, counts.tolist()]
    return np.asarray(design), exposures, state


def fit(anchors, design, scores):
    """Fit two bounded slopes using exact-score likelihood on the 25 bucket pairs."""
    if not len(scores):
        return [0., 0.]
    prior = np.array([bucket_mass(m).ravel() for m in anchors])
    h, a = np.minimum(scores, 4).T
    observed = design[np.arange(len(scores)), 5 * h + a].sum(axis=0)

    def objective(beta):
        weighted = prior * np.exp(design @ beta)
        normalizer = weighted.sum(axis=1)
        p = weighted / normalizer[:, None]
        loss = np.log(normalizer).sum() - observed @ beta
        gradient = np.einsum('nc,nck->k', p, design) - observed
        return loss + PENALTY / 2 * (beta @ beta), gradient + PENALTY * beta

    result = minimize(objective, np.zeros(2), jac=True, method='L-BFGS-B',
                      bounds=[(-1., 1.)] * 2, options={'ftol': 1e-12})
    if not result.success:
        raise ValueError('team shape fit failed: ' + str(result.message))
    return result.x.tolist()


def adjust(matrix, x, beta, dose):
    tilt = (x @ beta) * dose
    if not np.any(tilt):
        return matrix
    weighted = matrix * np.exp(tilt)[base.CELL].reshape(matrix.shape)
    return weighted / weighted.sum()


def actual_rank(matrix, actual):
    index = np.ravel_multi_index(actual, matrix.shape)
    cells = matrix.ravel()
    # Matches the stable row-major tie ordering used for published top three.
    return int(1 + np.count_nonzero(cells > cells[index]) + np.count_nonzero(cells[:index] == cells[index]))


def published_rank(matrix, actual, ordered):
    """Use the original three published scores, then the reconstructed remaining order."""
    score = '%d-%d' % tuple(actual)
    if score in ordered:
        return ordered.index(score) + 1
    rank = actual_rank(matrix, actual)
    promoted = [tuple(map(int, s.split('-'))) for s in ordered]
    return len(ordered) + rank - sum(actual_rank(matrix, cell) < rank for cell in promoted)


def choose(matrices, design, beta, scores):
    trials = []
    for dose in DOSES:
        first, three, loss = 0, 0, 0.
        for m, x, actual in zip(matrices, design, scores, strict=True):
            p = adjust(m, x, beta, dose)
            rank = actual_rank(p, actual)
            first += rank == 1
            three += rank <= 3
            loss -= np.log(max(1e-300, p[tuple(actual)]))
        trials.append(dict(dose=dose, games=len(scores), first=first, three=three,
                           mean_log_loss=float(loss / len(scores)) if len(scores) else None))
    best = min(trials, key=lambda t: (-t['first'], -t['three'], t['mean_log_loss'] or 0., t['dose']))
    return best['dose'], trials
