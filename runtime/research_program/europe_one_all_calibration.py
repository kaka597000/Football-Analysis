"""Diagnose and test a targeted 1-1 calibration in two European league phases."""

import argparse
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.special import logit

from . import all_scope_model as base
from . import continuation_study as archive
from . import europe_score_dispersion as r30
from . import r8_score_study as control
from .goal_state_study import sha, write_new
from .paired_kpi_inference import compare
from .ucl_zero_goals import IDENTITY, reference_rows


PROTOCOL = Path(__file__).resolve().parents[2] / 'research/20261126-r32-europe-one-all/protocol.json'
ONE_ALL = (1, 1)
DOSES = {'r13': 0., 'half': .5, 'full': 1.}
SCORE_METRICS = ('score_top1', 'score_top3')


def eligibility(original, matrix):
    if original['primary'] != '1-1':
        return 'other_first_score'
    if original['wdl'] not in ('home', 'away'):
        return 'no_independent_win_preference'
    if base.top_three_scores(matrix) != original['top3']:
        return 'original_order_differs_from_matrix'
    return 'eligible'


def fit(probabilities, labels):
    if not len(probabilities):
        return 0.
    if np.any(~np.isfinite(probabilities) | (probabilities <= 0.) | (probabilities >= 1.)):
        raise ValueError('a finite interior probability is required for each eligible score')
    original = logit(probabilities)

    def objective(offset):
        adjusted = original + offset
        return (np.logaddexp(0., adjusted) - labels * adjusted).sum() + 4. * offset**2

    result = minimize_scalar(objective, method='bounded', bounds=(-1.5, 1.5), options={'xatol': 1e-10})
    if not result.success:
        raise ValueError('exact-score calibration failed: ' + result.message)
    return round(float(result.x), 6)


def adjust(prior, offset):
    if not offset:
        return prior
    matrix = prior.copy()
    matrix[ONE_ALL] *= np.exp(offset)
    return matrix / matrix.sum()


def direction(score):
    home, away = score
    return 'home' if home > away else 'away' if home < away else 'draw'


def record(ref, matrix, state, coefficient):
    original = ref['forecast']['candidate']
    forecast = {'market': ref['forecast']['market']}
    actual = tuple(ref['actual'])
    actual_direction = direction(actual)
    first_three = [tuple(map(int, score.split('-'))) for score in original['top3']]
    min_error = min(abs(home - actual[0]) + abs(away - actual[1]) for home, away in first_three)
    outcome_in_three = any(direction(score) == actual_direction for score in first_three)
    probability, log_loss = {}, {}
    for name, dose in DOSES.items():
        updated = adjust(matrix, dose * coefficient) if state == 'eligible' else matrix
        if updated is matrix:
            forecast[name] = original
        else:
            ordered = base.top_three_scores(updated)
            forecast[name] = dict(original, primary=ordered[0], top3=ordered)
        probability[name] = round(float(updated[ONE_ALL]), 12)
        log_loss[name] = round(float(-np.log(max(1e-300, updated[actual]))), 12)
    return dict({key: ref[key] for key in IDENTITY}, forecast=forecast, eligibility=state,
                original_first_exact=first_three[0] == actual, original_three_exact=min_error == 0,
                actual_outcome=actual_direction, actual_outcome_in_three=outcome_in_three, minimum_goal_error=min_error,
                one_all_probability=probability, log_loss=log_loss)


def one_all_group(rows):
    return dict(fixtures=len(rows), actual_one_all=sum(tuple(row['actual']) == ONE_ALL for row in rows),
                expected_one_all=round(sum(row['one_all_probability']['r13'] for row in rows), 6),
                realized_outcomes={outcome: sum(row['actual_outcome'] == outcome for row in rows)
                                   for outcome in ('home', 'draw', 'away')},
                first_miss_with_actual_outcome_in_three=sum(not row['original_first_exact']
                    and row['actual_outcome_in_three'] for row in rows))


def diagnose(rows):
    misses = [row for row in rows if not row['original_three_exact']]
    firsts = [row for row in rows if row['forecast']['r13']['primary'] == '1-1']
    return dict(fixtures=len(rows), first_hits=sum(row['original_first_exact'] for row in rows),
                three_hits=len(rows) - len(misses),
                first_one_all=one_all_group(firsts),
                eligible_one_all=one_all_group([row for row in firsts if row['eligibility'] == 'eligible']),
                eligibility=dict(Counter(row['eligibility'] for row in rows)),
                three_misses=dict(actual_outcome_in_three=sum(row['actual_outcome_in_three'] for row in misses),
                    actual_outcome_omitted=sum(not row['actual_outcome_in_three'] for row in misses),
                    one_goal_away=sum(row['minimum_goal_error'] == 1 for row in misses),
                    two_or_more_goals_away=sum(row['minimum_goal_error'] >= 2 for row in misses)))


def choose(validation):
    trials = {}
    for arm in DOSES:
        metrics = compare(validation, baseline='market', candidate=arm)['tracks'][arm]['metrics']
        trials[arm] = {metric: metrics[metric]['hits'] for metric in SCORE_METRICS}
        trials[arm]['mean_log_loss'] = round(float(np.mean([row['log_loss'][arm] for row in validation])), 12)
    admitted = [arm for arm, trial in trials.items() if all(trial[m] >= trials['r13'][m] for m in SCORE_METRICS)]
    selected = min(admitted, key=lambda arm: (-trials[arm]['score_top1'], -trials[arm]['score_top3'],
                                              trials[arm]['mean_log_loss'], DOSES[arm]))
    return selected, trials


def summarize(windows, coefficient):
    selected, trials = choose(windows['validation'])
    test = windows['evaluation']
    chosen = [dict(row, forecast=dict(row['forecast'], selected=row['forecast'][selected])) for row in test]
    paired = compare(chosen, baseline='r13', candidate='selected')
    gains = [paired['paired'][metric]['net_hits'] for metric in SCORE_METRICS]
    return dict(training_offset=coefficient, diagnostics={key: diagnose(rows) for key, rows in windows.items()},
                validation_trials=trials, selected=selected, evaluation=paired,
                against_market=compare(chosen, baseline='market', candidate='selected'),
                evaluation_ablations={arm: compare(test, baseline='r13', candidate=arm) for arm in ('half', 'full')},
                changed={arm: dict(first=sum(row['forecast'][arm]['primary'] != row['forecast']['r13']['primary']
                                              for row in test),
                                   three=sum(set(row['forecast'][arm]['top3']) != set(row['forecast']['r13']['top3'])
                                             for row in test)) for arm in DOSES},
                forward_screen_passed=selected != 'r13' and min(gains) >= 0 and max(gains) > 0)


def run(data, out):
    spec = control.read(PROTOCOL)
    if sha(r30.PROTOCOL) != spec['r30_protocol_sha256']:
        raise ValueError('R30 source registration differs')
    locked = control.read(r30.PROTOCOL)
    summaries, ledger = {}, {key: [] for key in locked['windows']}
    with TemporaryDirectory(prefix='europe-one-all-') as folder:
        temp = Path(folder)
        r30.unpack(data, locked['archives']['r13'], temp/'r13')
        for competition in locked['competitions']:
            path = temp/competition
            for key in ('panel', 'r7'):
                r30.unpack(data, locked['archives'][competition][key], path/key)
            original = temp/'r13'/competition
            rows, grids = r30.original_grids(path/'panel/panel.jsonl', control.read(path/'r7/main/fit.json'),
                                             control.read(original/'fit.json'),
                                             reference_rows(original/'forecasts.jsonl.gz'), competition, locked)
            windows = {key: [i for i, row in enumerate(rows) if span['from'] <= row['date'] < span['before']]
                       for key, span in locked['windows'].items()}
            if any(len(indices) != locked['expected_per_window'][competition] for indices in windows.values()):
                raise ValueError('registered window size differs: ' + competition)
            states = [eligibility(row['forecast']['candidate'], grid) for row, grid in zip(rows, grids, strict=True)]
            train = [i for i in windows['train'] if states[i] == 'eligible']
            coefficient = fit(np.array([grids[i][ONE_ALL] for i in train]),
                              np.array([tuple(rows[i]['actual']) == ONE_ALL for i in train]))
            results = {key: [record(rows[i], grids[i], states[i], coefficient) for i in indices]
                       for key, indices in windows.items()}
            summaries[competition] = summarize(results, coefficient)
            for key, values in results.items():
                ledger[key].extend(values)
    out.mkdir(parents=True, exist_ok=False)
    write_new(out/'summary.json', dict(version=spec['version'], protocol_sha256=sha(PROTOCOL),
                                     competitions=summaries))
    for key, rows in ledger.items():
        archive.put(out/f'{key}.jsonl.gz', rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    run(args.data, args.out)
