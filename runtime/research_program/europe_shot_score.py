"""Test combinations of the signed domestic and shot-based European score grids."""

import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from . import all_scope_model as base
from . import all_scope_study as study
from . import continuation_scores as r7
from . import continuation_study as archive
from . import europe_score_dispersion as r30
from . import r10_shot_model as shots
from . import r10_study
from . import r8_score_study as control
from .goal_state_study import sha, write_new
from .paired_kpi_inference import compare
from .r9_team_shape import published_rank
from .ucl_zero_goals import IDENTITY, reference_rows


PROTOCOL = Path(__file__).resolve().parents[2] / 'research/20261126-r33-europe-shot-score/protocol.json'
ARMS = {'r13': 0., 'quarter': .25, 'half': .5, 'full': 1.}
SCORES = ('score_top1', 'score_top3')


def result_mass(matrix):
    return np.bincount(r30.RESULT, weights=matrix.ravel(), minlength=3)


def projected_shots(prior, shot_grid):
    """Use the shot grid inside each outcome group and the prior across outcomes."""
    prior_mass, shot_mass = result_mass(prior), result_mass(shot_grid)
    if np.any((prior_mass > 0.) & (shot_mass <= 0.)):
        raise ValueError('original outcome lacks support in the shot distribution')
    weights = np.divide(prior_mass, shot_mass, out=np.zeros(3), where=shot_mass > 0.)
    return shot_grid * weights[base.RESULT]


def restore_shots(panel, settings, fit, archived, observations, original):
    code = settings['competition']
    if fit['competition'] != code or fit['panel_sha256'] != [sha(panel)] or fit['bounds'] != settings['bounds']:
        raise ValueError('R7 and R10 source panel or bounds differ')
    if fit['validation_through'] >= min(row['date'] for row in original):
        raise ValueError('R10 selection overlaps the R33 target season')
    games = study.check_panel(panel, code)
    positions = {g['id']: i for i, g in enumerate(games)}
    indices = [positions[row['id']] for row in original]
    anchors, features, _ = r7.inputs(games, code)
    priors = control.matrices(anchors[indices], features[indices], settings)
    states, _, _ = shots.walk(games, observations, code)
    records = []
    for supplied, i, prior in zip(original, indices, priors, strict=True):
        mid = supplied['id']
        entry, context = archived[mid], states[i]
        if (any(entry[key] != supplied[key] for key in IDENTITY)
                or any(entry['forecast'][arm] != supplied['forecast'][arm] for arm in ('r7', 'market'))
                or entry['shot_ready'] != context['ready']
                or not np.allclose(entry['prior_shot_games'], context['exposure'], rtol=0., atol=2e-10)):
            raise ValueError('R10 signed identity or shot exposure differs: ' + mid)
        _, full = shots.alternatives(prior, context, fit['fitted'])
        picks, heads = shots.decisions(prior, full, fit['doses'], supplied['line'], entry['forecast']['r7'])
        recorded = entry['quality']['candidate']
        quality = control.quality(heads['score'], entry['actual'])
        if (picks != entry['forecast']['candidate'] or base.pick(full, supplied['line']) != entry['forecast']['fixed_full']
                or any(abs(quality[key] - recorded[key]) > 2e-8 for key in ('p_observed', 'p_first', 'p_three'))
                or not np.allclose(result_mass(heads['wdl']), recorded['wdl'], rtol=0., atol=2e-8)):
            raise ValueError('reconstructed R10 score or direction differs: ' + mid)
        records.append((full, entry))
    return records


def record(ref, prior, shot_grid, shot_ref):
    original = ref['forecast']['candidate']
    actual = tuple(ref['actual'])
    exposed = shot_ref['shot_ready']
    shot = projected_shots(prior, shot_grid) if exposed else prior
    forecast = dict(r7=ref['forecast']['r7'], r10=shot_ref['forecast']['candidate'], market=ref['forecast']['market'])
    ranks, loss, directions = {}, {}, {}
    locked_mass = result_mass(prior)
    max_error = 0.
    for name, dose in ARMS.items():
        matrix = shots.blend(prior, shot, dose)
        ordered = original['top3'] if matrix is prior else base.top_three_scores(matrix)
        forecast[name] = dict(original, primary=ordered[0], top3=ordered)
        ranks[name] = published_rank(matrix, actual, ordered)
        loss[name] = round(float(-np.log(max(1e-300, matrix[actual]))), 12)
        displayed = [tuple(map(int, score.split('-'))) for score in ordered]
        directions[name] = any(base.RESULT[score] == base.RESULT[actual] for score in displayed)
        max_error = max(max_error, float(np.max(abs(result_mass(matrix)-locked_mass))))
    if max_error > 2e-12:
        raise ValueError('mixture moved a locked R13 outcome mass')
    return dict({key: ref[key] for key in IDENTITY}, forecast=forecast, prior_shots=exposed,
                shot_exposure=shot_ref['prior_shot_games'], actual_rank=ranks, exact_log_loss=loss,
                actual_direction_in_three=directions, max_score_outcome_mass_error=max_error)


def score_counts(rows):
    return {name: dict(score_top1=sum(row['actual_rank'][name] == 1 for row in rows),
                       score_top3=sum(row['actual_rank'][name] <= 3 for row in rows),
                       log_loss=round(float(np.mean([row['exact_log_loss'][name] for row in rows])), 12))
            for name in ARMS}


def ties_baseline(trials, candidate):
    return all(trials[candidate][key] >= trials['r13'][key] for key in SCORES)


def grade(folds):
    nominating = score_counts(folds['train'])
    proposing = [name for name in ARMS if ties_baseline(nominating, name)]
    nominee = min(proposing, key=lambda name: (-nominating[name]['score_top1'], -nominating[name]['score_top3'],
                                               nominating[name]['log_loss'], ARMS[name]))
    validation = score_counts(folds['validation'])
    selected = nominee if ties_baseline(validation, nominee) else 'r13'
    test = folds['evaluation']
    chosen = [dict(row, forecast=dict(row['forecast'], selected=row['forecast'][selected])) for row in test]
    own = compare(chosen, baseline='r13', candidate='selected')
    controls = {role: compare(chosen, baseline=role, candidate='selected') for role in ('r7', 'r10', 'market')}
    gains = [own['paired'][metric]['net_hits'] for metric in SCORES]
    direction_misses = {name: sum(row['actual_direction_in_three'][name] and row['actual_rank'][name] > 3
                                  for row in test) for name in ARMS}
    return dict(nominating=nominating, nominee=nominee, validation=validation, selected=selected,
                sample={name: dict(fixtures=len(rows), paired_prior_shots=sum(row['prior_shots'] for row in rows))
                        for name, rows in folds.items()}, holdout=own, controls=controls,
                holdout_ablations={name: compare(test, baseline='r13', candidate=name) for name in ARMS if name != 'r13'},
                three_misses_containing_actual_direction=direction_misses,
                max_outcome_mass_error=max(row['max_score_outcome_mass_error'] for rows in folds.values() for row in rows),
                forward_screen_passed=selected != 'r13' and min(gains) >= 0 and max(gains) > 0)


def replay(data, out):
    spec = control.read(PROTOCOL)
    if sha(r30.PROTOCOL) != spec['r30_protocol_sha256']:
        raise ValueError('original matchup windows differ')
    locked = control.read(r30.PROTOCOL)
    ledger = {name: [] for name in locked['windows']}
    summaries = {}
    with TemporaryDirectory(prefix='r33-europe-shot-') as directory:
        root = Path(directory)
        r30.unpack(data, locked['archives']['r13'], root/'r13')
        for name, archive_spec in spec['archives'].items():
            r30.unpack(data, archive_spec, root/name)
        observed = r10_study.observations((root/'r6/inputs/replay-input.jsonl', root/'supplement/process.jsonl'))
        for code in locked['competitions']:
            path = root/code
            for name in ('panel', 'r7'):
                r30.unpack(data, locked['archives'][code][name], path/name)
            panel = path/'panel/panel.jsonl'
            settings = control.read(path/'r7/main/fit.json')
            signed = root/'r13'/code
            refs = reference_rows(signed/'forecasts.jsonl.gz')
            original, priors = r30.original_grids(panel, settings, control.read(signed/'fit.json'), refs, code, locked)
            shot = root/'r10/main'/code
            regimes = restore_shots(panel, settings, control.read(shot/'fit.json'),
                                    reference_rows(shot/'forecasts.jsonl.gz'), observed[code], original)
            rows = [record(ref, prior, grid, entry) for ref, prior, (grid, entry) in zip(original, priors, regimes, strict=True)]
            folds = {name: [row for row in rows if span['from'] <= row['date'] < span['before']]
                     for name, span in locked['windows'].items()}
            if any(len(group) != locked['expected_per_window'][code] for group in folds.values()):
                raise ValueError('incomplete registered league-phase sample: ' + code)
            summaries[code] = grade(folds)
            for name, group in folds.items():
                ledger[name].extend(group)
    out.mkdir(parents=True, exist_ok=False)
    write_new(out/'summary.json', dict(version=spec['version'], protocol_sha256=sha(PROTOCOL), competitions=summaries))
    for name, rows in ledger.items():
        archive.put(out/f'{name}.jsonl.gz', rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    replay(args.data, args.out)
