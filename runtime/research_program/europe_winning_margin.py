"""Replay conditional one-goal-win calibration on two European league phases."""

import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit

from . import all_scope_model as base
from . import continuation_study as archive
from . import europe_score_dispersion as original
from . import r8_score_study as control
from .goal_state_study import sha, write_new
from .paired_kpi_inference import compare
from .ucl_zero_goals import IDENTITY, reference_rows


PROTOCOL = Path(__file__).resolve().parents[2] / 'research/20261126-r34-europe-winning-margin/protocol.json'
MARGIN = base.H - base.A
WIN_CLASSES = np.array([[MARGIN == 1, MARGIN > 1], [MARGIN == -1, MARGIN < -1]])
TOTAL = (base.H + base.A).ravel()
DOSES = {'r13': 0., 'half': .5, 'full': 1.}
SCORE_METRICS = ('score_top1', 'score_top3')


def group_masses(grid):
    return np.einsum('ij,abij->ab', grid, WIN_CLASSES)


def predictors(masses):
    if np.any(masses <= 0) or not np.isfinite(masses).all():
        raise ValueError('nonpositive or invalid winning-margin probability')
    wins = masses.sum(axis=1)
    return np.log(masses[:, 0] / masses[:, 1]), wins - wins[::-1]


def fit(examples):
    if not examples:
        return np.zeros(2)
    offset, advantage, observed = np.asarray(examples).T
    design = np.column_stack((np.ones(len(offset)), advantage))
    penalty = np.array([8., 16.])

    def objective(coefficients):
        logits = offset + design @ coefficients
        loss = (np.logaddexp(0., logits) - observed * logits).sum()
        gradient = design.T @ (expit(logits) - observed)
        return loss + (penalty * coefficients**2).sum(), gradient + 2 * penalty * coefficients

    optimized = minimize(objective, np.zeros(2), jac=True, method='L-BFGS-B',
                         bounds=[(-1.25, 1.25)] * 2, options={'ftol': 1e-13, 'gtol': 1e-9})
    if not optimized.success:
        raise ValueError('winning-margin calibration failed: ' + optimized.message)
    return np.round(optimized.x, 6)


def adjust(grid, masses, coefficients):
    if not np.any(coefficients):
        return grid
    offset, advantage = predictors(masses)
    probability = expit(offset + coefficients[0] + coefficients[1] * advantage)
    target = masses.sum(axis=1)[:, None] * np.column_stack((probability, 1 - probability))
    weights = 1. + np.einsum('ab,abij->ij', target / masses - 1., WIN_CLASSES)
    return grid * weights


def record(ref, grid, rankable, coefficients):
    pick = ref['forecast']['candidate']
    forecast = {name: ref['forecast'][name] for name in ('market', 'r7')}
    actual = tuple(ref['actual'])
    margin = actual[0] - actual[1]
    masses = group_masses(grid)
    wdl = masses.sum(axis=1)
    totals = np.bincount(TOTAL, weights=grid.ravel())
    probabilities, losses = {}, {}
    max_wdl_error = max_total_delta = 0.
    for name, dose in DOSES.items():
        adjusted = adjust(grid, masses, dose * coefficients) if rankable else grid
        scores = pick['top3'] if adjusted is grid else base.top_three_scores(adjusted)
        forecast[name] = dict(pick, primary=scores[0], top3=scores)
        result = group_masses(adjusted)
        probabilities[name] = round(float(result[:, 0].sum()), 12)
        losses[name] = round(float(-np.log(max(1e-300, adjusted[actual]))), 12)
        new_totals = np.bincount(TOTAL, weights=adjusted.ravel())
        max_total_delta = max(max_total_delta, float(np.max(abs(new_totals - totals))))
        max_wdl_error = max(max_wdl_error, float(np.max(abs(result.sum(axis=1) - wdl))))
        if not np.array_equal(adjusted[MARGIN == 0], grid[MARGIN == 0]):
            raise ValueError('winning-margin adjustment changed a draw score')
    if max_wdl_error > 2e-12:
        raise ValueError('winning-margin adjustment changed a win probability')
    return dict({key: ref[key] for key in IDENTITY}, forecast=forecast, rankable=rankable,
                actual_margin=margin, one_goal_win_probability=probabilities, log_loss=losses,
                wdl_mass_error=round(max_wdl_error, 14), total_mass_delta=round(max_total_delta, 12))


def choose(rows):
    trials = {}
    for arm in DOSES:
        metrics = compare(rows, baseline='market', candidate=arm)['tracks'][arm]['metrics']
        trials[arm] = {metric: metrics[metric]['hits'] for metric in SCORE_METRICS}
        trials[arm]['log_loss'] = round(float(np.mean([row['log_loss'][arm] for row in rows])), 12)
    eligible = [arm for arm, stats in trials.items() if all(stats[m] >= trials['r13'][m] for m in SCORE_METRICS)]
    selected = min(eligible, key=lambda arm: (-trials[arm]['score_top1'], -trials[arm]['score_top3'],
                                              trials[arm]['log_loss'], DOSES[arm]))
    return selected, trials


def report(folds, coefficients, fit_rows):
    selected, trials = choose(folds['validation'])
    evaluation = folds['evaluation']
    chosen = [dict(row, forecast=dict(row['forecast'], selected=row['forecast'][selected])) for row in evaluation]
    paired = compare(chosen, baseline='r13', candidate='selected')
    gains = [paired['paired'][metric]['net_hits'] for metric in SCORE_METRICS]
    diagnostics = {}
    for name, rows in folds.items():
        diagnostics[name] = dict(fixtures=len(rows), rankable=sum(row['rankable'] for row in rows),
            actual_one_goal_wins=sum(abs(row['actual_margin']) == 1 for row in rows),
            expected_one_goal_wins={arm: round(sum(row['one_goal_win_probability'][arm] for row in rows), 6)
                                    for arm in DOSES},
            largest_wdl_mass_error=round(max(row['wdl_mass_error'] for row in rows), 14),
            largest_total_mass_shift=round(max(row['total_mass_delta'] for row in rows), 12))
    return dict(coefficients=dict(zip(('intercept', 'advantage'), coefficients.tolist())),
                training_wins=fit_rows, diagnostics=diagnostics, validation=trials, selected=selected,
                evaluation=paired, controls={arm: compare(chosen, baseline=arm, candidate='selected')
                                             for arm in ('r7', 'market')},
                ablations={arm: compare(evaluation, baseline='r13', candidate=arm) for arm in ('half', 'full')},
                changed={arm: dict(first=sum(row['forecast'][arm]['primary'] != row['forecast']['r13']['primary']
                                              for row in evaluation),
                                   three=sum(set(row['forecast'][arm]['top3']) != set(row['forecast']['r13']['top3'])
                                             for row in evaluation)) for arm in DOSES},
                forward_screen_passed=selected != 'r13' and min(gains) >= 0 and max(gains) > 0)


def replay(data, out):
    protocol = control.read(PROTOCOL)
    if sha(original.PROTOCOL) != protocol['r30_protocol_sha256']:
        raise ValueError('registered European score-grid provenance differs')
    locked = control.read(original.PROTOCOL)
    ledger = {name: [] for name in locked['windows']}
    summaries = {}
    with TemporaryDirectory(prefix='r34-europe-margin-') as folder:
        root = Path(folder)
        original.unpack(data, locked['archives']['r13'], root/'r13')
        for code in locked['competitions']:
            path, signed = root/code, root/'r13'/code
            for item in ('panel', 'r7'):
                original.unpack(data, locked['archives'][code][item], path/item)
            rows, grids = original.original_grids(path/'panel/panel.jsonl', control.read(path/'r7/main/fit.json'),
                control.read(signed/'fit.json'), reference_rows(signed/'forecasts.jsonl.gz'), code, locked)
            rankable = [base.top_three_scores(grid) == row['forecast']['candidate']['top3']
                        for row, grid in zip(rows, grids, strict=True)]
            windows = {name: [i for i, row in enumerate(rows) if span['from'] <= row['date'] < span['before']]
                       for name, span in locked['windows'].items()}
            if any(len(indices) != locked['expected_per_window'][code] for indices in windows.values()):
                raise ValueError('incomplete registered league-phase population: ' + code)
            examples = []
            for i in windows['train']:
                home, away = rows[i]['actual']
                if rankable[i] and home != away:
                    side = int(home < away)
                    offsets, strengths = predictors(group_masses(grids[i]))
                    examples.append((offsets[side], strengths[side], abs(home - away) == 1))
            coefficients = fit(examples)
            folds = {name: [record(rows[i], grids[i], rankable[i], coefficients) for i in indices]
                     for name, indices in windows.items()}
            summaries[code] = report(folds, coefficients, len(examples))
            for name, records in folds.items():
                ledger[name].extend(records)
    out.mkdir(parents=True, exist_ok=False)
    write_new(out/'summary.json', dict(version=protocol['version'], protocol_sha256=sha(PROTOCOL), competitions=summaries))
    for name, rows in ledger.items():
        archive.put(out/f'{name}.jsonl.gz', rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    replay(args.data, args.out)
