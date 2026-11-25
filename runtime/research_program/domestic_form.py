"""Domestic-only team histories for predicting continental club scorelines."""

from bisect import bisect_left
from collections import defaultdict
from datetime import date
from itertools import groupby

import numpy as np
from scipy.optimize import minimize

from . import all_scope_model as base

VERSION = 'R13-domestic-form-and-rest-1'
CODES = ('afc.champions', 'uefa.champions', 'uefa.europa', 'uefa.europa.conf')
FEATURES = ('own_attack', 'opponent_concessions', 'relative_short_rest', 'relative_games_14d')


def team_id(value):
    value = str(value)
    if not value.isascii() or not value.isdigit() or int(value) < 1:
        raise ValueError('invalid ESPN team identity')
    return value


class DomesticHistory:
    """Index completed league games with venue expectations known before each date."""

    def __init__(self, records):
        self.games = defaultdict(list)
        seen = set()
        league = defaultdict(lambda: np.array([40., 56., 56.]))
        records = sorted(records, key=lambda row: (row['date'], row['competition'], row['id']))
        self.through = records[-1]['date'] if records else None
        for day, group in groupby(records, key=lambda row: row['date']):
            ordinal = date.fromisoformat(day).toordinal()
            daily = defaultdict(lambda: np.zeros(3))
            for row in group:
                event = str(row['id']).rsplit(':', 1)[-1]
                home, away = (team_id(row[key]) for key in ('home_id', 'away_id'))
                h, a = row['home_goals'], row['away_goals']
                if (day < '2023-01-01' or event in seen or not event.isdigit() or home == away
                        or any(type(n) is not int or n < 0 for n in (h, a))
                        or len(row['source_sha256']) != 64):
                    raise ValueError('invalid or duplicate domestic observation: ' + event)
                seen.add(event)
                code = row['competition']
                count, home_sum, away_sum = league[code]
                expected_home, expected_away = home_sum / count, away_sum / count
                self.games[home].append((ordinal, h, a, expected_home, expected_away))
                self.games[away].append((ordinal, a, h, expected_away, expected_home))
                daily[code] += (1, h, a)
            for code, delta in daily.items():
                league[code] += delta
        self.dates = {key: [r[0] for r in games] for key, games in self.games.items()}

    def profile(self, team, target, cutoff):
        games = self.games.get(team, ())
        end = bisect_left(self.dates.get(team, ()), min(target, cutoff))
        past = [r for r in games[max(0, end - 8):end] if r[0] >= target - 120]
        if not past:
            return (0., 0., 0., 0.), 0
        goals, conceded, expected, against = np.sum(np.asarray(past)[:, 1:], axis=0)
        attack, defence = np.clip(np.log([(5.6 + goals) / (5.6 + expected),
                                            (5.6 + conceded) / (5.6 + against)]), -1., 1.)
        days_rest = target - past[-1][0]
        short_rest = max(0, 5 - days_rest) / 4
        recent_start = bisect_left(self.dates[team], target - 14, 0, end)
        load = min(1., (end - recent_start) / 4)
        return (attack, defence, short_rest, load), len(past)

    def context(self, team_ids, day, *, cutoff=None):
        target = date.fromisoformat(day).toordinal()
        valid_until = date.fromisoformat(cutoff).toordinal() if cutoff else target
        home, n_home = self.profile(team_ids[0], target, valid_until)
        away, n_away = self.profile(team_ids[1], target, valid_until)
        diff_rest, diff_load = home[2] - away[2], home[3] - away[3]
        design = [[home[0], away[1], diff_rest, diff_load],
                  [away[0], home[1], -diff_rest, -diff_load]]
        return np.asarray(design), [n_home, n_away]


def fit(priors, design, scores):
    if not (len(priors) == len(design) == len(scores)):
        raise ValueError('model input population mismatch')
    trained = np.any(design != 0, axis=(1, 2))
    if not np.any(trained):
        return dict(beta=[0.] * len(FEATURES), games_with_signal=0)
    x = design[trained].reshape(-1, len(FEATURES))
    matrices = np.asarray(priors)[trained]
    means = np.column_stack([(matrices * grid).sum(axis=(1, 2)) for grid in (base.H, base.A)]).ravel()
    observed = np.asarray(scores)[trained].ravel()

    def objective(beta):
        log_effect = x @ beta
        predicted = means * np.exp(log_effect)
        value = predicted.sum() - observed @ log_effect + 8 * (beta @ beta)
        return value, x.T @ (predicted - observed) + 16 * beta

    fit_result = minimize(objective, np.zeros(len(FEATURES)), jac=True, method='L-BFGS-B',
                          bounds=[(-1., 1.)] * len(FEATURES))
    if not fit_result.success:
        raise ValueError(str(fit_result.message))
    return dict(beta=fit_result.x.tolist(), games_with_signal=int(trained.sum()))


def adjust(prior, design, fitted):
    shift = np.clip(design @ fitted['beta'], -.55, .55)
    if not np.any(shift):
        return prior
    matrix = prior * np.exp(base.H * shift[0] + base.A * shift[1])
    return matrix / matrix.sum()
