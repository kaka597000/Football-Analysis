"""R23 prematch penalty-related xG contrasts and a two-slope R7 score tilt."""

from bisect import bisect_left
from collections import defaultdict
from datetime import date
from decimal import Context, Decimal, InvalidOperation, localcontext
import math
from numbers import Real

import numpy as np
from scipy.optimize import minimize

from .territory_history import SIDES, TerritoryHistory, _competition, _source_day, _source_id
from .three_arm_model import SIDES as WDL_SIDES, top_three_scores


VERSION = 'R23-penalty-opportunity-1'
FEATURES = ('home_attack', 'away_allowance', 'away_attack', 'home_allowance')
DOSES = (0., .5, 1.)
LAST_GAMES = 12
FEATURE_LIMIT = .75
COEFFICIENT_BOUND = 2.
RIDGE = 8.
FIT_GTOL = 1e-6
ROUNDING = Decimal('0.002')
ZERO_SCORE_LOSS = -math.log(1e-300)  # R22's exact-score log-loss floor


def _penalty_pair(row):
    """Return mirrored (home, away) penalty-related xG, or a missing pair."""
    sides = row.get('source_history_sides')
    if not isinstance(sides, dict) or any(not isinstance(sides.get(side), dict) for side in SIDES):
        return None
    fields = ('xG', 'npxG', 'xGA', 'npxGA')
    # Compare the printed decimal values so a rounding-sized discrepancy at
    # exactly 0.002 is treated the same on either side of the mirror.
    with localcontext(Context(prec=32)):
        try:
            home, away = [[Decimal(str(sides[side].get(field))) for field in fields] for side in SIDES]
        except InvalidOperation:
            return None
        if any(not v.is_finite() or v < 0 or not math.isfinite(v) for v in home + away):
            return None
        own = home[0] - home[1], away[0] - away[1]
        mirror = away[2] - away[3], home[2] - home[3]
        if min(own + mirror) < -ROUNDING or any(abs(a - b) > ROUNDING for a, b in zip(own, mirror)):
            return None
        return tuple(float((max(a, 0) + max(b, 0)) / 2) for a, b in zip(own, mirror))


class Design:
    """Use TerritoryHistory for as-of R5/Understat identity and calendar matching."""

    def __init__(self, events, code):
        code = _competition(code)
        records = [row for row in events if _competition(row.get('competition')) == code and row.get('date')]
        self.identity = TerritoryHistory(records, code)
        self._by_team = defaultdict(list)
        self._league_days = []
        self._league_means = []
        fixtures = {}
        for row in records:
            day = _source_day(row['date'])
            match_id = _source_id(row.get('source_match_id'))
            teams = tuple(_source_id(row.get(side + '_id')) for side in SIDES)
            if teams[0] == teams[1]:
                continue
            pair = _penalty_pair(row) if match_id is not None and None not in teams else None
            source_hash = row.get('source_sha256')
            key = day, match_id, teams
            if key in fixtures and fixtures[key][0] != pair:
                pair = None
            fixtures[key] = pair, source_hash

        mean = 0.
        for (day, match_id, teams), (pair, source_hash) in sorted(
                fixtures.items(), key=lambda item: (item[0][0], item[0][1] or '',
                                                    tuple(team or '' for team in item[0][2]))):
            for i, team in enumerate(teams):
                if team is not None:
                    oriented = pair[::-1] if pair is not None and i == 1 else pair
                    self._by_team[team].append((day, match_id, oriented, source_hash))
            if pair is not None:
                self._league_days.append(day)
                # Every valid fixture contributes two equally weighted team-games.
                low, high = sorted(pair)
                match_mean = low + (high - low) / 2
                mean += (match_mean - mean) / len(self._league_days)
                self._league_means.append(mean)

    def _from_identity(self, identity):
        stop = date.fromisoformat(identity['cutoff'])
        count = (bisect_left(self._league_days, stop)
                 if identity['competition'] == self.identity.competition else 0)
        mean = self._league_means[count - 1] if count else None
        profiles, sources = [], {}
        for side in SIDES:
            team = identity[side + '_id']
            history = self._by_team.get(team, ())
            end = bisect_left(history, (stop,))
            recent = history[max(0, end - LAST_GAMES):end]
            pairs = [pair for _, _, pair, _ in recent if pair is not None]
            n = len(pairs)
            profiles.append(np.clip([math.fsum((pair[i] - mean) / (n + 8) for pair in pairs)
                                     for i in range(2)], -FEATURE_LIMIT, FEATURE_LIMIT))
            sources[side] = dict(
                source_team_id=team, method=identity[side + '_method'], n=n, missing=n == 0,
                games=[dict(source_id=match_id, date=day.isoformat(), valid=pair is not None,
                            source_sha256=source_hash, xg_for=pair[0] if pair is not None else None,
                            xg_against=pair[1] if pair is not None else None)
                       for day, match_id, pair, source_hash in recent])
        (home_for, home_against), (away_for, away_against) = profiles
        return (np.array([home_for, away_against, away_for, home_against]),
                dict(competition=identity['competition'], cutoff=identity['cutoff'], league_mean=mean,
                     league_n=2 * count, **sources))

    def walk(self, games):
        """Return an N×4 design and aligned evidence in input fixture order."""
        _, identities = self.identity.walk(games)
        rows = [self._from_identity(identity) for identity in identities]
        return np.array([x for x, _ in rows]).reshape(-1, len(FEATURES)), [meta for _, meta in rows]

    def context(self, game, cutoff):
        """Return a forecast's design at the earlier of cutoff and fixture date."""
        _, identity = self.identity.context(game, cutoff=cutoff)
        return self._from_identity(identity)


def _grid(values, ndim):
    grid = np.asarray(values, dtype=float)
    if (grid.ndim != ndim or not grid.size or not np.isfinite(grid).all() or (grid < 0).any()
            or not np.allclose(grid.sum(axis=(-2, -1)), 1., rtol=1e-9, atol=1e-12)):
        raise ValueError('finite normalized nonnegative score grids required')
    return grid


@np.errstate(divide='ignore')
def _log_grid(grid):
    return np.log(grid)  # A zero prior cell has no support under any tilt.


@np.errstate(under='ignore')
def _tilted_mass(log_prior, shifts, home, away):
    home_tilt = shifts[..., 0, None, None] * home[:, None]
    away_tilt = shifts[..., 1, None, None] * away
    logits = log_prior + (home_tilt + away_tilt)
    peak = logits.max(axis=(-2, -1), keepdims=True)
    mass = np.exp(logits - peak)
    return mass, mass.sum(axis=(-2, -1)), peak[..., 0, 0]


def fit(priors, features, scores):
    """Fit one league to supplied chronological R7 anchors and regulation scores."""
    grids = _grid(priors, 3)
    n = len(grids)
    x = np.asarray(features, dtype=float)
    y = np.asarray(scores)
    if (x.shape != (n, len(FEATURES)) or not np.isfinite(x).all() or (np.abs(x) > FEATURE_LIMIT).any()
            or y.shape != (n, 2) or not np.issubdtype(y.dtype, np.integer) or (y < 0).any()):
        raise ValueError('one bounded design and two nonnegative integer scores per prior required')
    home, away = (np.arange(size, dtype=float) for size in grids.shape[-2:])
    inside = (y[:, 0] < len(home)) & (y[:, 1] < len(away))
    rows = np.flatnonzero(inside)
    supported = grids[rows, y[rows, 0], y[rows, 1]] > 0
    rows = rows[supported]
    observed = y[rows]
    design = x[rows].reshape(-1, 2, 2)
    log_prior = _log_grid(grids[rows])
    observed_log_prior = log_prior[np.arange(len(rows)), observed[:, 0], observed[:, 1]]

    def objective(beta):
        shifts = design @ beta
        mass, total, peak = _tilted_mass(log_prior, shifts, home, away)
        expected_home = (mass.sum(axis=2) * home).sum(axis=1) / total
        expected_away = (mass.sum(axis=1) * away).sum(axis=1) / total
        residual = np.column_stack((expected_home, expected_away)) - observed
        gradient = (design * residual[..., None]).sum(axis=(0, 1)) + 2 * RIDGE * beta
        # The observed prior log probabilities are constant in the slopes.
        loss = (peak + np.log(total) - (shifts * observed).sum(axis=1)).sum() + RIDGE * (beta @ beta)
        return float(loss), gradient

    lower, upper = -COEFFICIENT_BOUND, COEFFICIENT_BOUND
    result = minimize(objective, np.zeros(2), jac=True, method='L-BFGS-B', bounds=[(lower, upper)] * 2,
                      options={'ftol': 1e-13, 'gtol': FIT_GTOL, 'maxiter': 200, 'maxls': 50})
    # Check stationarity in the box directly. Improvement at smaller gradients
    # can fall below double precision during the line search.
    projected = result.x - np.clip(result.x - result.jac, lower, upper)
    if not (np.isfinite(result.fun) and np.max(np.abs(projected)) <= FIT_GTOL):
        raise ValueError('penalty opportunity fit did not converge: ' + str(result.message))
    # Out-of-grid scores and zero prior cells incur the fixed finite loss; they
    # have no score-grid mass that the two slopes can change.
    loss = result.fun - observed_log_prior.sum() + (n - len(rows)) * ZERO_SCORE_LOSS
    return dict(version=VERSION, features=list(FEATURES), beta=result.x.tolist(), training_rows=n,
                outside_grid=int((~inside).sum()), zero_prior_cells=int((~supported).sum()),
                penalized_loss=float(loss))


@np.errstate(under='ignore')
def matrix(prior, feature, fit):
    """Tilt every R7 score cell and normalize over the full grid."""
    prior = _grid(prior, 2)
    x = np.asarray(feature, dtype=float)
    beta = np.asarray(fit['beta'], dtype=float)
    if (x.shape != (len(FEATURES),) or not np.isfinite(x).all() or (np.abs(x) > FEATURE_LIMIT).any()
            or beta.shape != (2,) or not np.isfinite(beta).all() or (np.abs(beta) > COEFFICIENT_BOUND).any()
            or fit.get('version', VERSION) != VERSION or tuple(fit.get('features', FEATURES)) != FEATURES):
        raise ValueError('incompatible penalty opportunity fit or features')
    shifts = x.reshape(2, 2) @ beta
    if not np.any(shifts):
        return prior.copy()
    home, away = (np.arange(size, dtype=float) for size in prior.shape)
    h, a = np.nonzero(prior)
    if np.ptp(shifts[0] * h + shifts[1] * a) == 0:
        return prior.copy()
    mass, total, _ = _tilted_mass(_log_grid(prior), shifts, home, away)
    return mass / total


def choices(prior, full, dose, control, line):
    """Return one dose's score picks, inherited R7 market directions and score grid."""
    prior, full = _grid(prior, 2), _grid(full, 2)
    if prior.shape != full.shape or prior.size < 3:
        raise ValueError('prior and full grids must have the same shape and at least three cells')
    if isinstance(dose, bool) or not isinstance(dose, Real) or dose not in DOSES:
        raise ValueError('dose must be one of 0, 0.5, or 1')
    if line is not None and (isinstance(line, bool) or not isinstance(line, Real)
                             or not math.isfinite(line) or line <= 0):
        raise ValueError('line must be a positive finite number or None')
    top3 = control.get('top3')
    if (control.get('wdl') not in WDL_SIDES or not isinstance(top3, (list, tuple)) or len(top3) != 3
            or any(not isinstance(score, str) for score in top3) or len(set(top3)) != 3
            or control.get('primary') != top3[0]):
        raise ValueError('a valid original R7 first, ordered three, and WDL are required')
    side = control.get('total_side')
    if side not in (None, 'over', 'under'):
        raise ValueError('original R7 total side must be over, under, or None')
    if side is not None and control.get('total_line') != line:
        raise ValueError('original R7 total line differs from the supplied common line')
    if dose == 0 or np.array_equal(prior, full):
        grid = prior.copy()
    elif dose == 1:
        grid = full.copy()
    else:
        grid = (1. - dose) * prior + dose * full
    ranked = list(top3) if np.array_equal(grid, prior) else top_three_scores(grid)
    return (dict(primary=ranked[0], top3=ranked, wdl=control['wdl'], total_side=side,
                 total_line=control.get('total_line', line)), grid)
