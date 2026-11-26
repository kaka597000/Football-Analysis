"""Calibrate J1 exact scores while retaining the R7 outcome and total-goals marginals."""

import argparse
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

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


PROTOCOL = Path(__file__).resolve().parents[2] / 'research/20261126-r27-j1-score-calibration/protocol.json'
OUTCOME = base.RESULT.ravel()
CATEGORY = np.select([(base.H == 1) & (base.A == 1),
                      (np.minimum(base.H, base.A) == 0) & (base.H != base.A),
                      (np.minimum(base.H, base.A) > 0) & (abs(base.H - base.A) == 1)],
                     [1, 2, 3], default=0).ravel()
GROUPS = 2 * base.SIZE - 1, 3, 4
BUCKET = (TOTAL * GROUPS[1] + OUTCOME) * GROUPS[2] + CATEGORY
BUCKET_COUNT = int(np.prod(GROUPS))
TOLERANCE = 2e-13
DOSE_ARMS = {0.: 'r7', .5: 'half_dose', 1.: 'full_dose'}


def bucket_masses(matrices):
    return np.array([np.bincount(BUCKET, weights=matrix.ravel(), minlength=BUCKET_COUNT)
                     for matrix in matrices]).reshape(-1, *GROUPS)


def ratio(target, current):
    return np.divide(target, current, out=np.ones_like(current), where=current > 0.)


def raking_factors(buckets, multipliers):
    original = buckets.sum(axis=-1)
    weighted = buckets @ multipliers
    balanced = weighted.copy()
    goal_totals = original.sum(axis=2, keepdims=True)
    outcomes = original.sum(axis=1, keepdims=True)
    for _ in range(1000):
        balanced *= ratio(outcomes, balanced.sum(axis=1, keepdims=True))
        balanced *= ratio(goal_totals, balanced.sum(axis=2, keepdims=True))
        if np.max(abs(balanced.sum(axis=1, keepdims=True) - outcomes)) <= TOLERANCE:
            return ratio(balanced, weighted)
    raise ValueError('exact-score projection did not converge')


def fit(matrices, scores):
    """Fit the calibration factors using date-causal prior predictions."""
    if np.any((scores < 0) | (scores >= base.SIZE)) or len(matrices) != len(scores):
        raise ValueError('training score outside the exact-score support')
    home, away = scores.T
    rows = np.arange(len(scores))
    if np.any(matrices[rows, home, away] <= 0.):
        raise ValueError('training score has zero prior probability')
    cells = home * base.SIZE + away
    buckets = bucket_masses(matrices)

    def loss(beta):
        multipliers = np.exp(np.r_[0., beta])
        factors = raking_factors(buckets, multipliers)
        changes = factors[rows, TOTAL[cells], OUTCOME[cells]] * multipliers[CATEGORY[cells]]
        return -np.log(changes).sum() + 16. * (beta @ beta)

    result = minimize(loss, np.zeros(3), jac='3-point', method='L-BFGS-B',
                      bounds=[(-.65, .65)] * 3, options={'ftol': 1e-11})
    if not result.success:
        raise ValueError('J1 exact-score calibration failed: ' + str(result.message))
    return result.x


def adjust(prior, beta, dose):
    """Apply a fixed calibration to a normalized R7 score grid."""
    if dose not in DOSE_ARMS:
        raise ValueError('unregistered J1 calibration dose')
    if dose == 0. or not np.any(beta):
        return prior
    multipliers = np.exp(np.r_[0., beta])
    factors = raking_factors(bucket_masses([prior]), multipliers)
    changed = prior.ravel() * factors[0, TOTAL, OUTCOME] * multipliers[CATEGORY]
    changed = changed.reshape(prior.shape)
    return changed if dose == 1. else (1. - dose) * prior + dose * changed


def evaluate(game, prior, beta, window, reference):
    actual = game['home_goals'], game['away_goals']
    row = dict(id=game['id'], cohort=r25.COMPETITION, stage=game['stage'], date=game['date'],
               home=game['home'], away=game['away'], actual=list(actual), line=base.actual_line(game),
               source_sha256=game['source_sha256'], window=window)
    if any(row[key] != reference[key] for key in IDENTITY):
        raise ValueError('R7 fixture, result or line differs: ' + game['id'])
    original, market = (reference['forecast'][arm] for arm in ('candidate', 'market'))
    full = adjust(prior, beta, 1.)
    halfway = prior if full is prior else .5 * (prior + full)
    matrices = dict(r7=prior, half_dose=halfway, full_dose=full)
    picks = {name: selection(matrix, prior, original) for name, matrix in matrices.items()}
    probabilities = {name: quality(matrix, picks[name], actual) for name, matrix in matrices.items()}
    return dict(row, forecast=dict(picks, market=market), quality=probabilities,
                max_total_error=float(np.max(abs(totals(full) - totals(prior)))),
                max_wdl_error=float(np.max(abs(np.array(probabilities['full_dose']['wdl']) -
                                              probabilities['r7']['wdl']))))


def summarize(rows):
    arms = rows[0]['forecast']
    comparisons = {arm: {ref: report.paired(rows, ref, arm) for ref in ('r7', 'market')}
                   for arm in ('candidate', 'half_dose', 'full_dose')}
    for group in comparisons.values():
        for metrics in group.values():
            for item in metrics.values():
                item['gained'] = (item['disagreements'] + item['net']) // 2
                item['lost'] = item['disagreements'] - item['gained']
    return dict(fixtures=len(rows), scores={arm: report.rates(rows, arm) for arm in arms}, paired=comparisons,
                first_frequency={arm: dict(Counter(r['forecast'][arm]['primary'] for r in rows))
                                 for arm in ('r7', 'candidate', 'half_dose', 'full_dose')},
                max_total_error=max(r['max_total_error'] for r in rows),
                max_wdl_error=max(r['max_wdl_error'] for r in rows))


def replay(data, out):
    protocol = control.read(PROTOCOL)
    shared = control.read(r25.PROTOCOL)
    with TemporaryDirectory(prefix='r27-j1-') as folder:
        root = Path(folder)
        for source in ('r5_panel', 'r7_fit'):
            control.extract(r25.locked_path(data, shared, source), root / source)
        panel = root / 'r5_panel/panel.jsonl'
        r7_root = root / 'r7_fit/main'
        settings = control.read(r7_root / 'fit.json')
        if (settings['competition'], settings['source_panel_sha256']) != (r25.COMPETITION, sha(panel)):
            raise ValueError('J1 source panel differs from the published R7 fit')
        games = study.check_panel(panel, r25.COMPETITION)
        references = reference_rows(r7_root / 'forecasts.jsonl.gz')

    windows = r25.match_rows(games, shared)
    anchors, features, _ = r7.inputs(games, r25.COMPETITION)
    observed = np.array([(game['home_goals'], game['away_goals']) for game in games])
    training = windows['train']
    beta = fit(anchors[training], observed[training])
    names = ('validation', *r25.HISTORY)
    indices = [i for name in names for i in windows[name]]
    priors = dict(zip(indices, control.matrices(anchors[indices], features[indices], settings), strict=True))

    def rows(name):
        return [evaluate(games[i], priors[i], beta, name, references[games[i]['id']]) for i in windows[name]]

    validation = rows('validation')
    trials = {}
    for dose, arm in DOSE_ARMS.items():
        stats = report.rates(validation, arm)
        trials[dose] = dict(first=stats['counts']['score_top1'][0], three=stats['counts']['score_top3'][0],
                            log_loss=stats['score_log_loss'])
    eligible = [d for d, v in trials.items() if v['first'] >= trials[0.]['first']
                and v['three'] >= trials[0.]['three']]
    selected = min(eligible, key=lambda d: (-trials[d]['first'], -trials[d]['three'], trials[d]['log_loss'], d))
    result = {name: rows(name) for name in r25.HISTORY}
    combined = [row for name in r25.HISTORY for row in result[name]]
    for row in validation + combined:
        for field in ('forecast', 'quality'):
            row[field]['candidate'] = row[field][DOSE_ARMS[selected]]
    metrics = {name: summarize(group) for name, group in result.items()}
    metrics['combined'] = summarize(combined)
    pair = metrics['combined']['paired']['candidate']['r7']
    net = [pair[k]['net'] for k in ('score_top1', 'score_top3')]
    screen = selected > 0. and min(net) >= 0 and max(net) > 0

    out.mkdir(parents=True, exist_ok=False)
    write_new(out / 'fit.json', dict(version=protocol['version'], competition=r25.COMPETITION, label=protocol['label'],
              protocol_sha256=sha(PROTOCOL), windows=shared['windows'], beta=beta.tolist(), validation_trials=trials,
              selected_dose=selected, prospective_screen_passed=screen))
    write_new(out / 'metrics.json', metrics)
    archive.put(out / 'validation.jsonl.gz', validation)
    archive.put(out / 'forecasts.jsonl.gz', combined)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    replay(args.data, args.out)
