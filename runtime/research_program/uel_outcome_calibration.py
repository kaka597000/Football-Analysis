"""Calibrate the R13 Europa League outcome head and replay exact-score selection."""

import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
from scipy.optimize import minimize
from scipy.special import logsumexp, softmax

from . import all_scope_model as base
from . import continuation_study as archive
from . import europe_score_dispersion as r30
from . import r8_score_study as control
from .goal_state_study import sha, write_new
from .paired_kpi_inference import compare
from .r9_team_shape import published_rank
from .ucl_zero_goals import IDENTITY, reference_rows


PROTOCOL = Path(__file__).resolve().parents[2] / 'research/20261126-r31-uel-outcome-calibration/protocol.json'
DOSES = {'r13': 0., 'half': .5, 'full': 1.}
TARGETS = ('score_top1', 'score_top3', 'wdl')


def logits(log_priors, coefficients):
    multiplier, home, draw = coefficients
    return (1. + multiplier) * log_priors + [home, draw, 0.]


def fit(priors, outcomes):
    log_priors = np.log(priors)
    index = np.arange(len(outcomes))
    labels = np.eye(3)[outcomes]

    def objective(coefficients):
        weighted = logits(log_priors, coefficients)
        residual = softmax(weighted, axis=1) - labels
        loss = (logsumexp(weighted, axis=1) - weighted[index, outcomes]).sum()
        gradient = np.r_[(residual * log_priors).sum(), residual.sum(axis=0)[:2]]
        return loss + 6. * (coefficients @ coefficients), gradient + 12. * coefficients

    result = minimize(objective, np.zeros(3), jac=True, method='L-BFGS-B',
                      bounds=[(-.6, .6), (-.65, .65), (-.65, .65)],
                      options={'ftol': 1e-13, 'gtol': 1e-9})
    if not result.success:
        raise ValueError('outcome likelihood fit failed: ' + result.message)
    return [round(float(v), 6) for v in result.x]


def reweight(matrix, original, updated):
    ratio = (updated / original)[base.RESULT]
    new = matrix * ratio
    return new / new.sum()


def evaluate(ref, matrix, coefficients):
    prior = np.asarray(ref['quality']['candidate']['wdl'])
    calibrated = softmax(logits(np.log(prior), coefficients))
    base_pick = ref['forecast']['candidate']
    forecast, quality = {'market': ref['forecast']['market']}, {}
    actual = tuple(ref['actual'])
    outcome = base.RESULT[actual]
    cells = matrix.ravel()
    mass = np.bincount(r30.RESULT, weights=cells, minlength=3)[r30.RESULT]
    original_conditional = cells / mass
    for name, dose in DOSES.items():
        probabilities = prior if not dose else (1. - dose) * prior + dose * calibrated
        revised = reweight(matrix, prior, probabilities) if dose else matrix
        ordered = base.top_three_scores(revised) if dose else base_pick['top3']
        if dose:
            direction = base.SIDES[int(np.argmax(probabilities))]
        else:
            direction = base_pick['wdl']
        forecast[name] = dict(base_pick, primary=ordered[0], top3=ordered, wdl=direction)
        new_cells = revised.ravel()
        new_mass = np.bincount(r30.RESULT, weights=new_cells, minlength=3)[r30.RESULT]
        error = float(np.max(abs(original_conditional - new_cells / new_mass)))
        if error > 2e-12:
            raise ValueError('calibration changed probabilities within an outcome')
        quality[name] = dict(wdl=[round(float(p), 12) for p in probabilities],
                             wdl_log_loss=round(float(-np.log(probabilities[outcome])), 12),
                             score_log_loss=round(float(-np.log(revised[actual])), 12),
                             max_conditional_error=round(error, 14))
    return dict({key: ref[key] for key in IDENTITY}, forecast=forecast, quality=quality,
                original_rank=published_rank(matrix, actual, base_pick['top3']))


def distant_scores(rows):
    distant = [r for r in rows if r['original_rank'] >= 7]
    by_arm = {}
    for arm in DOSES:
        coverage, direction_hits = 0, 0
        for row in distant:
            realized = base.RESULT[tuple(row['actual'])]
            forecast = row['forecast'][arm]
            showed = {base.RESULT[tuple(map(int, value.split('-')))] for value in forecast['top3']}
            coverage += int(realized in showed)
            direction_hits += int(base.SIDES[realized] == forecast['wdl'])
        by_arm[arm] = dict(shown_outcome=coverage, correct_wdl=direction_hits)
    return dict(fixtures=len(distant), by_arm=by_arm)


def run(data, out):
    spec = control.read(PROTOCOL)
    if sha(r30.PROTOCOL) != spec['r30_protocol_sha256']:
        raise ValueError('R30 source registration differs')
    locked = control.read(r30.PROTOCOL)
    code = spec['competition']
    with TemporaryDirectory(prefix='uel-calibration-') as folder:
        temp = Path(folder)
        r30.unpack(data, locked['archives']['r13'], temp/'r13')
        for key in ('panel', 'r7'):
            r30.unpack(data, locked['archives'][code][key], temp/key)
        original = temp/'r13'/code
        rows, grids = r30.original_grids(temp/'panel/panel.jsonl', control.read(temp/'r7/main/fit.json'),
                                         control.read(original/'fit.json'),
                                         reference_rows(original/'forecasts.jsonl.gz'), code, locked)
    windows = {tag: [i for i, row in enumerate(rows) if window['from'] <= row['date'] < window['before']]
               for tag, window in locked['windows'].items()}
    if any(len(group) != locked['expected_per_window'][code] for group in windows.values()):
        raise ValueError('incorrect original Europa League window size')
    priors = np.array([r['quality']['candidate']['wdl'] for r in rows])
    if (not np.all(np.isfinite(priors)) or np.any(priors <= 0.)
            or not np.allclose(priors.sum(axis=1), 1., rtol=0., atol=1e-12)):
        raise ValueError('invalid archived R13 WDL probabilities')
    train = windows['train']
    outcomes = np.array([base.RESULT[tuple(rows[i]['actual'])] for i in train])
    coefficients = fit(priors[train], outcomes)
    validation = [evaluate(rows[i], grids[i], coefficients) for i in windows['validation']]
    trials = {}
    for name in DOSES:
        scores = compare(validation, baseline='market', candidate=name)['tracks'][name]['metrics']
        trials[name] = {key: scores[key]['hits'] for key in TARGETS}
        trials[name]['wdl_log_loss'] = round(float(np.mean([row['quality'][name]['wdl_log_loss'] for row in validation])), 12)
    keep = [name for name, values in trials.items() if all(values[k] >= trials['r13'][k] for k in TARGETS)]
    selected = min(keep, key=lambda name: (*(-trials[name][key] for key in TARGETS),
                                           trials[name]['wdl_log_loss'], DOSES[name]))
    test = [evaluate(rows[i], grids[i], coefficients) for i in windows['evaluation']]
    metrics = {name: compare(test, baseline='r13', candidate=name) for name in ('half', 'full')}
    chosen = [dict(row, forecast=dict(row['forecast'], selected=row['forecast'][selected])) for row in test]
    paired = compare(chosen, baseline='r13', candidate='selected')
    gains = [paired['paired'][metric]['net_hits'] for metric in TARGETS]
    changed = {arm: dict(first=sum(row['forecast'][arm]['primary'] != row['forecast']['r13']['primary'] for row in test),
                         three=sum(set(row['forecast'][arm]['top3']) != set(row['forecast']['r13']['top3']) for row in test),
                         wdl=sum(row['forecast'][arm]['wdl'] != row['forecast']['r13']['wdl'] for row in test))
               for arm in DOSES}
    out.mkdir(parents=True, exist_ok=False)
    write_new(out/'summary.json', dict(version=spec['version'], protocol_sha256=sha(PROTOCOL), coefficients=coefficients,
              competition=code, stage=spec['stage'], fixtures={k: len(v) for k,v in windows.items()},
              validation_trials=trials, selected=selected, evaluation=paired, evaluation_changes=changed,
              ablations=metrics, distant_scores=distant_scores(test),
              against_market=compare(chosen, baseline='market', candidate='selected'),
              forward_screen_passed=DOSES[selected] > 0. and min(gains) >= 0 and max(gains[:2]) > 0))
    archive.put(out/'validation.jsonl.gz', validation)
    archive.put(out/'evaluation.jsonl.gz', test)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    run(args.data, args.out)
