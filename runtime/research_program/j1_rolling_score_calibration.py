"""Replay calendar-causal recent J1 score residuals on the signed R7 grids."""

import argparse
from datetime import date
from pathlib import Path

import numpy as np

from research.analysis_freeze import DEFAULT_ROOT, aware, digest
from . import all_scope_model as base
from . import continuation_report as report
from . import continuation_scores as r7
from . import continuation_study as archive
from . import j1_first_consensus as r43
from . import j1_fixed_first_backups as r42
from . import j1_local_score_calibration as r41
from . import j1_process_forward as j1_forward
from . import j1_score_dispersion as r29
from . import j1_team_allocation as j1
from . import r8_score_study as controls
from .goal_state_study import sha, write_new
from .ucl_zero_goals import IDENTITY, quality, reference_rows, selection


PROTOCOL = Path(__file__).resolve().parents[2] / 'research/20261127-r44-j1-rolling-score-calibration/protocol.json'
HALF_LIFE_DAYS = 180.
ARMS = ('r7', 'context_half', 'context_full', 'recency_only')


def rolling_offsets(query_days, queries, history_days, contexts, observed, expected):
    """Weight completed dates strictly before each query, including within assessment windows."""
    age = query_days[:, None] - history_days[None, :]
    weights = np.where(age > 0, np.exp2(-np.maximum(age, 0) / HALF_LIFE_DAYS), 0.)
    recency = r41.log_offsets(weights @ observed, weights @ expected)
    context, exposure = r41.local_offsets(queries, contexts, observed, expected, case_weights=weights)
    return recency, context, np.column_stack(((age > 0).sum(axis=1), weights.sum(axis=1), exposure))


def evaluate_window(name, indices, games, priors, signed, fixed, recent, context, exposure):
    rows = []
    for i, prior, age_offset, local_offset, coverage in zip(indices, priors, recent, context, exposure, strict=True):
        game = games[i]
        mid, line = game['id'], base.actual_line(game)
        full, context_error = r41.adjusted(prior, local_offset, line)
        dated, recency_error = r41.adjusted(prior, age_offset, line)
        row = r29.record(game, prior, full, name, signed[mid])
        original = row['forecast']['r7']
        comparison = fixed[mid]
        if (any(row[key] != comparison[key] for key in (*IDENTITY, 'window'))
                or any(row['forecast'][arm] != comparison['forecast'][arm] for arm in ('r7', 'market'))):
            raise ValueError('fixed R41 comparison does not match the signed replay: ' + mid)
        for field in ('forecast', 'quality'):
            result = row[field]
            result['context_half'] = result.pop('half_dose')
            result['context_full'] = result.pop('full_dose')
            result['fixed_r41'] = comparison[field]['full_dose']
        picked = selection(dated, prior, original)
        row['forecast']['recency_only'] = picked
        row['quality']['recency_only'] = quality(dated, picked, tuple(row['actual']))
        row['history'] = dict(earlier_results=int(coverage[0]), recency_weight_sum=round(float(coverage[1]), 8),
                              context_weight_sum=round(float(coverage[2]), 8))
        row['max_projection_error'] = max(context_error, recency_error)
        rows.append(row)
    return rows


def choose(validation):
    stats = {arm: report.rates(validation, arm) for arm in ARMS}
    trials = {arm: dict(first=values['counts']['score_top1'][0], three=values['counts']['score_top3'][0],
                        log_loss=values['score_log_loss']) for arm, values in stats.items()}
    original = trials['r7']
    allowed = [arm for arm in ARMS if trials[arm]['first'] >= original['first']
               and trials[arm]['three'] >= original['three']]
    selected = min(allowed, key=lambda a: (-trials[a]['first'], -trials[a]['three'], trials[a]['log_loss'], ARMS.index(a)))
    return selected, trials


def historical(data, paths, spec):
    shared = controls.read(j1.PROTOCOL)
    games, signed, settings = r29.load_inputs(data, shared)
    windows = j1.match_rows(games, shared)
    anchors, features, _ = r7.inputs(games, j1.COMPETITION)
    indices = sorted(i for group in windows.values() for i in group)
    days = np.array([date.fromisoformat(g['date']).toordinal() for g in games])
    contexts = r41.contexts(anchors)
    observed, expected = r41.training_vectors(anchors[indices], [games[i] for i in indices])
    history = days[indices], contexts[indices], observed, expected
    directory = paths['r41'] / 'replay'
    controls_by_window = {
        'validation': reference_rows(directory / 'validation.jsonl.gz'),
        **dict.fromkeys(j1.HISTORY, reference_rows(directory / 'forecasts.jsonl.gz')),
    }

    def evaluate(name):
        group = windows[name]
        recent, local, exposure = rolling_offsets(days[group], contexts[group], *history)
        priors = controls.matrices(anchors[group], features[group], settings)
        return evaluate_window(name, group, games, priors, signed, controls_by_window[name], recent, local, exposure)

    validation = evaluate('validation')
    selected, trials = choose(validation)
    windows_out = {name: evaluate(name) for name in j1.HISTORY}
    combined = [row for rows in windows_out.values() for row in rows]
    for row in validation + combined:
        for field in ('forecast', 'quality'):
            row[field]['candidate'] = row[field][selected]
    metrics = {
        name: r29.summarize(rows, ('candidate', *ARMS[1:], 'fixed_r41'), ('r7', 'market', 'fixed_r41'))
        for name, rows in dict(windows_out, combined=combined).items()
    }
    gains = [metrics['combined']['paired']['candidate']['r7'][key]['net_hits'] for key in r29.SCORE_METRICS]
    eligible = selected != 'r7' and min(gains) >= 0 and max(gains) > 0
    exposure = {name: [min(row['history'][name] for row in validation + combined),
                       max(row['history'][name] for row in validation + combined)] for name in validation[0]['history']}
    details = dict(version=spec['version'], protocol_sha256=sha(PROTOCOL), windows=shared['windows'],
                   selected_arm=selected, validation_trials=trials, prospective_screen_passed=eligible,
                   causal_history_ranges=exposure,
                   max_projection_error=max(row['max_projection_error'] for row in validation + combined))
    return details, metrics, validation, combined, history


def prepare_forward(paths, spec, selected, history):
    parent, originals, priors, _ = r43.forward_inputs(paths, spec)
    days = np.array([aware(row['fixture']['kickoff_at']).date().toordinal() for _, row, _, _ in originals])
    anchors = np.array([row['matrices']['anchor'] for _, row, _, _ in originals])
    if history[0].max() > date(2026, 11, 21).toordinal() or history[0].max() >= min(days):
        raise ValueError('forward J1 history no longer matches the locked R7 cutoff')
    recent, local, coverage = rolling_offsets(days, r41.contexts(anchors), *history)
    offsets = recent if selected == 'recency_only' else local
    rows = {}
    for (mid, ref, original, source), prior, offset, exposure in zip(originals, priors, offsets, coverage, strict=True):
        adjusted, error = r41.adjusted(prior, offset, ref['line'])
        candidate = .5 * (prior + adjusted) if selected == 'context_half' else adjusted
        picks = selection(candidate, prior, original)
        lead = tuple(map(int, picks['primary'].split('-')))
        rows[mid] = dict(fixture=ref['fixture'], line=ref['line'],
                         forecasts=dict(r7=original, candidate=dict(picks, first_probability=float(prior[lead])),
                                        market=ref['forecasts']['market'], comprehensive=ref['forecasts']['comprehensive']),
                         matrices=dict(r7=prior.tolist(), candidate=candidate.tolist()),
                         exposure=dict(earlier_results=int(exposure[0]), recency_weight_sum=round(float(exposure[1]), 8),
                                       context_weight_sum=round(float(exposure[2]), 8)),
                         max_projection_error=error, source_r42_sha256=source['record_sha256'])
    selected_ids = j1_forward.ranked(rows, 'candidate')
    identity = dict(version=spec['version'], protocol_sha256=sha(PROTOCOL), competitions=[j1.COMPETITION],
                    parent_sha256=parent['record_sha256'], selected={'candidate': selected_ids},
                    selected_arm=selected, history_through=date.fromordinal(int(history[0].max())).isoformat(),
                    source_cutoff_at=max(aware(source['recorded_at']) for _, _, _, source in originals).isoformat(),
                    pairs={f'candidate_vs_{arm}': [arm, 'candidate'] for arm in ('r7', 'comprehensive', 'market')},
                    forecast_sha256={mid: digest(row) for mid, row in rows.items()})
    return dict(identity=identity, rows=rows)


def forward_check(prepared):
    matches = [dict(match_id=mid, kickoff_at=row['fixture']['kickoff_at'],
                    r7=row['forecasts']['r7']['top3'], candidate=row['forecasts']['candidate']['top3'])
               for mid, row in prepared['rows'].items()]
    return dict(registered=len(matches), changed_first=sum(r['r7'][0] != r['candidate'][0] for r in matches),
                changed_ordered_scores=sum(r['r7'] != r['candidate'] for r in matches),
                source_sha256=digest(prepared['identity']), matches=matches)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    modes = parser.add_mutually_exclusive_group(required=True)
    for mode in ('replay', 'issue', 'verify'):
        modes.add_argument('--' + mode, type=Path)
    parser.add_argument('--freeze-root', type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    protocol = controls.read(PROTOCOL)
    paths = r43.locked_archives(args.data, protocol, (('r41', r41.PROTOCOL), ('forward', r42.PROTOCOL)))
    fit, results, validation, evaluation, training = historical(args.data, paths, protocol)
    prepared, status = None, dict(disposition='HISTORICAL_SCREEN_FAILED')
    if fit['prospective_screen_passed']:
        prepared = prepare_forward(paths, protocol, fit['selected_arm'], training)
        check = forward_check(prepared)
        status = dict(check, disposition='READY_TO_FREEZE' if check['changed_ordered_scores'] else 'IDENTICAL_TO_R7')
    if args.replay:
        args.replay.mkdir(parents=True, exist_ok=False)
        write_new(args.replay / 'fit.json', fit)
        write_new(args.replay / 'metrics.json', results)
        write_new(args.replay / 'forward-check.json', status)
        archive.put(args.replay / 'validation.jsonl.gz', validation)
        archive.put(args.replay / 'forecasts.jsonl.gz', evaluation)
    else:
        if status['disposition'] != 'READY_TO_FREEZE':
            raise ValueError('rolling score calibration has no eligible different forward issue')
        if args.issue:
            r42.issue_forward(prepared, args.issue, protocol['track'], args.freeze_root)
        r42.verify_forward(prepared, args.issue or args.verify, protocol['track'])
