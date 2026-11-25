"""Estimate a joint score tilt from earlier within-competition fixture dates and roles."""

from collections import defaultdict
from datetime import date
from itertools import groupby

import numpy as np
from scipy.optimize import minimize
from scipy.special import logsumexp

from . import all_scope_model as base
from .r9_team_shape import bucket_mass


VERSION = "R17-within-competition-schedule-1"
FEATURES = ("own_short_gap", "opponent_short_gap", "own_extra_in_14", "opponent_extra_in_14",
            "own_away_in_7", "opponent_away_in_7")
RIDGE = 24.
HOME_BUCKET, AWAY_BUCKET = np.indices((5, 5)).reshape(2, -1)
BUCKETS = np.stack((HOME_BUCKET, AWAY_BUCKET), axis=1)
EFFECTS = np.stack((BUCKETS > 0, np.maximum(BUCKETS - 1, 0)), axis=2)


class Calendar:
    def __init__(self, prior=None):
        self.teams = defaultdict(list, {team: list(rows) for team, rows in (prior or {}).items()})

    def observe(self, game, day, *, scheduled=False):
        if not game["home"] or not game["away"] or game["home"] == game["away"]:
            raise ValueError("a calendar fixture needs two different teams")
        for side in ("home", "away"):
            team = game[side]
            rows = self.teams[team]
            if rows and rows[-1][0] > day:
                raise ValueError("calendar updates must be chronological")
            self.teams[team] = [row for row in rows if row[0] >= day - 14]
            self.teams[team].append([day, side == "away", scheduled])

    def features(self, game, day):
        features = []
        coverage = {}
        for side in ("home", "away"):
            rows = self.teams.get(game[side], [])
            if rows and rows[-1][0] >= day:
                raise ValueError("calendar features may use only earlier dates")
            recent = [row for row in rows if row[0] >= day - 14]
            gap = day - recent[-1][0] if recent else None
            away = sum(row[1] for row in recent if row[0] >= day - 7)
            features.append([max(0, 7 - gap) / 6 if gap is not None else 0.,
                             min(1., max(0, len(recent) - 2) / 3), min(1., away / 2)])
            coverage[side] = dict(days_since_last_within_14=gap, games_in_14=len(recent),
                                  listed_away_in_7=away, schedule_only_in_14=sum(row[2] for row in recent))
        own, opponent = np.asarray(features, dtype=float)
        return np.stack((np.column_stack((own, opponent)).ravel(),
                         np.column_stack((opponent, own)).ravel())), coverage

    def snapshot(self, last_day):
        return {team: [row for row in rows if row[0] >= last_day - 14]
                for team, rows in sorted(self.teams.items()) if rows and rows[-1][0] >= last_day - 14}


def walk(games, withheld=()):
    calendar = Calendar()
    values, coverage = [], []
    for stamp, group in groupby(games, key=lambda g: g["date"]):
        day = date.fromisoformat(stamp).toordinal()
        fixtures = list(group)
        for game in fixtures:
            x, observed = calendar.features(game, day)
            values.append(x)
            coverage.append(observed)
        for game in fixtures:
            if not withheld or game["id"] not in withheld:
                calendar.observe(game, day)
    return np.asarray(values), coverage, calendar


def fit(anchors, x, scores):
    """A joint exponential-family likelihood on 0, 1, 2, 3 and 4+ goal cells."""
    x = np.asarray(x, dtype=float)
    scores = np.asarray(scores)
    if x.shape != (len(anchors), 2, len(FEATURES)) or scores.shape != (len(anchors), 2):
        raise ValueError("training inputs do not align")
    if (not np.isfinite(x).all() or (x < 0).any() or (x > 1).any()
            or not np.issubdtype(scores.dtype, np.integer) or (scores < 0).any()):
        raise ValueError("unusable calendar features or score labels")
    if not len(scores):
        # With no training observations the registered ridge objective is minimized at zero.
        return dict(features=list(FEATURES), penalty=RIDGE,
                    scoring_beta=[0.]*len(FEATURES), additional_beta=[0.]*len(FEATURES))
    mass = np.array([bucket_mass(m).ravel() for m in anchors])
    observed = np.minimum(scores[:, 0], 4) * 5 + np.minimum(scores[:, 1], 4)
    if (not np.isfinite(mass).all() or (mass < 0).any() or not np.allclose(mass.sum(axis=1), 1.)
            or (mass[np.arange(len(mass)), observed] <= 0).any()):
        raise ValueError("invalid observed joint score mass")
    with np.errstate(divide="ignore"):
        logmass = np.log(mass)
    design = np.einsum("nsf,bsq->nbqf", x, EFFECTS).reshape(len(mass), 25, -1)
    sufficient = design[np.arange(len(mass)), observed].sum(axis=0)

    def objective(beta):
        logits = logmass + design @ beta
        norm = logsumexp(logits, axis=1)
        prob = np.exp(logits - norm[:, None])
        loss = norm.sum() - sufficient @ beta + RIDGE / 2 * (beta @ beta)
        grad = np.einsum("nb,nbk->k", prob, design) - sufficient + RIDGE * beta
        return loss, grad

    width = 2 * len(FEATURES)
    result = minimize(objective, np.zeros(width), jac=True, method="L-BFGS-B",
                      bounds=[(-1., 1.)] * width, options={"ftol": 1e-13, "gtol": 1e-8})
    if not result.success:
        raise ValueError("calendar joint fit failed: " + str(result.message))
    return dict(features=list(FEATURES), penalty=RIDGE,
                scoring_beta=result.x[:len(FEATURES)].tolist(), additional_beta=result.x[len(FEATURES):].tolist())


def apply(prior, features, fitted):
    x = np.asarray(features, dtype=float)
    if fitted["features"] != list(FEATURES) or x.shape != (2, len(FEATURES)) or not np.isfinite(x).all():
        raise ValueError("unexpected schedule model or feature dimensions")
    if not x.any():
        return prior
    betas = np.array([fitted["scoring_beta"], fitted["additional_beta"]])
    shifts = np.einsum("bsq,sq->b", EFFECTS, x @ betas.T)
    if not shifts.any():
        return prior
    tilted = prior * np.exp(shifts[base.CELL].reshape(prior.shape) - shifts.max())
    return tilted / tilted.sum()
