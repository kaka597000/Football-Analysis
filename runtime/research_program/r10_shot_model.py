"""A causal score model using team shot volume, finishing and game-level shot covariance."""

from collections import defaultdict
from datetime import date
from itertools import groupby

import numpy as np
from scipy.optimize import minimize

from . import all_scope_model as base
from .all_scope_study import half_life
from .r9_team_shape import actual_rank
from optimization.evaluate import settle_ou

VERSION = 'R10-shot-volume-finishing-1'
DOSES = (0., .5, 1.)
START_SHOTS = np.array([4.5, 3.8])


def decayed(record, day, half):
    if record is None:
        return np.zeros((2, 4))
    last, counts = record
    if day <= last:
        raise ValueError('shot history must precede the forecast date')
    return np.asarray(counts) * .5 ** ((day - last) / half)


def context(state, game, day, half):
    phase = state.get(game['stage'], {})
    league = decayed(phase.get('league'), day, half)
    teams = phase.get('teams', {})
    home = decayed(teams.get(game['home']), day, half)
    away = decayed(teams.get(game['away']), day, half)
    venue = (league[:, 1] + 8 * START_SHOTS) / (league[:, 0] + 8)
    conversion = (league[:, 3] + 12) / (league[:, 2] + 40)
    if game.get('neutral') is True:
        venue[:] = venue.mean()
        conversion[:] = conversion.mean()
    pooled_shots = venue.mean()
    pooled_conversion = (league[:, 3].sum() + 24) / (league[:, 2].sum() + 80)

    def rate(counts):
        shot = (counts[1] + 8 * pooled_shots) / (counts[0] + 8)
        finish = (counts[3] + 40 * pooled_conversion) / (counts[2] + 40)
        return shot / pooled_shots, finish / pooled_conversion

    attack = np.array([rate(home[0]), rate(away[0])])
    allowance = np.array([rate(away[1]), rate(home[1])])
    relative = np.sqrt(attack * allowance)
    return dict(ready=bool(min(home[0, 0], away[0, 0]) > 0),
                exposure=[float(home[0, 0]), float(away[0, 0])],
                shots=venue * relative[:, 0], conversion=conversion, relative_finish=relative[:, 1])


def observation(row, game):
    if row is None:
        return None
    if any(row[k] != game[k] for k in ('id', 'competition', 'date', 'home', 'away')):
        raise ValueError('shot identity mismatch: ' + game['id'])
    values = [row.get(k + '_sot') for k in ('home', 'away')]
    for side, value in zip(('home', 'away'), values):
        if type(value) not in (int, float) or not np.isfinite(value) or value < 0 or value != int(value):
            return None
        shots = row.get(side + '_shots')
        if shots is not None and shots < value:
            return None
    return np.array(values)


def walk(rows, observed, code):
    state, contexts, shots = {}, [], []
    half = half_life(code)
    for stamp, group in groupby(rows, key=lambda g: g['date']):
        day = date.fromisoformat(stamp).toordinal()
        league_delta = defaultdict(lambda: np.zeros((2, 4)))
        team_delta = defaultdict(lambda: np.zeros((2, 4)))
        for game in group:
            contexts.append(context(state, game, day, half))
            value = observation(observed.get(game['id']), game)
            shots.append(value)
            if value is None:
                continue
            goals = np.array([game['home_goals'], game['away_goals']])
            legal = goals <= value
            update = np.column_stack((np.ones(2), value, np.where(legal, value, 0), np.where(legal, goals, 0)))
            stage = game['stage']
            league_delta[stage] += update
            team_delta[stage, game['home']] += update
            team_delta[stage, game['away']] += update[::-1]
        for stage, change in league_delta.items():
            phase = state.setdefault(stage, {'teams': {}})
            phase['league'] = [day, (decayed(phase.get('league'), day, half) + change).tolist()]
        for (stage, name), change in team_delta.items():
            teams = state[stage]['teams']
            teams[name] = [day, (decayed(teams.get(name), day, half) + change).tolist()]
    return contexts, shots, state


def design(matrix, ctx):
    means = np.array([np.sum(matrix * base.H), np.sum(matrix * base.A)])
    volume = ctx['shots'] * ctx['conversion'] / means
    x = np.clip(np.log(np.column_stack((volume, ctx['relative_finish']))), -1.25, 1.25)
    return means, x


def fit(priors, contexts, shots, scores):
    ready = [i for i, ctx in enumerate(contexts) if ctx['ready']]
    beta = np.zeros(2)
    if ready:
        data = [design(priors[i], contexts[i]) for i in ready]
        offsets = np.concatenate([m for m, x in data])
        x = np.vstack([x for m, x in data])
        target = np.asarray(scores)[ready].ravel()

        def objective(b):
            shift = x @ b
            mu = offsets * np.exp(shift)
            return mu.sum() - target @ shift + 8 * (b @ b), x.T @ (mu - target) + 16 * b

        fitted = minimize(objective, beta, jac=True, method='L-BFGS-B', bounds=[(-1., 1.)] * 2)
        if not fitted.success:
            raise ValueError('shot and finishing rate fit failed: ' + str(fitted.message))
        beta = fitted.x
    residuals = [shots[i] / contexts[i]['shots'] - 1 for i in ready if shots[i] is not None]
    covariance = 0.
    if residuals:
        e = np.asarray(residuals)
        covariance = float(np.prod(e - e.mean(axis=0), axis=1).sum() / (len(e) + 60))
    return dict(beta=beta.tolist(), covariance=covariance, signed_shock=float(np.sign(covariance) *
                min(.35, np.sqrt(abs(covariance)))), rate_fit_games=len(ready), covariance_games=len(residuals))


def alternatives(prior, ctx, fit):
    if not ctx['ready']:
        return prior, prior
    means, x = design(prior, ctx)
    shift = x @ fit['beta']
    log_ratio = base.H * shift[0] + base.A * shift[1]
    def tilted(logs):
        if not np.any(logs):
            return prior
        matrix = prior * np.exp(logs)
        return matrix / matrix.sum()
    rate_only = tilted(log_ratio)
    shock = fit['signed_shock']
    if shock == 0:
        return rate_only, rate_only
    change = np.array([abs(shock), shock])
    rates = means * np.exp(shift)
    def regime(z):
        factors = np.log1p(z * change)
        return base.H * factors[0] + base.A * factors[1] - z * (rates @ change)
    return rate_only, tilted(log_ratio + np.logaddexp(regime(1), regime(-1)) - np.log(2.))


def blend(prior, full, dose):
    if dose == 0. or full is prior:
        return prior
    return full if dose == 1. else prior * (1 - dose) + full * dose


def choose(rows, matrices, full):
    trials = []
    for dose in DOSES:
        counts = dict(dose=dose, first=0, three=0, wdl=0, total=0, total_n=0, loss=0.)
        for g, prior, other in zip(rows, matrices, full, strict=True):
            m = blend(prior, other, dose)
            actual = (g['home_goals'], g['away_goals'])
            rank = actual_rank(m, actual)
            counts['first'] += rank == 1
            counts['three'] += rank <= 3
            counts['loss'] -= float(np.log(max(1e-300, m[actual])))
            line = base.actual_line(g)
            pick = base.pick(m, line)
            counts['wdl'] += pick['wdl'] == base.SIDES[base.RESULT[actual]]
            if line is not None:
                outcome = settle_ou(sum(actual), line, pick['total_side'])
                counts['total'] += outcome == 'FULL_WIN'
                counts['total_n'] += outcome != 'PUSH'
        trials.append(counts)
    score = min(trials, key=lambda t: (-t['first'], -t['three'], t['loss'], t['dose']))
    wdl = max(trials, key=lambda t: t['wdl'])
    total = max(trials, key=lambda t: t['total'] / max(1, t['total_n']))
    return dict(score=score['dose'], wdl=wdl['dose'], total=total['dose']), trials


def decisions(prior, full, doses, line, original):
    matrices = {d: blend(prior, full, d) for d in set(doses.values())}
    picks = {d: original if m is prior else base.pick(m, line) for d, m in matrices.items()}
    chosen = dict(picks[doses['score']], wdl=picks[doses['wdl']]['wdl'], total_side=picks[doses['total']]['total_side'])
    return chosen, {head: matrices[d] for head, d in doses.items()}
