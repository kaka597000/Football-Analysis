"""R26 date-causal season and club scoring-rate adjustment for J1."""

import argparse
from collections import defaultdict
from datetime import date
from itertools import groupby
from pathlib import Path
import tempfile

import numpy as np
from scipy.optimize import minimize

from . import all_scope_model as base
from . import all_scope_study as study
from . import continuation_report as report
from . import continuation_scores as r7
from . import continuation_study as archive
from . import j1_team_allocation as r25
from . import r8_score_study as control
from .goal_state_study import sha, write_new
from .ucl_zero_goals import IDENTITY, TOTAL, quality, reference_rows, selection, totals


PROTOCOL = Path(__file__).resolve().parents[2] / 'research/20261126-r26-j1-total-pace/protocol.json'
LEAGUE = '@league'
BUCKET = np.minimum(TOTAL, 8)
LEVELS = np.arange(9)
HALF_LIFE = 45


def decayed(record, day):
    if record is None:
        return np.zeros(2)
    stamp, residual, count = record
    if stamp >= day:
        raise ValueError('total-goals features require history strictly before the match date')
    return np.array([residual, count]) * .5 ** ((day - stamp) / HALF_LIFE)


def features(state, game, day):
    previous = state.get(game['season'], {})
    league, home, away = [decayed(previous.get(key), day) for key in (LEAGUE, game['home'], game['away'])]
    league_contrast = np.clip(league[0] / (league[1] + 20), -1., 1.)
    team_contrasts = [np.clip(team[0] / (team[1] + 8), -1., 1.) for team in (home, away)]
    return np.array([1., league_contrast, float(np.mean(team_contrasts))]), [float(home[1]), float(away[1])]


def history(games, anchors):
    """Score every fixture on a date against the same pre-date season state."""
    state, design, exposures = {}, [], []
    entries = zip(games, anchors, strict=True)
    for stamp, group in groupby(entries, key=lambda item: item[0]['date']):
        day = date.fromisoformat(stamp).toordinal()
        changes = defaultdict(lambda: np.zeros(2))
        for game, anchor in group:
            x, exposure = features(state, game, day)
            design.append(x)
            exposures.append(exposure)
            if game['stage'] != r25.STAGE:
                continue
            residual = game['home_goals'] + game['away_goals'] - anchor.ravel() @ TOTAL
            for key in (LEAGUE, game['home'], game['away']):
                changes[game['season'], key] += [float(np.clip(residual, -4., 4.)), 1.]
        for (season, key), delta in changes.items():
            members = state.setdefault(season, {})
            updated = decayed(members.get(key), day) + delta
            members[key] = [day, *updated.tolist()]
    return np.asarray(design), exposures, state


def fit(anchors, design, scores):
    """Fit by the likelihood of the observed total with the anchor as offset."""
    observed = np.minimum(scores.sum(axis=1), 8)
    prior = np.array([np.bincount(BUCKET, weights=matrix.ravel(), minlength=len(LEVELS)) for matrix in anchors])
    achieved = design.T @ observed

    def objective(beta):
        posterior = prior * np.exp((design @ beta)[:, None] * LEVELS)
        norm = posterior.sum(axis=1)
        expected = (posterior @ LEVELS) / norm
        loss = np.log(norm).sum() - achieved @ beta + 16. * (beta @ beta)
        gradient = design.T @ expected - achieved + 32. * beta
        return loss, gradient

    result = minimize(objective, np.zeros(3), jac=True, method='L-BFGS-B',
                      bounds=[(-.15, .15), (-.25, .25), (-.25, .25)], options={'ftol': 1e-12})
    if not result.success:
        raise ValueError('total-goals calibration fit failed: ' + result.message)
    return result.x.tolist()


def adjust(prior, design, beta, dose):
    """Tilt the totals without changing which score is most likely within a total."""
    shift = float(design @ beta)
    if dose == 0. or shift == 0.:
        return prior
    weighted = prior * np.exp(shift * BUCKET).reshape(prior.shape)
    projected = weighted / weighted.sum()
    return projected if dose == 1. else (1. - dose) * prior + dose * projected


def evaluated(game, prior, design, exposure, beta, dose, window, r7_ref, r25_ref):
    actual = game['home_goals'], game['away_goals']
    line = base.actual_line(game)
    row = dict(id=game['id'], cohort=r25.COMPETITION, stage=game['stage'], date=game['date'],
               home=game['home'], away=game['away'], actual=list(actual), line=line,
               source_sha256=game['source_sha256'], window=window)
    if any(row[k] != r7_ref[k] or (r25_ref is not None and row[k] != r25_ref[k]) for k in IDENTITY):
        raise ValueError('R26 comparator fixture, score or line differs: ' + game['id'])
    original, market = (r7_ref['forecast'][k] for k in ('candidate', 'market'))
    full = adjust(prior, design, beta, 1.)
    chosen = full if dose == 1. else adjust(prior, design, beta, dose)
    matrices = dict(r7=prior, candidate=chosen, full_dose=full)
    picks = {name: selection(matrix, prior, original) for name, matrix in matrices.items()}
    distribution = {name: totals(matrix) for name, matrix in matrices.items()}
    row.update(forecast=dict(picks, market=market),
               quality={name: quality(matrix, picks[name], actual) for name, matrix in matrices.items()},
               prior_team_exposure=exposure, total_features=design.tolist(),
               expected_goals={name: float(np.arange(len(p)) @ p) for name, p in distribution.items()},
               probability_three_plus={name: float(p[3:].sum()) for name, p in distribution.items()})
    if r25_ref is not None:
        if (r25_ref['forecast']['r7'], r25_ref['forecast']['market']) != (original, market):
            raise ValueError('R25 original choices differ: ' + game['id'])
        row['forecast']['r25'] = r25_ref['forecast']['candidate']
    return row


def summarize(rows):
    pairs = {arm: {other: report.paired(rows, other, arm) for other in ('r7', 'r25', 'market')}
             for arm in ('candidate', 'full_dose')}
    for comparisons in pairs.values():
        for comparison in comparisons.values():
            for metric in comparison.values():
                metric['gained'] = (metric['disagreements'] + metric['net']) // 2
                metric['lost'] = metric['disagreements'] - metric['gained']
    goals = [sum(row['actual']) for row in rows]
    pace = {arm: dict(mean_expected_goals=float(np.mean([r['expected_goals'][arm] for r in rows])),
                      expected_three_plus=sum(r['probability_three_plus'][arm] for r in rows))
            for arm in ('r7', 'candidate', 'full_dose')}
    return dict(fixtures=len(rows), scores={arm: report.rates(rows, arm) for arm in rows[0]['forecast']},
                paired=pairs, actual_mean_goals=float(np.mean(goals)),
                actual_three_plus=sum(goals_value >= 3 for goals_value in goals), goal_totals=pace,
                both_team_histories=sum(min(r['prior_team_exposure']) > 0 for r in rows),
                changed_firsts=sum(r['forecast']['r7']['primary'] != r['forecast']['candidate']['primary'] for r in rows))


def run(data, out):
    protocol, shared = control.read(PROTOCOL), control.read(r25.PROTOCOL)
    spec = protocol['r25_comparator']
    comparison = data / 'workspace-updates' / spec['path']
    if sha(comparison) != spec['sha256']:
        raise ValueError('registered R25 evidence changed')
    r25_refs = reference_rows(comparison)
    with tempfile.TemporaryDirectory(prefix='r26-j1-') as folder:
        root = Path(folder)
        for key in ('r5_panel', 'r7_fit'):
            control.extract(r25.locked_path(data, shared, key), root / key)
        panel = root / 'r5_panel/panel.jsonl'
        saved = root / 'r7_fit/main'
        settings = control.read(saved / 'fit.json')
        if (settings['competition'], settings['source_panel_sha256']) != (r25.COMPETITION, sha(panel)):
            raise ValueError('J1 source panel differs from the R7 fit')
        games = study.check_panel(panel, r25.COMPETITION)
        refs = reference_rows(saved / 'forecasts.jsonl.gz')
        forward_hash = sha(saved / 'forward.json')
    windows = r25.match_rows(games, shared)
    anchors, r7_design, _ = r7.inputs(games, r25.COMPETITION)
    design, exposure, state = history(games, anchors)
    scores = np.array([(g['home_goals'], g['away_goals']) for g in games])
    training = windows['train']
    beta = fit(anchors[training], design[training], scores[training])
    indices = windows['validation'] + windows[r25.HISTORY[0]] + windows[r25.HISTORY[1]]
    priors = dict(zip(indices, control.matrices(anchors[indices], r7_design[indices], settings), strict=True))

    def rows(window, dose):
        return [evaluated(games[i], priors[i], design[i], exposure[i], beta, dose, window,
                          refs[games[i]['id']], None if window == 'validation' else r25_refs[games[i]['id']])
                for i in windows[window]]

    trials = {}
    for dose in r25.DOSES:
        stats = report.rates(rows('validation', dose), 'candidate')
        trials[dose] = dict(first=stats['counts']['score_top1'][0], three=stats['counts']['score_top3'][0],
                            log_loss=stats['score_log_loss'])
    legal = [d for d, s in trials.items() if s['first'] >= trials[0.]['first'] and s['three'] >= trials[0.]['three']]
    chosen = min(legal, key=lambda d: (-trials[d]['first'], -trials[d]['three'], trials[d]['log_loss'], d))
    result = {window: rows(window, chosen) for window in r25.HISTORY}
    combined = result[r25.HISTORY[0]] + result[r25.HISTORY[1]]
    metrics = {window: summarize(group) for window, group in result.items()}
    metrics['combined'] = summarize(combined)
    both = metrics['combined']['paired']['candidate']['r7']
    deltas = [both[key]['net'] for key in ('score_top1', 'score_top3')]
    eligible = chosen > 0 and min(deltas) >= 0 and max(deltas) > 0

    out.mkdir(parents=True, exist_ok=False)
    fit_path = out / 'fit.json'
    write_new(fit_path, dict(version=protocol['version'], competition=r25.COMPETITION, stage=r25.STAGE,
              label=protocol['label'], protocol_sha256=sha(PROTOCOL), windows=shared['windows'], beta=beta,
              validation_trials=trials, selected_dose=chosen, prospective_screen_passed=eligible))
    write_new(out / 'metrics.json', metrics)
    archive.put(out / 'validation.jsonl.gz', rows('validation', chosen))
    archive.put(out / 'forecasts.jsonl.gz', combined)
    if eligible:
        refit = training + indices
        write_new(out / 'forward.json', dict(version=protocol['version'], through=games[refit[-1]]['date'],
                  beta=fit(anchors[refit], design[refit], scores[refit]), dose=chosen, state=state,
                  r7_sha256=forward_hash, fit_sha256=sha(fit_path)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    run(args.data, args.out)
