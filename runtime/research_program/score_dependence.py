"""A discrete FGM copula of the two R7 goal marginals, conditioned on earlier npxG."""
from bisect import bisect_left
from collections import defaultdict
from datetime import date
import math

import numpy as np
from scipy.optimize import minimize

from .territory_history import FORM_WINDOW, LAST_GAMES, SIDES, TerritoryHistory


VERSION = 'R21-five-league-score-dependence-1'
FEATURES = ('intercept', 'club_concordance', 'club_chance_volume')


def _npxg_pair(row):
    sides = row.get('source_history_sides') or {}
    def number(side, field):
        value = (sides.get(side) or {}).get(field)
        if isinstance(value, bool):
            return None
        try:
            value = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return value if math.isfinite(value) and value >= 0 else None

    home, away = (number(s, 'npxG') for s in SIDES)
    home_against, away_against = (number(s, 'npxGA') for s in SIDES)
    if None in (home, away, home_against, away_against):
        return None
    if abs(home - away_against) > .001 or abs(away - home_against) > .001:
        return None
    return home, away


def _club_profile(history):
    pairs = [pair for _, _, pair in history if pair is not None]
    n = len(pairs)
    if not n:
        return np.zeros(2), 0
    values = np.asarray(pairs)
    centered = values - np.mean(values, axis=0)
    scales = np.linalg.norm(centered, axis=0)
    correlation = 0.
    if n >= 3 and np.all(scales > 1e-12):
        correlation = float(centered[:, 0] @ centered[:, 1] / np.prod(scales))
    volume = np.clip((values.sum(axis=1).mean() - 2.6) / 2., -1., 1.)
    return np.array([correlation, volume]) * n / (n + 8), n


class Design:
    """Use the existing as-of team identity resolver; carry matched games with missing pairs."""
    def __init__(self, observations, code):
        records = [r for r in observations if r.get('competition') == code]
        self.identity = TerritoryHistory(records, code)
        self.by_team = defaultdict(list)
        seen = {}
        for r in records:
            mid, day = r.get('source_match_id'), r.get('date')
            team_ids = [r.get(s + '_id') for s in SIDES]
            if not mid or not day or any(t is None or not str(t).strip() for t in team_ids):
                continue
            home, away = (str(t).strip() for t in team_ids)
            if home == away:
                raise ValueError('same team on both sides of Understat fixture ' + str(mid))
            key = str(mid)
            actual_day = date.fromisoformat(day[:10])
            pair = _npxg_pair(r)
            identity = actual_day, home, away, pair
            if key in seen:
                if identity != seen[key]:
                    raise ValueError('conflicting npxG observations: ' + key)
                continue
            seen[key] = identity
            self.by_team[home].append((actual_day, key, pair))
            self.by_team[away].append((actual_day, key, pair[::-1] if pair is not None else None))
        for entries in self.by_team.values():
            entries.sort(key=lambda entry: (entry[0], entry[1]))

    def _from_identity(self, meta):
        start = date.fromisoformat(meta['date']) - FORM_WINDOW
        stop = date.fromisoformat(meta['cutoff'])
        profiles, sources = [], {}
        for side in SIDES:
            team = meta[side + '_id']
            history = self.by_team.get(team, ())
            lo = bisect_left(history, (start,))
            hi = bisect_left(history, (stop,))
            recent = history[max(lo, hi - LAST_GAMES):hi]
            profile, n = _club_profile(recent)
            profiles.append(profile)
            sources[side] = dict(team_id=team, method=meta[side + '_method'], usable=n,
                games=[dict(source_id=mid, date=day.isoformat(), usable=pair is not None) for day, mid, pair in recent])
        x = np.r_[1., np.mean(profiles, axis=0)]
        return x, dict(competition=meta['competition'], date=meta['date'], cutoff=meta['cutoff'], **sources)

    def walk(self, games):
        _, identities = self.identity.walk(games)
        rows = [self._from_identity(meta) for meta in identities]
        return np.array([r[0] for r in rows]).reshape(-1, len(FEATURES)), [r[1] for r in rows]

    def context(self, game, cutoff):
        _, meta = self.identity.context(game, cutoff=cutoff)
        return self._from_identity(meta)


def _marginals(grid):
    prior = np.asarray(grid, dtype=float)
    if (prior.ndim != 2 or not prior.size or not np.isfinite(prior).all() or (prior < 0).any()
            or not math.isclose(float(prior.sum()), 1., rel_tol=1e-9, abs_tol=1e-12)):
        raise ValueError('finite normalized nonnegative score grid required')
    return prior.sum(axis=1), prior.sum(axis=0)


def _rank(marginal):
    return 2. * np.cumsum(marginal) - marginal - 1.


def fit(anchors, features, scores, *, constant=False):
    design = np.asarray(features, dtype=float)
    observed = np.asarray(scores)
    if (design.shape != (len(anchors), len(FEATURES)) or not len(anchors) or not np.isfinite(design).all()
            or not np.all(design[:, 0] == 1.) or observed.shape != (len(anchors), 2)
            or not np.issubdtype(observed.dtype, np.integer) or (observed < 0).any()):
        raise ValueError('one finite design and two regulation goals per training grid required')
    if constant:
        design = design[:, :1]
    terms = []
    for prior, (h, a) in zip(anchors, observed, strict=True):
        home, away = _marginals(prior)
        if h >= len(home) or a >= len(away) or not (home[h] > 0 and away[a] > 0):
            raise ValueError('observed score has no marginal support')
        terms.append(_rank(home)[h] * _rank(away)[a])
    terms = np.asarray(terms)

    def objective(beta):
        t = np.tanh(design @ beta)
        adjustment = .95 * terms * t
        loss = -np.log1p(adjustment).sum() + 2. * (beta @ beta)
        gradient = 4. * beta - design.T @ (.95 * terms * (1. - t*t) / (1. + adjustment))
        return loss, gradient

    dims = design.shape[1]
    fitted = minimize(objective, np.zeros(dims), method='L-BFGS-B', jac=True, bounds=[(-3., 3.)] * dims,
                      options={'ftol': 1e-12, 'gtol': 1e-7, 'maxiter': 200})
    if not fitted.success or not np.isfinite(fitted.x).all():
        raise ValueError('score dependence fit did not converge: ' + str(fitted.message))
    return dict(version=VERSION, features=list(FEATURES[:dims]), beta=fitted.x.tolist(),
                training_rows=len(anchors), penalized_relative_loss=float(fitted.fun))


def matrix(prior, features, weights):
    home, away = _marginals(prior)
    x = np.asarray(features, dtype=float)
    b = np.asarray(weights['beta'], dtype=float)
    if (weights['version'] != VERSION or weights['features'] not in ([FEATURES[0]], list(FEATURES))
            or b.shape != (len(weights['features']),) or not np.isfinite(b).all() or np.any(np.abs(b) > 3.)
            or x.shape != (len(FEATURES),) or x[0] != 1. or not np.isfinite(x).all()):
        raise ValueError('incompatible score dependence weights or prematch features')
    theta = .95 * np.tanh(x[:len(b)] @ b)
    return np.outer(home, away) * (1. + theta * np.outer(_rank(home), _rank(away)))
