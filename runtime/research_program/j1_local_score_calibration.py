"""Compare pooled and local score error calibration on the signed J1 R7 grids."""

import argparse
from collections import Counter
from pathlib import Path

import numpy as np

from . import all_scope_model as base
from . import continuation_report as report
from . import continuation_scores as r7
from . import continuation_study as archive
from . import j1_score_dispersion as replay_controls
from . import j1_team_allocation as j1
from . import r8_score_calibration as calibration
from . import r8_score_study as archive_controls
from .goal_state_study import sha, write_new
from .ucl_zero_goals import quality, selection


PROTOCOL = Path(__file__).resolve().parents[2] / 'research/20261127-r41-j1-local-score-calibration/protocol.json'
BUCKETS = int(calibration.CELL.max()) + 1
SCALES = np.array([.45, .25])
TOTAL = base.H + base.A
BALANCE = (base.RESULT == 0).astype(float) - (base.RESULT == 2)
ARMS = ('candidate', 'half_dose', 'full_dose', 'pooled')


def contexts(anchors):
    return np.column_stack([(anchors * axis).sum(axis=(1, 2)) for axis in (TOTAL, BALANCE)])


def training_vectors(anchors, games):
    """Count observed and expected buckets in the labeled outcome and settlement group."""
    observed = np.zeros((len(anchors), BUCKETS))
    expected = np.zeros_like(observed)
    for index, (anchor, game) in enumerate(zip(anchors, games, strict=True)):
        home, away = game['home_goals'], game['away_goals']
        if not (0 <= home < base.SIZE and 0 <= away < base.SIZE) or anchor[home, away] <= 0.:
            raise ValueError('training score outside the positive R7 support: ' + game['id'])
        cell = home * base.SIZE + away
        group = calibration.partitions(base.actual_line(game))
        mask = group == group[cell]
        mass = np.bincount(calibration.CELL[mask], weights=anchor.ravel()[mask], minlength=BUCKETS)
        observed[index, calibration.CELL[cell]] = 1.
        expected[index] = mass / mass.sum()
    return observed, expected


def log_offsets(observed, expected):
    ratios = np.clip((observed + 8.) / (expected + 8.), 2. / 3., 1.5)
    return np.round(np.log(ratios), 12)


def local_offsets(queries, neighbors, observed, expected):
    distance = ((queries[:, None, :] - neighbors[None, :, :]) / SCALES) ** 2
    weights = np.exp(-.5 * distance.sum(axis=2))
    return log_offsets(weights @ observed, weights @ expected), weights.sum(axis=1)


def adjusted(prior, offsets, line):
    if not np.any(offsets):
        return prior, 0.
    matrix = calibration.apply(prior, offsets, line)
    group = calibration.partitions(line)
    old = np.bincount(group, weights=prior.ravel())
    new = np.bincount(group, weights=matrix.ravel())
    error = float(np.max(np.abs(old - new)))
    if error > 2e-14:
        raise ValueError('changed a protected WDL or Asian settlement probability')
    return matrix, error


def window_rows(name, indices, games, signed, priors, offsets, pooled_offsets, exposure):
    rows = []
    for i, prior, local, count in zip(indices, priors, offsets, exposure, strict=True):
        game = games[i]
        line = base.actual_line(game)
        full, local_error = adjusted(prior, local, line)
        pooled, pooled_error = adjusted(prior, pooled_offsets, line)
        row = replay_controls.record(game, prior, full, name, signed[game['id']])
        picked = selection(pooled, prior, row['forecast']['r7'])
        row['forecast']['pooled'] = picked
        row['quality']['pooled'] = quality(pooled, picked, tuple(row['actual']))
        row['kernel_weight_sum'] = round(float(count), 8)
        row['max_projection_error'] = max(local_error, pooled_error)
        rows.append(row)
    return rows


def summarize(rows):
    summary = replay_controls.summarize(rows, ARMS, ('r7', 'market', 'pooled'))
    movements = {}
    for arm in ARMS:
        ranks = [(row['quality']['r7']['actual_rank'], row['quality'][arm]['actual_rank']) for row in rows]
        movements[arm] = {
            name: dict(Counter(f'{before}->{after}' for before, after in ranks
                               if (before <= limit) != (after <= limit)))
            for name, limit in (('first', 1), ('three', 3))
        }
    summary['rank_movements'] = movements
    return summary


def replay(data, out):
    protocol = archive_controls.read(PROTOCOL)
    shared_hash = sha(j1.PROTOCOL)
    if shared_hash != protocol['inputs']['j1_source_protocol_sha256']:
        raise ValueError('registered J1 archives or dates changed')
    shared = archive_controls.read(j1.PROTOCOL)
    games, signed, settings = replay_controls.load_inputs(data, shared)
    windows = j1.match_rows(games, shared)
    anchors, features, _ = r7.inputs(games, j1.COMPETITION)
    train = windows['train']
    designs = contexts(anchors)
    observed, expected = training_vectors(anchors[train], [games[i] for i in train])
    pooled = log_offsets(observed.sum(axis=0), expected.sum(axis=0))

    def evaluate(name):
        indices = windows[name]
        local, exposure = local_offsets(designs[indices], designs[train], observed, expected)
        priors = archive_controls.matrices(anchors[indices], features[indices], settings)
        return window_rows(name, indices, games, signed, priors, local, pooled, exposure)

    validation = evaluate('validation')
    trials = {}
    for dose, arm in replay_controls.DOSES.items():
        stats = report.rates(validation, arm)
        trials[dose] = {key: stats['counts'][key][0] for key in replay_controls.SCORE_METRICS}
        trials[dose]['score_log_loss'] = stats['score_log_loss']
    selected = replay_controls.choose(trials)

    history = {name: evaluate(name) for name in j1.HISTORY}
    combined = [row for rows in history.values() for row in rows]
    predictions = validation + combined
    for row in predictions:
        for field in ('forecast', 'quality'):
            row[field]['candidate'] = row[field][replay_controls.DOSES[selected]]
    metrics = {name: summarize(rows) for name, rows in dict(history, combined=combined).items()}
    gains = [metrics['combined']['paired']['candidate']['r7'][key]['net_hits']
             for key in replay_controls.SCORE_METRICS]
    eligible = selected > 0. and min(gains) >= 0 and max(gains) > 0
    weights = [row['kernel_weight_sum'] for row in predictions]

    out.mkdir(parents=True, exist_ok=False)
    write_new(out / 'fit.json', dict(
        version=protocol['version'], competition=j1.COMPETITION, stage=j1.STAGE, label=protocol['label'],
        protocol_sha256=sha(PROTOCOL), r25_protocol_sha256=shared_hash, windows=shared['windows'],
        pooled_log_offsets=pooled.reshape(6, 6).tolist(), selected_dose=selected, validation_trials=trials,
        validation_pooled=report.rates(validation, 'pooled'), prospective_screen_passed=eligible,
        min_kernel_weight_sum=min(weights), max_kernel_weight_sum=max(weights),
        max_projection_error=max(row['max_projection_error'] for row in predictions)))
    write_new(out / 'metrics.json', metrics)
    archive.put(out / 'validation.jsonl.gz', validation)
    archive.put(out / 'forecasts.jsonl.gz', combined)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    replay(args.data, args.out)
