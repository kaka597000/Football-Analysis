"""Test outcome-conditional score dispersion in the Europa and Conference league phases."""

import argparse
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
from scipy.optimize import minimize_scalar

from . import all_scope_model as base
from . import all_scope_study as study
from . import continuation_scores as r7
from . import continuation_study as archive
from . import domestic_form as r13
from . import r8_score_study as control
from . import r10_shot_model as decision
from .goal_state_study import sha, write_new
from .paired_kpi_inference import compare
from .r9_team_shape import published_rank
from .ucl_zero_goals import IDENTITY, reference_rows


PROTOCOL = Path(__file__).resolve().parents[2] / 'research/20261126-r30-europe-score-dispersion/protocol.json'
GOALS = np.arange(2 * base.SIZE - 1)
RESULT = base.RESULT.ravel()
BUCKET = RESULT * len(GOALS) + (base.H + base.A).ravel()
CURVATURE = np.minimum(GOALS, 6) * (np.minimum(GOALS, 6) - 1) / 2 + 5.5 * np.maximum(GOALS - 6, 0)
ARMS = {'r13': (0., 'original'), 'third_half': (.5, 'third'), 'third_full': (1., 'third'),
        'rank_half': (.5, 'rank'), 'rank_full': (1., 'rank')}
REGULATION = {'regulation_90', 'regulation_90 (sum of ESPN first- and second-half linescores)',
              'regulation_90 (ESPN scoreboard STATUS_FULL_TIME, period=2; no extra-time state)'}


def bucket_masses(matrices):
    return np.array([np.bincount(BUCKET, weights=m.ravel(), minlength=3 * len(GOALS))
                     for m in matrices]).reshape(-1, 3, len(GOALS))


def tilt(probabilities, beta):
    """Find the linear offset that preserves the supplied discrete goal mean."""
    if beta == 0.:
        return probabilities
    means = probabilities @ GOALS
    lo = np.full(len(probabilities), -5.5 * abs(beta))
    hi = -lo
    base_weight = probabilities * np.exp(beta * CURVATURE)
    for _ in range(44):
        midpoint = (lo + hi) / 2
        weight = base_weight * np.exp(midpoint[:, None] * GOALS)
        larger = weight @ GOALS > means * weight.sum(axis=1)
        hi = np.where(larger, midpoint, hi)
        lo = np.where(larger, lo, midpoint)
    weight = base_weight * np.exp(((lo + hi) / 2)[:, None] * GOALS)
    return weight / weight.sum(axis=1, keepdims=True)


def fit(buckets, actual):
    home, away = actual.T
    outcome = base.RESULT[home, away]
    totals = home + away
    conditional = buckets[np.arange(len(buckets)), outcome]
    conditional = conditional / conditional.sum(axis=1, keepdims=True)
    coefficients = []
    for mask in (outcome != 1, outcome == 1):
        current, observed = conditional[mask], totals[mask]
        index = np.arange(len(current))
        if not len(current) or np.any(current[index, observed] <= 0):
            raise ValueError('calibration requires positive probability of each observed training total')

        def objective(beta):
            fitted = tilt(current, beta)[index, observed]
            return -np.log(fitted).sum() + 16 * beta**2

        result = minimize_scalar(objective, method='bounded', bounds=(-.22, .22), options={'xatol': 1e-9})
        if not result.success:
            raise ValueError('dispersion fit failed: ' + result.message)
        coefficients.append(round(float(result.x), 6))
    return coefficients


def adjust(matrices, coefficients):
    buckets = bucket_masses(matrices)
    outcomes = buckets.sum(axis=2, keepdims=True)
    if np.any(outcomes <= 0):
        raise ValueError('each R13 outcome group requires positive probability')
    conditional = buckets / outcomes
    win, draw = coefficients
    tilted = np.stack([tilt(conditional[:, group, :], coefficient)
                      for group, coefficient in enumerate((win, draw, win))], axis=1)
    target = tilted * outcomes
    ratios = np.divide(target, buckets, out=np.ones_like(buckets), where=buckets > 0)
    changed = matrices * ratios.reshape(len(matrices), -1)[:, BUCKET].reshape(matrices.shape)
    adjusted = bucket_masses(changed)
    means = adjusted / adjusted.sum(axis=2, keepdims=True) @ GOALS
    errors = dict(wdl=float(np.max(abs(adjusted.sum(axis=2, keepdims=True) - outcomes))),
                  conditional_mean=float(np.max(abs(means - conditional @ GOALS))))
    if max(errors.values()) > 2e-12:
        raise ValueError('dispersion changed a locked marginal or conditional mean')
    return changed, errors


def third_pick(prior, changed, ordered):
    first, second, third = [tuple(map(int, value.split('-'))) for value in ordered]
    permitted = prior >= prior[third] - .015
    permitted[first] = permitted[second] = False
    index = np.unravel_index(np.argmax(np.where(permitted, changed, -1.)), changed.shape)
    if changed[index] <= changed[third] + 1e-12:
        return ordered
    return [*ordered[:2], f'{index[0]}-{index[1]}']


def unpack(data, spec, out):
    path = data / 'workspace-updates' / spec['path']
    if sha(path) != spec['sha256']:
        raise ValueError('registered archive differs: ' + spec['path'])
    control.extract(path, out)


def original_grids(panel, settings, fitted, refs, code, protocol):
    if settings['competition'] != code or fitted['competition'] != code or sha(panel) != settings['source_panel_sha256']:
        raise ValueError('R7/R13 recipe or panel identity differs')
    if fitted['validation_through'] >= protocol['windows']['train']['from']:
        raise ValueError('a source recipe used target-season results')
    games = study.check_panel(panel, code)
    indices = [i for i, g in enumerate(games)
               if g['stage'] == protocol['stage'] and protocol['windows']['train']['from'] <= g['date']
               < protocol['windows']['evaluation']['before']]
    expected_ids = {r['id'] for r in refs.values() if r['stage'] == protocol['stage']}
    if {games[i]['id'] for i in indices} != expected_ids:
        raise ValueError('the complete signed league-phase population is required')
    anchors, features, _ = r7.inputs(games, code)
    priors = control.matrices(anchors[indices], features[indices], settings)
    rows, grids = [], []
    for i, prior in zip(indices, priors, strict=True):
        game = games[i]
        ref = refs[game['id']]
        match = dict(game, cohort=code, actual=[game['home_goals'], game['away_goals']], line=base.actual_line(game))
        if any(ref[key] != match[key] for key in IDENTITY) or game['score_basis'] not in REGULATION:
            raise ValueError('archive fixture, final or line differs: ' + game['id'])
        full = r13.adjust(prior, np.asarray(ref['features']), fitted['fitted'])
        pick, heads = decision.decisions(prior, full, fitted['doses'], ref['line'], ref['forecast']['r7'])
        matrix = heads['score']
        q = control.quality(matrix, ref['actual'])
        wdl = np.bincount(RESULT, weights=heads['wdl'].ravel(), minlength=3)
        if pick != ref['forecast']['candidate'] or any(
                abs(q[key] - ref['quality']['candidate'][key]) > 2e-8 for key in ('p_observed', 'p_first', 'p_three')) or (
                not np.allclose(wdl, ref['quality']['candidate']['wdl'], rtol=0., atol=2e-8)):
            raise ValueError('signed R13 firsts, score or WDL probabilities differ: ' + game['id'])
        grids.append(matrix)
        rows.append(ref)
    return rows, np.asarray(grids)


def record(ref, prior, full):
    original = ref['forecast']['candidate']
    score = tuple(ref['actual'])
    picks = {'market': ref['forecast']['market'], 'r7': ref['forecast']['r7']}
    log_losses = {}
    for name, (dose, method) in ARMS.items():
        matrix = decision.blend(prior, full, dose)
        scores = original['top3']
        if method == 'rank':
            scores = base.top_three_scores(matrix)
        elif method == 'third':
            scores = third_pick(prior, matrix, scores)
        picks[name] = dict(original, primary=scores[0], top3=scores)
        log_losses[name] = round(float(-np.log(max(matrix[score], 1e-300))), 12)
    return dict({key: ref[key] for key in IDENTITY}, forecast=picks, log_loss=log_losses,
                original_actual_rank=published_rank(prior, score, original['top3']))


def metrics(rows, selected):
    comparisons = {arm: compare(rows, baseline='r13', candidate=arm) for arm in ARMS if arm != 'r13'}
    ranks = Counter(row['original_actual_rank'] for row in rows)
    original = compare(rows, baseline='market', candidate='r13')
    chosen = [dict(row, forecast=dict(row['forecast'], selected=row['forecast'][selected])) for row in rows]
    return dict(fixtures=len(rows), original_against_market=original, ablations=comparisons,
                selected_against_r13=compare(chosen, baseline='r13', candidate='selected'),
                selected_against_market=compare(chosen, baseline='market', candidate='selected'),
                r13_actual_ranks={key: ranks.get(key, 0) for key in range(1, 7)},
                r13_rank_seven_or_lower=len(rows)-sum(ranks.get(key, 0) for key in range(1, 7)),
                first_changed=sum(r['forecast'][selected]['primary'] != r['forecast']['r13']['primary'] for r in rows),
                three_members_changed=sum(set(r['forecast'][selected]['top3']) != set(r['forecast']['r13']['top3'])
                                          for r in rows))


def run(data, out):
    spec = control.read(PROTOCOL)
    summaries, validation, evaluation = {}, [], []
    with TemporaryDirectory(prefix='europe-dispersion-') as folder:
        temp = Path(folder)
        unpack(data, spec['archives']['r13'], temp/'r13')
        for code in spec['competitions']:
            path = temp/code
            for key in ('panel', 'r7'):
                unpack(data, spec['archives'][code][key], path/key)
            original = temp/'r13'/code
            refs = reference_rows(original/'forecasts.jsonl.gz')
            rows, matrices = original_grids(path/'panel/panel.jsonl', control.read(path/'r7/main/fit.json'),
                                            control.read(original/'fit.json'), refs, code, spec)
            windows = {tag: [i for i, r in enumerate(rows) if window['from'] <= r['date'] < window['before']]
                       for tag, window in spec['windows'].items()}
            if any(len(idx) != spec['expected_per_window'][code] for idx in windows.values()):
                raise ValueError('original chronological population differs: ' + code)
            train = windows['train']
            labels = np.array([rows[i]['actual'] for i in train])
            coefficients = fit(bucket_masses(matrices[train]), labels)

            def grade(window):
                idx = windows[window]
                full, errors = adjust(matrices[idx], coefficients)
                return [record(rows[i], matrices[i], m) for i, m in zip(idx, full, strict=True)], errors

            choosing, val_errors = grade('validation')
            trials = {}
            for name in ARMS:
                stats = compare(choosing, baseline='market', candidate=name)['tracks'][name]['metrics']
                trials[name] = dict(first=stats['score_top1']['hits'], three=stats['score_top3']['hits'],
                                    log_loss=round(float(np.mean([r['log_loss'][name] for r in choosing])), 12))
            original_trial = trials['r13']
            eligible = [name for name, values in trials.items()
                        if all(values[k] >= original_trial[k] for k in ('first', 'three'))]
            selected = min(eligible, key=lambda name: (-trials[name]['first'], -trials[name]['three'],
                           trials[name]['log_loss'], ARMS[name][0], ARMS[name][1] == 'rank'))
            held, eval_errors = grade('evaluation')
            result = metrics(held, selected)
            paired = result['selected_against_r13']['paired']
            gains = [paired[key]['net_hits'] for key in ('score_top1', 'score_top3')]
            summaries[code] = dict(training=len(train), validation=len(choosing), beta=dict(win=coefficients[0], draw=coefficients[1]),
                                   validation_trials=trials, selected=selected, evaluation=result,
                                   forward_screen_passed=selected != 'r13' and min(gains) >= 0 and max(gains) > 0,
                                   numeric_error={k: round(max(val_errors[k], eval_errors[k]), 14) for k in val_errors})
            validation.extend(choosing)
            evaluation.extend(held)
    out.mkdir(parents=True, exist_ok=False)
    write_new(out/'summary.json', dict(version=spec['version'], protocol_sha256=sha(PROTOCOL), competitions=summaries))
    archive.put(out/'validation.jsonl.gz', validation)
    archive.put(out/'evaluation.jsonl.gz', evaluation)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    run(args.data, args.out)
