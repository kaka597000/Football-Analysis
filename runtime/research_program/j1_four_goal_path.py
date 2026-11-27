"""Test a calibrated four-or-more-goal path on the signed J1 forecasts."""

import argparse
from collections import Counter
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit

from research.analysis_freeze import DEFAULT_ROOT, aware, digest
from . import all_scope_model as base
from . import continuation_report as report
from . import continuation_scores as r7
from . import continuation_study as archive
from . import j1_first_consensus as r43
from . import j1_fixed_first_backups as r42
from . import j1_process_forward as j1_forward
from . import j1_score_dispersion as r29
from . import j1_team_allocation as j1
from . import r8_score_calibration as partitions
from . import r8_score_study as controls
from .goal_state_study import sha, write_new
from .ucl_zero_goals import quality, selection


PROTOCOL = Path(__file__).resolve().parents[2] / 'research/20261127-r45-j1-four-goal-path/protocol.json'
TOTAL = base.H + base.A
HIGH = TOTAL >= 4
ARMS = ('r7', 'guarded_third', 'context_half', 'context_full', 'static_full')


def pace(anchors):
    return np.clip(np.sum(anchors * TOTAL, axis=(1, 2)) - 2.6, -1.5, 1.5)


def coefficients(design, offset, observed):
    def loss(beta):
        logits = offset + design @ beta
        value = np.sum(np.logaddexp(0., logits) - observed * logits) + 8. * (beta @ beta)
        gradient = design.T @ (expit(logits) - observed) + 16. * beta
        return value, gradient

    bounds = [(-1.25, 1.25), (-.8, .8)][:design.shape[1]]
    result = minimize(loss, np.zeros(design.shape[1]), jac=True, method='L-BFGS-B', bounds=bounds,
                      options={'ftol': 1e-11})
    if not result.success:
        raise ValueError('four-plus calibration failed: ' + result.message)
    return np.round(result.x, 6)


def fit(anchors, games):
    rows = []
    high = HIGH.ravel()
    for prior, game, x in zip(anchors, games, pace(anchors), strict=True):
        actual = game['home_goals'], game['away_goals']
        if not all(0 <= n < base.SIZE for n in actual) or prior[actual] <= 0.:
            raise ValueError('the observed regulation score is outside the anchor support')
        group = partitions.partitions(base.actual_line(game))
        index = actual[0] * base.SIZE + actual[1]
        cell = group == group[index]
        masses = [prior.ravel()[cell & mask].sum() for mask in (~high, high)]
        if min(masses) > 0.:
            rows.append((float(x), np.log(masses[1] / masses[0]), high[index], base.RESULT[actual] == 1))
    x, offset, observed, draw = np.array(rows).T
    design = np.column_stack((np.ones(len(x)), x))
    contextual, static = [], []
    for mask in (draw == 0, draw == 1):
        contextual.append(coefficients(design[mask], offset[mask], observed[mask]))
        static.append([coefficients(design[mask, :1], offset[mask], observed[mask])[0], 0.])
    return dict(context=np.asarray(contextual), static=np.asarray(static), informative=len(rows))


def adjust(prior, fitted, x, line):
    delta = (fitted @ np.array([1., x]))[[0, 1, 0]]
    if not np.any(delta):
        return prior
    groups = partitions.partitions(line)
    weights = prior.ravel() * np.exp((delta[base.RESULT] * HIGH).ravel())
    original = np.bincount(groups, weights=prior.ravel())
    shifted = np.bincount(groups, weights=weights)
    factor = np.divide(original, shifted, out=np.ones_like(shifted), where=shifted > 0.)
    matrix = (weights * factor[groups]).reshape(prior.shape)
    if not np.allclose(np.bincount(groups, weights=matrix.ravel()), original, rtol=0., atol=2e-14):
        raise ValueError('four-plus recalibration changed a protected WDL or settlement probability')
    return matrix


def guarded_third(full, original):
    ordered = original['top3']
    old = [tuple(map(int, score.split('-'))) for score in ordered]
    if any(HIGH[cell] for cell in old) or full[HIGH].sum() < .30:
        return original, None
    index = np.unravel_index(np.argmax(np.where(HIGH, full, -1.)), full.shape)
    cost = float(full[old[2]] - full[index])
    if cost > .015:
        return original, cost
    return dict(original, top3=[*ordered[:2], '%d-%d' % index]), cost


def variants(prior, x, line, original, fitted):
    full = adjust(prior, fitted['context'], x, line)
    matrices = dict(r7=prior, guarded_third=full, context_half=adjust(prior, .5 * fitted['context'], x, line),
                    context_full=full, static_full=adjust(prior, fitted['static'], x, line))
    picks = {arm: selection(grid, prior, original) for arm, grid in matrices.items()}
    picks['guarded_third'], cost = guarded_third(full, original)
    return picks, matrices, cost


def miss_reason(actual, scores):
    original = [tuple(map(int, score.split('-'))) for score in scores]
    if actual in original:
        return 'first' if actual == original[0] else 'backup_only'
    totals = {sum(cell) for cell in original}
    total = sum(actual)
    if total > max(totals):
        return 'above_four_plus' if total >= 4 else 'above_three_or_less'
    if total < min(totals):
        return 'below'
    if total not in totals:
        return 'unrepresented_total'
    if not any(sum(cell) == total and base.RESULT[cell] == base.RESULT[actual] for cell in original):
        return 'same_total_different_direction'
    return 'same_total_and_direction'


def summarize(rows):
    metrics = r29.summarize(rows, ('candidate', *ARMS[1:]))
    observed = np.array([sum(row['actual']) >= 4 for row in rows])
    calibration = {}
    for arm in ARMS:
        estimated = np.array([row['p_four_plus'][arm] for row in rows])
        calibration[arm] = dict(expected_matches=float(estimated.sum()), brier=float(np.mean((estimated - observed) ** 2)))
    guards = [row['guard_cost'] for row in rows if row['guard_cost'] is not None]
    metrics['four_plus'] = dict(observed=int(observed.sum()), forecast=calibration,
                                guarded_paths_considered=len(guards), min_guard_cost=min(guards, default=None))
    metrics['r7_three_score_breakdown'] = dict(Counter(row['score_breakdown'] for row in rows))
    return metrics


def evaluate(data, spec):
    shared = controls.read(j1.PROTOCOL)
    games, signed, settings = r29.load_inputs(data, shared)
    windows = j1.match_rows(games, shared)
    anchors, features, _ = r7.inputs(games, j1.COMPETITION)
    train = windows['train']
    fitted = fit(anchors[train], [games[i] for i in train])

    def make_rows(name):
        indices = windows[name]
        priors = controls.matrices(anchors[indices], features[indices], settings)
        rows = []
        for i, prior, x in zip(indices, priors, pace(anchors[indices]), strict=True):
            game = games[i]
            ref = signed[game['id']]
            row, original = r29.validated_reference(game, prior, name, ref)
            picks, matrices, cost = variants(prior, x, row['line'], original, fitted)
            actual = tuple(row['actual'])
            rows.append(dict(row, forecast=dict(picks, market=ref['forecast']['market']),
                quality={arm: quality(matrix, picks[arm], actual) for arm, matrix in matrices.items()},
                p_four_plus={arm: float(matrix[HIGH].sum()) for arm, matrix in matrices.items()},
                guard_cost=cost, score_breakdown=miss_reason(actual, original['top3'])))
        return rows

    validation = make_rows('validation')
    validation_stats = {arm: report.rates(validation, arm)['counts'] for arm in ARMS}
    trials = {arm: [counts[metric][0] for metric in r29.SCORE_METRICS] for arm, counts in validation_stats.items()}
    allowed = [arm for arm in ARMS if all(v >= ref for v, ref in zip(trials[arm], trials['r7'], strict=True))]
    selected = min(allowed, key=lambda a: (-trials[a][0], -trials[a][1], ARMS.index(a)))
    groups = {name: make_rows(name) for name in j1.HISTORY}
    combined = [row for group in groups.values() for row in group]
    for row in validation + combined:
        for field in ('forecast', 'quality'):
            row[field]['candidate'] = row[field][selected]
    metrics = {name: summarize(rows) for name, rows in dict(groups, validation=validation, combined=combined).items()}
    gains = [metrics['combined']['paired']['candidate']['r7'][key]['net_hits'] for key in r29.SCORE_METRICS]
    fit_record = dict(version=spec['version'], protocol_sha256=sha(PROTOCOL), windows=shared['windows'],
                      fitted={k: fitted[k].tolist() for k in ('context', 'static')},
                      informative_training_games=fitted['informative'], validation_counts=trials,
                      selected_arm=selected, forward_eligible=selected != 'r7' and min(gains) >= 0 and max(gains) > 0)
    return fit_record, metrics, validation, combined, fitted


def prepare_forward(paths, spec, fitted, selected):
    parent, originals, priors, _ = r43.forward_inputs(paths, spec)
    anchors = np.array([row['matrices']['anchor'] for _, row, _, _ in originals])
    rows = {}
    for (mid, ref, original, source), prior, x in zip(originals, priors, pace(anchors), strict=True):
        picks, matrices, cost = variants(prior, x, ref['line'], original, fitted)
        candidate, grid = picks[selected], matrices[selected]
        lead = tuple(map(int, candidate['primary'].split('-')))
        rows[mid] = dict(fixture=ref['fixture'], line=ref['line'], guard_cost=cost,
                         forecasts=dict(r7=original, candidate=dict(candidate, first_probability=float(grid[lead])),
                                        market=ref['forecasts']['market'], comprehensive=ref['forecasts']['comprehensive']),
                         matrices=dict(r7=prior.tolist(), candidate=grid.tolist()),
                         source_r42_sha256=source['record_sha256'])
    identity = dict(version=spec['version'], protocol_sha256=sha(PROTOCOL), competitions=[j1.COMPETITION],
                    parent_sha256=parent['record_sha256'], selected_arm=selected,
                    selected={'candidate': j1_forward.ranked(rows, 'candidate')},
                    source_cutoff_at=max(aware(source['recorded_at']) for _, _, _, source in originals).isoformat(),
                    pairs={f'candidate_vs_{arm}': [arm, 'candidate'] for arm in ('r7', 'comprehensive', 'market')},
                    forecast_sha256={mid: digest(row) for mid, row in rows.items()})
    return dict(identity=identity, rows=rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    modes = parser.add_mutually_exclusive_group(required=True)
    for mode in ('replay', 'issue', 'verify'):
        modes.add_argument('--' + mode, type=Path)
    parser.add_argument('--freeze-root', type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    spec = controls.read(PROTOCOL)
    paths = r43.locked_archives(args.data, spec, (('forward', r42.PROTOCOL),))
    fit_record, metrics, validation, history, fitted = evaluate(args.data, spec)
    if args.replay:
        args.replay.mkdir(parents=True, exist_ok=False)
        write_new(args.replay / 'fit.json', fit_record)
        write_new(args.replay / 'metrics.json', metrics)
        archive.put(args.replay / 'forecasts.jsonl.gz', history)
        archive.put(args.replay / 'validation.jsonl.gz', validation)
    else:
        if not fit_record['forward_eligible']:
            raise ValueError('four-plus calibration did not meet the preregistered historical screen')
        prepared = prepare_forward(paths, spec, fitted, fit_record['selected_arm'])
        if all(row['forecasts']['candidate']['top3'] == row['forecasts']['r7']['top3']
               for row in prepared['rows'].values()):
            raise ValueError('there is no different forward score list to freeze')
        if args.issue:
            r42.issue_forward(prepared, args.issue, spec['track'], args.freeze_root)
        r42.verify_forward(prepared, args.issue or args.verify, spec['track'])
