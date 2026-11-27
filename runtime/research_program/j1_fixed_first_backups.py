"""Test calibrated J1 backup scores behind the same first score."""

import argparse
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from publication import atomic_json
from research.analysis_freeze import DEFAULT_ROOT, aware, digest
from . import all_scope_model as base
from . import continuation_forward as r7_forward
from . import continuation_report as report
from . import continuation_scores as r7
from . import continuation_study as archive
from . import goal_state_forward as ledger
from . import j1_local_score_calibration as r41
from . import j1_process_forward as j1_forward
from . import j1_score_dispersion as replay_controls
from . import j1_team_allocation as j1
from . import r8_score_study as controls
from .future_identity import fixture_identity
from .goal_state_study import sha, write_new
from .ucl_zero_goals import IDENTITY


PROTOCOL = Path(__file__).resolve().parents[2] / 'research/20261127-r42-j1-fixed-first-backups/protocol.json'
DOSES = replay_controls.DOSES
BACKUP_ARMS = ('half_dose', 'full_dose')


def keep_first(original, ordered):
    if len(ordered) != 3 or len(set(ordered)) != 3 or not original['primary']:
        raise ValueError('backup reranking requires a first score and three distinct ranked cells')
    primary = original['primary']
    backups = [score for score in ordered if score != primary][:2]
    return dict(original, top3=[primary, *backups])


def specification():
    protocol = controls.read(PROTOCOL)
    for version, path in (('r41', r41.PROTOCOL), ('r25', j1.PROTOCOL)):
        if sha(path) != protocol['inputs'][f'{version}_source_protocol_sha256']:
            raise ValueError('analysis differs from registered score calibration inputs: ' + version)
    return protocol


def historical_rows(path, windows, shared):
    rows = []
    for source in archive.read_gzip(path):
        name = source['window']
        if name not in windows:
            raise ValueError('unexpected J1 development window: ' + name)
        bounds = shared['windows'][name]
        if (source['cohort'] != j1.COMPETITION or source['stage'] != j1.STAGE
                or not bounds['from'] <= source['date'] < bounds['before']):
            raise ValueError('fixture outside its registered J1 development scope')
        original = source['forecast']['r7']
        forecast = {arm: keep_first(original, source['forecast'][arm]['top3']) for arm in BACKUP_ARMS}
        rows.append(dict({key: source[key] for key in (*IDENTITY, 'window')},
                         forecast=dict(forecast, r7=original, market=source['forecast']['market'])))
    if (len({row['id'] for row in rows}) != len(rows)
            or Counter(row['window'] for row in rows) != {w: shared['windows'][w]['fixtures'] for w in windows}):
        raise ValueError('J1 replay did not retain the complete unique fixture population')
    return rows


def summarize(rows):
    result = replay_controls.summarize(rows)
    by_score = {}
    for arm in ('candidate', *BACKUP_ARMS):
        counts = dict(gained=Counter(), lost=Counter())
        for row in rows:
            actual = '%d-%d' % tuple(row['actual'])
            before = actual in row['forecast']['r7']['top3']
            after = actual in row['forecast'][arm]['top3']
            if before != after:
                counts['gained' if after else 'lost'][actual] += 1
        by_score[arm] = counts
    return dict(result, score_changes=by_score)


def replay(data, out):
    protocol = specification()
    inputs = protocol['inputs']
    source = data / inputs['r41_replay']
    for file, expected in inputs['r41_replay_sha256'].items():
        if sha(source / file) != expected:
            raise ValueError('R41 replay hash differs: ' + file)
    shared = controls.read(j1.PROTOCOL)
    validation = historical_rows(source / 'validation.jsonl.gz', ('validation',), shared)
    hits = {dose: report.rates(validation, arm)['counts']['score_top3'][0] for dose, arm in DOSES.items()}
    selected = min(hits, key=lambda dose: (-hits[dose], dose))
    history = historical_rows(source / 'forecasts.jsonl.gz', j1.HISTORY, shared)
    if {row['id'] for row in validation} & {row['id'] for row in history}:
        raise ValueError('overlap between J1 model selection and historical evaluation')
    for row in validation + history:
        row['forecast']['candidate'] = row['forecast'][DOSES[selected]]
    groups = {name: [row for row in history if row['window'] == name] for name in j1.HISTORY}
    metrics = {name: summarize(rows) for name, rows in dict(groups, combined=history).items()}
    out.mkdir(parents=True, exist_ok=False)
    write_new(out / 'summary.json', dict(version=protocol['version'], protocol_sha256=sha(PROTOCOL),
              validation_hits=hits, validation=summarize(validation), selected_development_dose=selected,
              evaluation=metrics))
    archive.put(out / 'validation.jsonl.gz', validation)
    archive.put(out / 'history.jsonl.gz', history)


def original_inputs(data, shared, batch):
    """Recover and verify the original issued R7 grid and its game-level anchor."""
    source_records = {}
    with TemporaryDirectory(prefix='j1-backups-original-') as directory:
        root = Path(directory)
        for name in ('r7_fit', 'r7_firsts', 'r5_firsts'):
            controls.extract(j1.locked_path(data, shared, name), root / name)
        recipe_path = root / 'r7_fit/main/forward.json'
        recipe = controls.read(recipe_path)
        recipe_sha256 = sha(recipe_path)
        if recipe['competition'] != j1.COMPETITION or recipe['version'] != r7.VERSION:
            raise ValueError('wrong J1 pregame score recipe')
        anchor_recipe = dict(recipe, policy=dict(recipe['policy'], mix=0.))
        issued = root / 'r7_firsts'
        input_dir = root / 'r5_firsts/issued'
        refs = controls.read(issued / 'receipts.json')
        source_refs = controls.read(input_dir / 'receipts.json')
        for mid, (fixture, _, _) in batch.items():
            first = ledger._read(issued, refs['matches'][mid])
            source = ledger._read(input_dir, source_refs['matches'][mid])
            payload = first['payload']
            if (any(fixture_identity(record['fixture']) != fixture_identity(fixture) for record in (first, source))
                    or payload['registration_sha256'] != refs['registration']['sha256']
                    or payload['input_sha256'] != source['record_sha256']
                    or payload['model_sha256'] != recipe_sha256 or payload['training_through'] != recipe['through']
                    or not date.fromisoformat(recipe['through']) < aware(source['recorded_at']).date()
                    or not aware(source['recorded_at']) <= aware(first['recorded_at']) < aware(fixture['kickoff_at'])):
                raise ValueError('R7 first, recipe, input or original fixture differs: ' + mid)
            game = r7_forward.frozen_game(fixture, source['payload'])
            if (game['competition'] != j1.COMPETITION or game['stage'] != j1.STAGE
                    or base.actual_line(game) != payload['line'] or payload['line'] != source['payload']['line']):
                raise ValueError('R7 original model phase or actual line differs: ' + mid)
            anchor, prior = (r7_forward.matrix(game, settings) for settings in (anchor_recipe, recipe))
            original = payload['forecasts']['candidate']
            reference = payload['quality']
            wdl = np.bincount(base.RESULT.ravel(), weights=prior.ravel())
            if (base.pick(prior, payload['line']) != original
                    or not np.allclose(wdl, reference['wdl'], rtol=0., atol=2e-8)
                    or not np.allclose([prior.max(), np.partition(prior.ravel(), -3)[-3:].sum()],
                                       [reference['p_first'], reference['p_three']], rtol=0., atol=2e-8)):
                raise ValueError('recomputed score grid differs from the signed R7 issue: ' + mid)
            source_records[mid] = (first, source, anchor, prior)
    return source_records, recipe_sha256


def prepare_forward(data, protocol):
    locked = protocol['inputs']
    original_root = data / j1_forward.REGISTRATION
    plan = ledger._read(original_root, controls.read(original_root / 'plan.json'))
    publication, batch = j1_forward.original_batch(data, j1_forward.SECOND_BATCH, 'nov28_29', plan)
    if (plan['freeze_id'] != locked['original_registration']
            or publication['record_sha256'] != locked['original_publication_sha256'] or len(batch) != 10):
        raise ValueError('research must use the registered ten J1 firsts')
    shared = controls.read(j1.PROTOCOL)
    originals, recipe_sha256 = original_inputs(data, shared, batch)
    games, _, _ = replay_controls.load_inputs(data, shared)
    train = j1.match_rows(games, shared)['train']
    anchors, _, _ = r7.inputs(games, j1.COMPETITION)
    training = anchors[train]
    observed, expected = r41.training_vectors(training, [games[i] for i in train])
    queries = r41.contexts(np.array([originals[mid][2] for mid in batch]))
    offsets, exposure = r41.local_offsets(queries, r41.contexts(training), observed, expected)
    rows = {}
    for (mid, (fixture, authored, _)), local, weight_sum in zip(batch.items(), offsets, exposure, strict=True):
        first, source, anchor, prior = originals[mid]
        payload = first['payload']
        line = payload['line']
        original = payload['forecasts']['candidate']
        full, _ = r41.adjusted(prior, local, line)
        matrices = dict(r7=prior, half_dose=.5 * (prior + full), full_dose=full)
        arms = {arm: keep_first(original, base.top_three_scores(matrices[arm])) for arm in BACKUP_ARMS}
        main = authored['payload']
        first_probability = round(float(payload['quality']['p_first']), 12)
        forecasts = dict(arms, r7=dict(original, first_probability=first_probability),
                         market=payload['forecasts']['market'],
                         comprehensive=dict(primary=main['primary'], top3=main['displayed_scores'],
                                            wdl=j1_forward.WDL[main['wdl']]))
        rows[mid] = dict(fixture=fixture, forecasts=forecasts, line=line,
                         matrices={name: matrix.tolist() for name, matrix in dict(anchor=anchor, **matrices).items()},
                         kernel_weight_sum=round(float(weight_sum), 8),
                         source_r7_sha256=first['record_sha256'], source_r5_sha256=source['record_sha256'],
                         source_comprehensive_sha256=authored['record_sha256'])
    if all(row['forecasts']['r7']['top3'] == row['forecasts'][arm]['top3']
           for row in rows.values() for arm in BACKUP_ARMS):
        raise ValueError('the registered backup arms would issue identical score lists')
    selected = j1_forward.ranked(rows, 'r7')
    source_times = [aware(record['recorded_at']) for first, source, _, _ in originals.values()
                    for record in (first, source)]
    source_cutoff = max(*source_times, aware(publication['recorded_at']))
    identity = dict(version=protocol['version'], competitions=[j1.COMPETITION], protocol_sha256=sha(PROTOCOL),
                    recipe_sha256=recipe_sha256, source_cutoff_at=source_cutoff.isoformat(),
                    parent_sha256=plan['record_sha256'], original_publication_sha256=publication['record_sha256'],
                    pairs={f'{arm}_vs_{ref}': [ref, arm] for arm in BACKUP_ARMS
                           for ref in ('r7', 'market', 'comprehensive')},
                    selected={arm: selected for arm in ('r7', *BACKUP_ARMS)},
                    forecast_sha256={mid: digest(row) for mid, row in rows.items()})
    return dict(identity=identity, rows=rows)


def issue_forward(prepared, out, track, freeze_root):
    identity, rows = prepared['identity'], prepared['rows']
    fixtures = [row['fixture'] for row in rows.values()]
    deadline = min(aware(fixture['kickoff_at']) for fixture in fixtures)
    if not aware(identity['source_cutoff_at']) <= datetime.now(timezone.utc) < deadline:
        raise ValueError('the complete J1 slate is outside its pregame issue interval')
    plan = ledger.register(fixtures, identity, track, out, freeze_root=freeze_root)
    matches = {mid: ledger.issue(plan, dict(row['fixture'], cutoff_at=plan['recorded_at']), row,
                                 track, out, freeze_root=freeze_root) for mid, row in rows.items()}
    index = dict(registration=plan, matches=matches)
    path = out / 'receipts.json'
    if path.exists() and controls.read(path) != index:
        raise ValueError('existing research index differs from the first issues')
    atomic_json(path, index)


def verify_forward(prepared, out, track):
    identity, rows = prepared['identity'], prepared['rows']
    index = controls.read(out / 'receipts.json')
    plan = ledger._read(out, index['registration'])
    deadline = min(aware(row['fixture']['kickoff_at']) for row in rows.values())
    if (plan['track'] != track + '-registration' or plan['payload']['track'] != track
            or plan['payload']['identity'] != identity or set(index['matches']) != set(rows)
            or not aware(identity['source_cutoff_at']) <= aware(plan['recorded_at']) < deadline):
        raise ValueError('the pregame registration differs from the replayed J1 slate')
    for mid, row in rows.items():
        saved = ledger._read(out, index['matches'][mid])
        if (saved['payload'] != dict(row, registration_sha256=plan['record_sha256'])
                or saved['fixture'] != dict(row['fixture'], cutoff_at=plan['recorded_at'])
                or not aware(plan['recorded_at']) <= aware(saved['recorded_at']) < deadline):
            raise ValueError('first issue differs from the complete pregame replay: ' + mid)
    pending = ledger.review(out, [], out, by_competition=True)[j1.COMPETITION]
    if (pending['registered'], pending['settled']) != (len(rows), 0):
        raise ValueError('result readiness check differs from the ten pending J1 issues')
    print(f'{len(rows)} pending J1 matches verified under {plan["freeze_id"]}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    modes = parser.add_mutually_exclusive_group(required=True)
    for mode in ('replay', 'issue', 'verify'):
        modes.add_argument('--' + mode, type=Path)
    parser.add_argument('--freeze-root', type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    if args.replay:
        replay(args.data, args.replay)
    else:
        spec = specification()
        prepared = prepare_forward(args.data, spec)
        if args.issue:
            issue_forward(prepared, args.issue, spec['track'], args.freeze_root)
        verify_forward(prepared, args.issue or args.verify, spec['track'])
