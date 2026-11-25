"""Run the registered R23 penalty-opportunity replay on the original R22 data export.

Export the R5/R7/R10/R14/R21/R22 directories under workspace-updates from data
commit 64efb374fb598b39c1c14048573c3e8754633c76, or use that checkout directly.
With PYTHONPATH=runtime, run python -B -m research_program.penalty_opportunity_study
--data-root DATA --out NEW_DIRECTORY. Repeat with another output directory.
Optionally pass --events with the exact R14 events.jsonl for either run.
"""

import argparse
from collections import Counter, defaultdict
from datetime import date
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import platform
import tarfile
import tempfile

import numpy as np

from . import all_scope_model as base
from . import continuation_forward as r7_forward
from . import continuation_scores as r7
from . import continuation_study as archive
from . import draw_preserving_study as r22
from . import paired_kpi_inference as paired
from . import penalty_opportunity as model
from . import score_dependence_study as r21
from .goal_state_study import read_lines, sha, write_new


PROTOCOL = r22.ROOT / 'research/20261125-r23-penalty-opportunity/protocol.json'
PROTOCOL_SHA = 'c6c3672cc729f18ba3233cba005e604153e1da08a30d4693491b5e521e94d654'
R22_DATA = '64efb374fb598b39c1c14048573c3e8754633c76'
R22_MANIFEST_SHA = '4fe70a743c3ba3450c095448810e932ab09c71a3770ff63447668102f602510c'
R22_SOURCE = '46de33168c093816ac7cd19a57e36e51286b408f'
EVENTS_SHA = r21.EVENTS_SHA
CODES, BOUNDS, WINDOWS = r22.CODES, r22.BOUNDS, r22.WINDOWS
EXPECTED = dict(zip(WINDOWS, (1752, 1752, 500, 48), strict=True))
COUNTS = dict(zip(WINDOWS, ('validation_fixtures', 'evaluation_fixtures',
                           'recent_fixtures', 'extension_fixtures'), strict=True))
DOSES = (0., .5, 1.)
ARMS = ('r7', 'r22', 'market', 'candidate', 'full_dose')
BASELINES = (('r7', 'r22'), ('r7', 'market'), ('r7', 'candidate'), ('r22', 'candidate'),
             ('market', 'candidate'), ('r7', 'full_dose'), ('candidate', 'full_dose'))
DIRECTIONS = ('wdl', 'total_side', 'total_line')
ARCHIVE_ROOTS = {
    'r5': '20261123-all-scope-kpi', 'r7': '20261123-prematch-literature',
    'r10': '20261125-r10-shot-conversion', 'r11': '20261125-r21-score-dependence/inputs/r11',
}


def _read(path):
    return json.loads(path.read_bytes())


def _checked(package, manifest, relative):
    path = package / relative
    r21.check_sha(path, manifest['files'][relative])
    return path


def _reference_file(package, manifest, relative):
    ref = _checked(package, manifest, 'replay/reference/' + relative)
    repeat = _checked(package, manifest, 'replay/repeat/' + relative)
    if ref.read_bytes() != repeat.read_bytes():
        raise ValueError('R22 reference and repeat differ: ' + relative)
    return ref


def _archives(data_root):
    updates = data_root / 'workspace-updates'
    package = updates / '20261125-r22-draw-preserving'
    manifest_path = package / 'manifest.json'
    if sha(manifest_path) != R22_MANIFEST_SHA:
        raise ValueError(f'original R22 manifest required from data commit {R22_DATA}: {manifest_path}')
    manifest = _read(manifest_path)
    lock = _read(_checked(package, manifest, 'source-lock.json'))
    if lock['final_source_commit'] != R22_SOURCE or lock['protocol_sha256'] != r22.PROTOCOL_SHA:
        raise ValueError('R22 source lock differs from the registered source')
    for name, digest in lock['source_files_sha256'].items():
        r21.check_sha(r22.ROOT / name, digest)
    locked = _read(_reference_file(package, manifest, 'inputs.json'))
    if locked['r21_inputs_sha256'] != r22.R21_INPUTS_SHA:
        raise ValueError('R21 parent inputs differ from the R22 lock')
    r21_package = updates / '20261125-r21-score-dependence'
    for run in ('reference', 'repeat'):
        r21.check_sha(r21_package / 'replay' / run / 'inputs.json', r22.R21_INPUTS_SHA)
    parents = _read(r21_package / 'replay/reference/inputs.json')
    if (locked['archive_manifest_sha256'] != r21.MANIFESTS
            or parents['archive_manifest_sha256'] != r21.MANIFESTS
            or parents['events_sha256'] != EVENTS_SHA):
        raise ValueError('R22 archive manifest or event digests differ from R21')
    roots = {name: updates / sub for name, sub in ARCHIVE_ROOTS.items()}
    manifests = r22._verify_archive_bindings(
        roots['r5'], roots['r7'], roots['r10'], roots['r11'], locked)
    if manifests['r11']['files']['extension.tar.gz'] != parents['extension_sha256']:
        raise ValueError('R11 extension differs from its locked R21 digest')
    event_archive = updates / '20261125-r14-territory-press/inputs/understat.tar.gz'
    r21.check_sha(event_archive, lock['r21_parent']['understat_archive_sha256'])
    return package, manifest, locked, roots, manifests, event_archive, lock


def _observations(event_archive, events):
    if events is None:
        with tarfile.open(event_archive, 'r:gz') as bundle:
            raw = bundle.extractfile('events.jsonl').read()
    else:
        raw = events.read_bytes()
    if hashlib.sha256(raw).hexdigest() != EVENTS_SHA:
        raise ValueError('Understat events differ from the locked R14 event bytes')
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def _check_design(games, features, metadata):
    if (features.shape != (len(games), len(model.FEATURES)) or len(model.FEATURES) != 4
            or not np.isfinite(features).all() or np.any(np.abs(features) > .75)
            or len(metadata) != len(games)):
        raise ValueError('invalid four-column historical design')
    for g, x, meta in zip(games, features, metadata, strict=True):
        if (meta['cutoff'] != g['date'] or meta.get('date', g['date']) != g['date']
                or meta.get('competition', g['competition']) != g['competition']):
            raise ValueError('historical feature cutoff differs: ' + g['id'])
        mean = meta['league_mean']
        if mean is not None and (not np.isfinite(mean) or mean < 0.):
            raise ValueError('invalid as-of league mean: ' + g['id'])
        cutoff = date.fromisoformat(meta['cutoff'])
        for side, columns in (('home', (0, 3)), ('away', (2, 1))):
            detail = meta[side]
            n, history = detail['n'], detail['games']
            if (type(n) is not int or not 0 <= n <= len(history) <= 12
                    or any(date.fromisoformat(row['date']) >= cutoff for row in history)
                    or n != sum(row['valid'] for row in history)
                    or (n and (detail.get('source_team_id') is None or mean is None))
                    or (not n and any(x[i] != 0. for i in columns))):
                raise ValueError('invalid as-of club history: ' + g['id'] + '/' + side)


def _actual(game):
    return game['home_goals'], game['away_goals']


def _observed_probability(grid, score):
    home, away = score
    return float(grid[home, away]) if home < grid.shape[0] and away < grid.shape[1] else 0.


def _case_match(game, window, reference, clock):
    expected = dict(id=game['id'], cohort=game['competition'], stage=game['stage'], date=game['date'],
                    home=game['home'], away=game['away'], actual=list(_actual(game)),
                    line=base.actual_line(game), source_sha256=game['source_sha256'], window=window,
                    kickoff=clock, prices=dict(h2h=game.get('h2h'), total=game.get('total')),
                    bookmaker=game.get('bookmaker'), market_time_basis=game.get('market_time_basis'))
    for key, value in expected.items():
        if reference[key] != value:
            raise ValueError(f'R22 archived {key} differs: {game["id"]}')
    return expected


def _verify_quality(original, rebuilt, mid):
    for key in ('p_observed', 'p_first', 'p_three', 'p_ordered'):
        if not np.allclose(original[key], rebuilt[key], rtol=1e-10, atol=1e-12):
            raise ValueError(f'archived {key} differs from the reconstructed grid: {mid}')


def _pick(prior, full, dose, original, line):
    pick, grid = model.choices(prior, full, dose, original, line)
    if (any(pick.get(key) != original.get(key) for key in DIRECTIONS)
            or (dose == 0. and (pick != original or not np.array_equal(grid, prior)))):
        raise ValueError('R23 dose altered an original R7 decision outside the registered score head')
    return pick, grid


def _selection(games, priors, full_grids, originals):
    if not len(games) or not len(games) == len(priors) == len(full_grids) == len(originals):
        raise ValueError('misaligned 2024/25 dose-selection inputs')
    trials = [dict(dose=dose, first_hits=0, top3_hits=0, negative_log_likelihood=0.,
                   validation_games=len(games)) for dose in DOSES]
    details = []
    for g, prior, full, original in zip(games, priors, full_grids, originals, strict=True):
        actual = _actual(g)
        text = f'{actual[0]}-{actual[1]}'
        detail = []
        for trial in trials:
            pick, grid = _pick(prior, full, trial['dose'], original, base.actual_line(g))
            probability = _observed_probability(grid, actual)
            trial['first_hits'] += pick['primary'] == text
            trial['top3_hits'] += text in pick['top3']
            trial['negative_log_likelihood'] -= float(np.log(max(1e-300, probability)))
            detail.append(dict(dose=trial['dose'], primary=pick['primary'], top3=pick['top3'], p_observed=probability))
        details.append(detail)
    chosen = min(trials, key=lambda t: (-t['first_hits'], -t['top3_hits'], t['negative_log_likelihood'], t['dose']))
    return chosen['dose'], trials, details


def _record(game, window, prior, full, dose, feature, process, reference, clock):
    row = _case_match(game, window, reference, clock)
    r7_pick = reference['forecast']['r7']
    market = reference['forecast']['market']
    line, actual = row['line'], row['actual']
    r7_quality = r22._quality(prior, r7_pick, actual)
    _verify_quality(reference['quality']['r7'], r7_quality, game['id'] + '/R7')
    market_grid, _, _, _ = base.baseline(game, np.full_like(prior, 1. / prior.size))
    # Equal market cells can swap under floating-point re-ranking. Audit the
    # archived picks in their original order against the price-implied grid.
    market_quality = r22._quality(market_grid, market, actual) if market.get('top3') else None
    if reference['quality']['market'] is not None:
        _verify_quality(reference['quality']['market'], market_quality, game['id'] + '/market')
    candidate, selected_grid = _pick(prior, full, dose, r7_pick, line)
    fixed, fixed_grid = _pick(prior, full, 1., r7_pick, line)
    forecast = dict(r7=r7_pick, r22=reference['forecast']['candidate'], market=market,
                    candidate=candidate, full_dose=fixed)
    quality = dict(r7=r7_quality, r22=reference['quality']['candidate'], market=market_quality,
                   candidate=r22._quality(selected_grid, candidate, actual),
                   full_dose=r22._quality(fixed_grid, fixed, actual))
    settled = {arm: paired._decisions(pick, actual, line) for arm, pick in forecast.items()}
    return dict(row, sources=game['sources'], price_quote_at=None, market_as_of_certified=False,
                r7_basis='frozen_R7_recipe_on_extension' if window == WINDOWS[3] else
                'postfit_R7_validation' if window == WINDOWS[0] else 'original_R7_heldout',
                features=feature.tolist(), process=process, dose=dose, forecast=forecast, quality=quality,
                outcomes={arm: item[1] for arm, item in settled.items()},
                total_settlement={arm: item[2] for arm, item in settled.items()})


def _rank_batches(rows):
    groups = defaultdict(list)
    for row in rows:
        row['batch'] = {}
        groups[row['cohort'], row['window'], row['date']].append(row)
    result = []
    for (code, window, day), group in sorted(groups.items()):
        for arm in ARMS:
            ranked = sorted((r for r in group if r['quality'][arm] is not None),
                            key=lambda r: (-r['quality'][arm]['p_first'], r['kickoff']['source_time'], r['id']))
            selected, scores = [], {}
            for rank, row in enumerate(ranked, 1):
                seat = rank if rank <= 3 else None
                row['batch'][arm] = dict(rank=rank, seat=seat)
                if seat is not None:
                    scores[row['id']] = '-'.join(map(str, row['actual']))
                    selected.append(dict(match_id=row['id'], seat=seat, kickoff=row['kickoff'],
                                         first_probability=row['quality'][arm]['p_first'], **row['forecast'][arm],
                                         actual=row['actual'], outcomes=row['outcomes'][arm],
                                         total_settlement=row['total_settlement'][arm]))
            swept = r21.sweep(selected, scores)
            result.append(dict(cohort=code, window=window, date=day, arm=arm, entrants=len(group),
                               batch_size=len(ranked), ordered_ids=[r['id'] for r in ranked], selected=selected,
                               short=len(ranked) < 3, primary_sweep=bool(swept['primary_sweeps'])
                               if len(ranked) >= 3 else None, **swept))
    return result


def _coverage(metadata, features):
    squads = [sum(meta[side]['n'] > 0 for side in ('home', 'away')) for meta in metadata]
    count = Counter(squads)
    by_side = {}
    for side in ('home', 'away'):
        details = [meta[side] for meta in metadata]
        by_side[side] = dict(with_valid_observations=sum(d['n'] > 0 for d in details),
                             valid_observations=sum(d['n'] for d in details),
                             missing_identity=sum(d.get('source_team_id') is None for d in details),
                             neutral_fallback=sum(d['n'] == 0 for d in details))
    return dict(fixtures=len(metadata), both_clubs=count[2], one_club=count[1], neither_club=count[0],
                both_club_coverage=count[2] / len(metadata) if metadata else None,
                neutral_fallback_matches=count[0] + count[1],
                neutral_feature_vectors=sum(not any(row) for row in features), sides=by_side)


def _summarize(rows, batches):
    tracks, comparisons = {}, {}
    for baseline, arm in BASELINES:
        stats = paired.compare(rows, baseline=baseline, candidate=arm,
                               draws=r22.BOOTSTRAP_DRAWS, seed=r22.BOOTSTRAP_SEED)
        tracks.update(stats['tracks'])
        comparisons[baseline + '_vs_' + arm] = stats['paired']
    coverage = _coverage([row['process'] for row in rows], [row['features'] for row in rows])
    coverage['market_time_basis'] = dict(sorted(Counter(row['market_time_basis'] for row in rows).items()))
    coverage['certified_market_as_of'] = sum(row['market_as_of_certified'] for row in rows)
    return dict(population=len(rows), tracks=tracks, paired=comparisons, bootstrap=stats['bootstrap'],
                coverage=coverage, three_seat_sweep={arm: r22._seat_summary([b for b in batches if b['arm'] == arm])
                                                     for arm in ARMS})


def _load_league(code, work, package, manifest, locked, roots, manifests):
    folders = {arm: work / code / arm for arm in ('r5', 'r7')}
    for arm, dest in folders.items():
        r21.verified_archive(roots[arm], manifests[arm], 'models/' + code + '.tar.gz', dest)
    panel, old_r7 = folders['r5'] / 'panel.jsonl', folders['r7'] / 'main'
    forecast_dir = f'main/{code}/'
    paths = dict(panel=panel, r7_fit=old_r7 / 'fit.json', r7_recipe=old_r7 / 'forward.json',
                 r7_forecasts=old_r7 / 'forecasts.jsonl.gz',
                 r10_forecasts=work / 'r10/main' / code / 'forecasts.jsonl.gz',
                 r5_evaluation=folders['r5'] / 'study/evaluation.jsonl',
                 r5_recent=folders['r5'] / 'study/recent.jsonl')
    hashes = {name + '_sha256': sha(path) for name, path in paths.items()}
    if any(digest != locked[name] for name, digest in hashes.items()):
        raise ValueError('R22 retained R5/R7/R10 input file differs: ' + code)
    for name, filename in (('r22_fit', 'fit.json'), ('r22_forecasts', 'forecasts.jsonl.gz'),
                           ('r22_validation', 'validation.jsonl.gz')):
        paths[name] = _reference_file(package, manifest, forecast_dir + filename)
        hashes[name + '_sha256'] = sha(paths[name])
    games = r21.panels.check_panel(panel, code)
    settings, recipe, r22_fit = (_read(paths[key]) for key in ('r7_fit', 'r7_recipe', 'r22_fit'))
    if (any(artifact['competition'] != code for artifact in (settings, recipe, r22_fit))
            or settings['version'] != r7.VERSION or recipe['version'] != r7.VERSION
            or r22_fit['version'] != r22.model.VERSION or r22_fit['dose'] not in DOSES
            or settings['source_panel_sha256'] != hashes['panel_sha256']
            or recipe['fit_sha256'] != hashes['r7_fit_sha256']):
        raise ValueError('R7/R22 archived fit or recipe identity differs: ' + code)
    held = [g for g in games if g['date'] >= BOUNDS[1]]
    held_ids = [g['id'] for g in held]
    r5_rows = [dict(row, window=w) for w in WINDOWS[1:3] for row in read_lines(paths['r5_' + w])]
    r5_ref, r7_ref, r10_ref = (r21.index_rows(rows, held_ids) for rows in
                              (r5_rows, archive.read_gzip(paths['r7_forecasts']),
                               archive.read_gzip(paths['r10_forecasts'])))
    originals = {g['id']: r21.original_controls(g, WINDOWS[1] if g['date'] < BOUNDS[2] else WINDOWS[2],
                                               r5_ref[g['id']], r7_ref[g['id']], r10_ref[g['id']])[:2]
                 for g in held}
    reference = archive.read_gzip(paths['r22_validation']) + archive.read_gzip(paths['r22_forecasts'])
    for row in reference:
        if row['id'] in originals and (row['forecast']['r7'], row['forecast']['market']) != originals[row['id']]:
            raise ValueError('R22 differs from the original R7 or market forecast: ' + row['id'])
    return games, settings, recipe, r22_fit, reference, hashes


def _fit(anchors, features, scores):
    fitted = model.fit(anchors, features, scores)
    outside = int(np.any(scores >= np.array(anchors.shape[-2:]), axis=1).sum())
    if fitted.get('outside_grid', outside) != outside:
        raise ValueError('R23 fit returned an inconsistent number of out-of-grid scores')
    return fitted, outside


def _replay(code, games, extra, observations, settings, recipe, r22_fit, reference, clocks, locked, hashes, output):
    all_games = games + extra
    keys = [(g['date'], g['id']) for g in all_games]
    if (not games or not extra or extra[0]['date'] <= games[-1]['date'] or keys != sorted(keys)
            or len({g['id'] for g in all_games}) != len(all_games) or any(g['competition'] != code for g in all_games)):
        raise ValueError('incomplete, overlapping or unordered fixture population: ' + code)
    cut, test, recent = (sum(g['date'] < bound for g in games) for bound in BOUNDS)
    if (not 0 < cut < test < recent < len(games) or tuple(settings['bounds']) != BOUNDS
            or settings['training_games'] != cut or settings['training_through'] != games[cut-1]['date']
            or recipe['through'] != games[-1]['date'] or recipe['n'] != len(games)
            or r22_fit['validation_through'] != games[test-1]['date']):
        raise ValueError('locked R7/R22 chronological boundaries differ: ' + code)
    windows = [w for w, n in zip(WINDOWS, (test-cut, recent-test, len(games)-recent, len(extra)), strict=True)
               for _ in range(n)]
    if len(games) != locked['base_fixtures'] or Counter(windows) != {w: locked[COUNTS[w]] for w in WINDOWS}:
        raise ValueError('locked R22 window counts differ: ' + code)
    lookup = r21.index_rows(reference, [g['id'] for g in all_games[cut:]])
    for g, window in zip(all_games[cut:], windows, strict=True):
        _case_match(g, window, lookup[g['id']], clocks[g['id']])

    features, metadata = model.Design(observations, code).walk(all_games)
    _check_design(all_games, features, metadata)
    anchors, r7_features, _ = r7.inputs(all_games, code)
    scores = np.array([_actual(g) for g in all_games])
    postfit = r21.control.matrices(anchors[cut:len(games)], r7_features[cut:len(games)], settings)
    priors = list(postfit) + [r7_forward.matrix(g, recipe) for g in extra]
    fitted, outside = _fit(anchors[:cut], features[:cut], scores[:cut])
    tilted = [model.matrix(prior, x, fitted) for prior, x in zip(priors, features[cut:], strict=True)]
    n_validation = test - cut
    validation = games[cut:test]
    dose, trials, checks = _selection(validation, priors[:n_validation], tilted[:n_validation],
                                      [lookup[g['id']]['forecast']['r7'] for g in validation])
    rows = [_record(g, w, prior, full, dose, x, meta, lookup[g['id']], clocks[g['id']])
            for g, w, prior, full, x, meta in zip(all_games[cut:], windows, priors, tilted, features[cut:],
                                                  metadata[cut:], strict=True)]
    for row, exposures in zip(rows[:n_validation], checks, strict=True):
        row['validation_doses'] = exposures
    batches = _rank_batches(rows)

    output.mkdir(parents=True)
    fit_path = output / 'fit.json'
    training_coverage = _coverage(metadata[:cut], features[:cut])
    write_new(fit_path, dict(version=model.VERSION, competition=code, training_through=games[cut-1]['date'],
                            validation_through=games[test-1]['date'], dose=dose, fitted=fitted,
                            training_games=cut, validation_games=n_validation, training_outside_grid=outside,
                            protocol_sha256=PROTOCOL_SHA, inputs=hashes, training_coverage=training_coverage,
                            validation_trials=trials))
    forward, forward_outside = _fit(anchors, features, scores)
    forward_coverage = _coverage(metadata, features)
    write_new(output / 'forward.json', dict(version=model.VERSION, competition=code, through=extra[-1]['date'],
                                            dose=dose, fitted=forward, training_games=len(all_games),
                                            training_outside_grid=forward_outside, fit_sha256=sha(fit_path),
                                            inputs=hashes, protocol_sha256=PROTOCOL_SHA,
                                            training_coverage=forward_coverage))
    archive.put(output / 'exposure.jsonl.gz', (
        dict(id=g['id'], competition=code, date=g['date'], home=g['home'], away=g['away'], actual=list(_actual(g)),
             source_sha256=g['source_sha256'], features=x.tolist(), process=m)
        for g, x, m in zip(all_games, features, metadata, strict=True)))
    archive.put(output / 'validation.jsonl.gz', rows[:n_validation])
    archive.put(output / 'validation-seats.jsonl.gz', [b for b in batches if b['window'] == WINDOWS[0]])
    archive.put(output / 'forecasts.jsonl.gz', rows[n_validation:])
    archive.put(output / 'seats.jsonl.gz', [b for b in batches if b['window'] != WINDOWS[0]])
    weights = dict(dose=dose, beta=fitted['beta'], training_through=games[cut-1]['date'], training_games=cut,
                   training_outside_grid=outside, validation_through=games[test-1]['date'],
                   forward_beta=forward['beta'], forward_through=extra[-1]['date'], forward_games=len(all_games),
                   forward_outside_grid=forward_outside, r22_dose=r22_fit['dose'])
    return rows, batches, weights


def _metrics(rows, batches):
    return {w: _summarize([r for r in rows if r['window'] == w], [b for b in batches if b['window'] == w])
            for w in WINDOWS}


def run(*, data_root, out, events=None):
    if out.exists():
        raise FileExistsError(out)
    r21.check_sha(PROTOCOL, PROTOCOL_SHA)
    spec = _read(PROTOCOL)
    if spec['version'] != model.VERSION or tuple(spec['competitions']) != CODES:
        raise ValueError('R23 model version or competition list differs from preregistration')
    package, manifest, locked, roots, manifests, event_archive, lock = _archives(data_root)
    observations = _observations(event_archive, events)
    source_index = _read(_checked(roots['r5'], manifests['r5'], 'evidence/source-index.json'))
    inputs = dict(version=model.VERSION, protocol_sha256=PROTOCOL_SHA, r22_source_commit=R22_SOURCE,
                  r22_data_commit=R22_DATA, r22_manifest_sha256=R22_MANIFEST_SHA,
                  r22_source_lock_sha256=manifest['files']['source-lock.json'],
                  r22_inputs_sha256=manifest['files']['replay/reference/inputs.json'],
                  r21_inputs_sha256=r22.R21_INPUTS_SHA, archive_manifest_sha256=r21.MANIFESTS,
                  extension_sha256=manifests['r11']['files']['extension.tar.gz'],
                  r14_archive_sha256=lock['r21_parent']['understat_archive_sha256'], events_sha256=EVENTS_SHA,
                  event_rows=len(observations), features=list(model.FEATURES), grid_shape=list(base.H.shape),
                  study_sha256=sha(Path(__file__)), core_sha256=sha(Path(model.__file__)),
                  runtime=dict(python=platform.python_version(), **{p: version(p) for p in ('numpy', 'scipy', 'lightgbm')}),
                  competitions={})
    metrics = dict(version=model.VERSION, protocol_sha256=PROTOCOL_SHA, formal_kpi_eligible=False,
                   evidence='previously inspected dates, chronological historical development replay',
                   prices='Original archived Bet365 closing categories and non-closing extension categories. '
                          'No authenticated bookmaker quote timestamps. Acquisition and kickoff are distinct.',
                   seats='Within each competition, window, date and arm, order by issued first-score probability, '
                         'then archived kickoff minute and stable fixture ID.',
                   competitions={}, weights={})
    all_rows, all_batches = [], []
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out.parent, prefix=f'.{out.name}-') as tmp:
        work = Path(tmp)
        r21.verified_archive(roots['r11'], manifests['r11'], 'extension.tar.gz', work / 'ext')
        r21.verified_archive(roots['r10'], manifests['r10'], 'replay/main.tar.gz', work / 'r10')
        extra = r21.extensions(work / 'ext')
        clocks = r21.extension_clocks(work / 'ext', extra)
        checked_archives = set()
        stage = work / 'study'
        for code in CODES:
            binding = locked['competitions'][code]
            games, settings, recipe, r22_fit, refs, hashes = _load_league(code, work, package, manifest,
                                                                         binding, roots, manifests)
            clocks.update(r21.original_clocks([g for g in games if g['date'] >= BOUNDS[0]], roots['r5'],
                                              manifests['r5'], source_index, checked_archives))
            output = stage / 'main' / code
            rows, batches, weights = _replay(code, games, extra[code], observations, settings, recipe,
                                             r22_fit, refs, clocks, binding, hashes, output)
            inputs['competitions'][code] = dict(hashes, base_fixtures=len(games),
                                                **{field: binding[field] for field in COUNTS.values()})
            metrics['competitions'][code] = _metrics(rows, batches)
            metrics['weights'][code] = weights
            write_new(output / 'metrics.json', dict(competition=code, weights=weights,
                                                     windows=metrics['competitions'][code]))
            all_rows.extend(rows)
            all_batches.extend(batches)
            print(code, 'dose', weights['dose'], 'train', weights['training_games'], 'refit', weights['forward_games'],
                  'reference windows', len(rows) - binding['validation_fixtures'], flush=True)
        if (Counter(row['window'] for row in all_rows) != EXPECTED
                or len({row['id'] for row in all_rows}) != len(all_rows)):
            raise ValueError('registered R23 windows are incomplete or overlap')
        digest = hashlib.sha256('\n'.join(sorted(row['id'] + ':' + row['source_sha256'] for row in all_rows)).encode())
        inputs['fixtures_sha256'] = digest.hexdigest()
        if inputs['fixtures_sha256'] != locked['fixtures_sha256']:
            raise ValueError('R23 historical fixture identities differ from the locked R22 population')
        metrics['overall'] = _metrics(all_rows, all_batches)
        write_new(stage / 'inputs.json', inputs)
        write_new(stage / 'metrics.json', metrics)
        stage.rename(out)
    return metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--data-root', required=True, type=Path, help='root of original 64efb37 checkout or export')
    parser.add_argument('--events', type=Path, help='exact R14 events.jsonl, defaults to the pinned archive member')
    parser.add_argument('--out', required=True, type=Path, help='new directory for the deterministic replay outputs')
    args = parser.parse_args()
    run(data_root=args.data_root, events=args.events, out=args.out)


if __name__ == '__main__':
    main()
