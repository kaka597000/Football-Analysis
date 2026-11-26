"""Compare high-total calibration and a guarded third score in the European league phases."""

import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.special import expit

from . import all_scope_model as base
from . import continuation_study as archive
from . import europe_score_dispersion as locked_grids
from . import r8_score_study as control
from .goal_state_study import sha, write_new
from .paired_kpi_inference import compare
from .r9_team_shape import published_rank
from .ucl_zero_goals import IDENTITY, reference_rows


PROTOCOL = Path(__file__).resolve().parents[2] / 'research/20261126-r36-europe-three-goal-coverage/protocol.json'
TOTAL = base.H + base.A
HIGH = TOTAL >= 3
BUCKETS = base.RESULT * 2 + HIGH
ARMS = ('third_coverage', 'half_rank', 'full_rank')
SCORE_KEYS = ('score_top1', 'score_top3')


def groups(matrix):
    return np.bincount(BUCKETS.ravel(), weights=matrix.ravel(), minlength=6).reshape(3, 2)


def binary_offset(offsets, observed):
    def loss(beta):
        logits = offsets + beta
        return (np.logaddexp(0., logits) - observed * logits).sum() + 8. * beta**2

    result = minimize_scalar(loss, bounds=(-1.25, 1.25), method='bounded', options={'xatol': 1e-10})
    if not result.success:
        raise ValueError('total-score calibration failed: ' + result.message)
    return round(float(result.x), 6)


def fit(grids, actual):
    home, away = np.asarray(actual).T
    wdl = base.RESULT[home, away]
    high = home + away >= 3
    mass = np.array([groups(grid)[result] for grid, result in zip(grids, wdl, strict=True)])
    offsets = np.log(mass[:, 1] / mass[:, 0])
    win, draw = [binary_offset(offsets[where], high[where]) for where in (wdl != 1, wdl == 1)]
    return np.array([win, draw, win])


def adjust(grid, offsets):
    if not np.any(offsets):
        return grid
    mass = groups(grid)
    conditional_high = expit(np.log(mass[:, 1] / mass[:, 0]) + offsets)
    target = mass.sum(axis=1)[:, None] * np.column_stack((1 - conditional_high, conditional_high))
    updated = grid * (target / mass).ravel()[BUCKETS]
    if not np.allclose(groups(updated).sum(axis=1), mass.sum(axis=1), rtol=0., atol=2e-12):
        raise ValueError('totals calibration changed the signed WDL masses')
    return updated


def high_third(original, adjusted, ordered, *, target_mask=HIGH):
    old = [tuple(map(int, score.split('-'))) for score in ordered]
    past_low = {base.RESULT[score] for score in old[:2] if not HIGH[score]}
    past_high = {base.RESULT[score] for score in old if target_mask[score]}
    mass = groups(adjusted)
    result_mass = mass.sum(axis=1)
    relevant = past_low - past_high
    viable = {r for r in relevant if result_mass[r] >= .30 and mass[r, 1] / result_mass[r] >= .60}
    ranked = np.argsort(-original.ravel(), kind='stable')[3:12]
    candidates = zip(*np.unravel_index(ranked, original.shape), strict=True)
    options = [score for score in candidates if target_mask[score] and base.RESULT[score] in viable
               and adjusted[score] >= adjusted[old[2]] / 2]
    if not options:
        return ordered
    home, away = max(options, key=lambda score: adjusted[score])
    return [*ordered[:2], f'{home}-{away}']


def record(ref, grid, offsets, *, calibrate=adjust, select_third=high_third):
    original = ref['forecast']['candidate']
    ordered = original['top3']
    rankable = base.top_three_scores(grid) == ordered
    half, full = (calibrate(grid, offsets*dose) if rankable else grid for dose in (.5, 1.))
    forecasts = dict(r13=original, r7=ref['forecast']['r7'], market=ref['forecast']['market'])
    three = select_third(grid, full, ordered) if rankable else ordered
    for arm, scores in zip(ARMS, (three, ordered if half is grid else base.top_three_scores(half),
                                 ordered if full is grid else base.top_three_scores(full)), strict=True):
        forecasts[arm] = dict(original, primary=scores[0], top3=scores)
    actual = tuple(ref['actual'])
    outcome = base.RESULT[actual]
    statistics = {}
    for arm, matrix in zip(('r13', 'half_rank', 'full_rank'), (grid, half, full), strict=True):
        partition = groups(matrix)
        statistics[arm] = dict(high=round(float(partition[:, 1].sum()), 12),
            high_given_observed_outcome=round(float(partition[outcome, 1] / partition[outcome].sum()), 12),
            exactly_three=round(float(matrix[TOTAL == 3].sum()), 12),
            mean_goals=round(float(np.sum(TOTAL * matrix)), 12))
    return dict({key: ref[key] for key in IDENTITY}, forecast=forecasts, rankable=rankable, probabilities=statistics,
                original_actual_rank=published_rank(grid, actual, ordered), actual_three_or_more=bool(HIGH[actual]))


def calibration(rows):
    high = np.array([r['actual_three_or_more'] for r in rows])
    exactly_three = np.array([sum(r['actual']) == 3 for r in rows])
    report = {}
    for arm in ('r13', 'half_rank', 'full_rank'):
        values = [row['probabilities'][arm] for row in rows]
        report[arm] = dict(mean_goals=round(float(np.mean([x['mean_goals'] for x in values])), 8))
        for item, labels in (('high', high), ('high_given_observed_outcome', high), ('exactly_three', exactly_three)):
            predicted = np.array([x[item] for x in values])
            report[arm][item] = dict(expected_count=round(float(predicted.sum()), 8),
                                     brier=round(float(np.mean((predicted - labels)**2)), 8))
    return dict(fixtures=len(rows), rankable=sum(r['rankable'] for r in rows), observed_three_plus=int(high.sum()),
                observed_exactly_three=int(exactly_three.sum()),
                observed_average_goals=round(float(np.mean([sum(r['actual']) for r in rows])), 8), forecasts=report)


def changes(rows, arm):
    return dict(first=sum(r['forecast'][arm]['primary'] != r['forecast']['r13']['primary'] for r in rows),
                three=sum(set(r['forecast'][arm]['top3']) != set(r['forecast']['r13']['top3']) for r in rows))


def grade(folds, offsets):
    validation = folds['validation']
    trials = {arm: compare(validation, baseline='r13', candidate=arm)['paired'] for arm in ARMS}
    gains = {arm: tuple(trial[k]['net_hits'] for k in SCORE_KEYS) for arm, trial in trials.items()}
    passing = [arm for arm in ARMS if min(gains[arm]) >= 0 and max(gains[arm]) > 0]
    selected = min(passing, key=lambda arm: (-gains[arm][0], -gains[arm][1], ARMS.index(arm)), default='r13')
    evaluation = folds['evaluation']
    chosen = [dict(row, forecast=dict(row['forecast'], selected=row['forecast'][selected])) for row in evaluation]
    paired = compare(chosen, baseline='r13', candidate='selected')
    test_gains = [paired['paired'][k]['net_hits'] for k in SCORE_KEYS]
    return dict(offsets=dict(win=float(offsets[0]), draw=float(offsets[1])), selected=selected,
                calibration={key: calibration(rows) for key, rows in folds.items()},
                validation={arm: dict(net_hits=gains[arm], changed=changes(validation, arm)) for arm in ARMS},
                evaluation=paired, market=compare(chosen, baseline='market', candidate='selected'),
                alternatives={arm: compare(evaluation, baseline='r13', candidate=arm) for arm in ARMS},
                evaluation_changes={arm: changes(evaluation, arm) for arm in ARMS},
                forward_screen_passed=selected != 'r13' and min(test_gains) >= 0 and max(test_gains) > 0)


def run(data, out, *, protocol=PROTOCOL, fit_offsets=fit, forecast_row=record):
    policy = control.read(protocol)
    if sha(locked_grids.PROTOCOL) != policy['r30_protocol_sha256']:
        raise ValueError('locked source dimensions, archives or dates differ')
    source = control.read(locked_grids.PROTOCOL)
    ledger = {tag: [] for tag in source['windows']}
    results = {}
    with TemporaryDirectory(prefix='europe-three-goal-') as directory:
        work = Path(directory)
        locked_grids.unpack(data, source['archives']['r13'], work/'r13')
        for code in source['competitions']:
            folder, signed = work/code, work/'r13'/code
            for item in ('panel', 'r7'):
                locked_grids.unpack(data, source['archives'][code][item], folder/item)
            refs, grids = locked_grids.original_grids(folder/'panel/panel.jsonl', control.read(folder/'r7/main/fit.json'),
                control.read(signed/'fit.json'), reference_rows(signed/'forecasts.jsonl.gz'), code, source)
            windows = {tag: [i for i, row in enumerate(refs) if span['from'] <= row['date'] < span['before']]
                       for tag, span in source['windows'].items()}
            if any(len(indices) != source['expected_per_window'][code] for indices in windows.values()):
                raise ValueError('incomplete signed fixture window for ' + code)
            train = windows['train']
            offsets = fit_offsets(grids[train], [refs[i]['actual'] for i in train])
            folds = {tag: [forecast_row(refs[i], grids[i], offsets) for i in indices] for tag, indices in windows.items()}
            results[code] = grade(folds, offsets)
            for tag, rows in folds.items():
                ledger[tag].extend(rows)
    out.mkdir(parents=True, exist_ok=False)
    write_new(out/'summary.json', dict(version=policy['version'], protocol_sha256=sha(protocol), competitions=results))
    for tag, rows in ledger.items():
        archive.put(out/f'{tag}.jsonl.gz', rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    run(args.data, args.out)
