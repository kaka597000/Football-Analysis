"""Offline R21 five-league historical replay of the registered score-dependence study."""

import argparse
from collections import Counter, defaultdict
import csv
from datetime import date, datetime, time
import gzip
import hashlib
import io
from pathlib import Path
import tarfile
import tempfile

import numpy as np

from . import all_scope_model as base
from . import all_scope_study as panels
from . import continuation_forward as r7_forward
from . import continuation_scores as r7
from . import continuation_study as archive
from . import r8_score_study as control
from . import r10_shot_model as decision
from . import score_dependence as model
from .goal_state_study import read_lines, sha, write_new
from .paired_kpi_inference import _decisions, compare
from .r9_score_study import verified_archive
from .ranked_score_study import sweep
from .territory_study import CODES, extensions


PROTOCOL = Path(__file__).resolve().parents[2] / 'research/20261125-r21-score-dependence/protocol.json'
PROTOCOL_SHA = 'a4eb968e0965882c0844235fb0e9448a25a172f4a4ea8f16ec6d5e490439ed52'
EVENTS_SHA = '63f853739198298cbbde9dfce2cb02c582cb9dccb4070215c51453325f553bd5'
MANIFESTS = {
    'r5': 'f3b6fa64903e97a6c74966362894c3378dfe5bca03a4a84709d4c694946ed3d8',
    'r7': 'e2ed191e29150ba59bf9623ce2a44b265e8b71c9e777d9197ec869975a957df3',
    'r10': 'f136707b0d5cbbac883f74cc9ce8dc85e9bc434ece591a2ba8c72c327faaf8cb',
    'r11': 'd76cdbc338407c9962122f70aa69a071c0df51203fc2cb6e74d9a722054543bb',
}
BOUNDS = ('2024-07-01', '2025-07-01', '2026-07-01')
DIVISIONS = dict(zip(CODES, ('E0', 'SP1', 'D1', 'I1', 'F1'), strict=True))
ARMS = ('context', 'constant')
HEADS = ('score', 'wdl', 'total')
WINDOWS = ('evaluation', 'recent', 'november_extension')
PAIRS = [('candidate', arm) for arm in ('r7', 'market', 'constant', 'constant_matched', 'r10')] + [
    ('fixed_context', 'fixed_constant'), ('half_context', 'half_constant'),
]


def check_sha(path, expected):
    if sha(path) != expected:
        raise ValueError('source checksum mismatch: ' + str(path))


def index_rows(rows, ids):
    index = {r['id']: r for r in rows}
    if len(index) != len(rows) or set(index) != set(ids):
        raise ValueError('source forecast population differs')
    return index


def original_controls(game, window, r5_row, r7_row, r10_row):
    expected = dict(id=game['id'], cohort=game['competition'], date=game['date'], home=game['home'],
                    away=game['away'], stage=game['stage'], actual=[game['home_goals'], game['away_goals']],
                    line=base.actual_line(game), source_sha256=game['source_sha256'], window=window)
    for row in (r5_row, r7_row, r10_row):
        if any(row[k] != v for k, v in expected.items()):
            raise ValueError('fixture, line, archive window or source differs: ' + game['id'])
    market = r5_row['forecast']['market']
    original = r7_row['forecast']['candidate']
    if (original != r10_row['forecast']['r7'] or market != r7_row['forecast']['market']
            or market != r10_row['forecast']['market']):
        raise ValueError('published R7 or market decisions differ: ' + game['id'])
    return original, market, r10_row['forecast']['candidate']


def csv_rows(data, digest):
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError('raw football-data response checksum differs')
    reader = csv.DictReader(io.StringIO(data.decode('utf-8-sig'), newline=''))
    return {reader.line_num: row for row in reader}


def source_clock(game, raw, *, extension=False):
    """Bind the kickoff minute and the original market category to one physical result row."""
    source, = game['sources']
    div = DIVISIONS[game['competition']]
    expected_url = f"https://www.football-data.co.uk/mmz4281/{game['season']}/{div}.csv"
    row = raw[source['row']]
    stamp = datetime.strptime(row['Date'], '%d/%m/%Y').date().isoformat()
    if (source['sha256'] != game['source_sha256'] or source['url'] != expected_url or row['Div'] != div
            or (stamp, row['HomeTeam'], row['AwayTeam'], int(row['FTHG']), int(row['FTAG'])) !=
            (game['date'], game['home'], game['away'], game['home_goals'], game['away_goals'])):
        raise ValueError('raw kickoff fixture differs: ' + game['id'])
    prefix = 'B365' if extension else 'B365C'
    for prices, columns, keys in ((game['h2h'], 'HDA', base.SIDES),
                                 (game['total'], ('>2.5', '<2.5'), ('over', 'under'))):
        quotes = [row.get(prefix + name) for name in columns]
        if all(quotes):
            if prices is None or any(float(q) != prices[k] for q, k in zip(quotes, keys, strict=True)):
                raise ValueError('raw source prices differ: ' + game['id'])
        elif prices:
            raise ValueError('missing original quote: ' + game['id'])
    if game['total'] and base.actual_line(game) != 2.5:
        raise ValueError('raw totals line differs: ' + game['id'])
    minute = row['Time']
    if time.fromisoformat(minute).strftime('%H:%M') != minute:
        raise ValueError('invalid original kickoff minute: ' + game['id'])
    return dict(source_time=minute, source_field='football-data CSV Time', timezone=None, utc=None)


def original_clocks(games, root, manifest, source_index, checked_archives):
    by_archive = defaultdict(lambda: defaultdict(list))
    for g in games:
        digest = g['source_sha256']
        entry = source_index[digest]
        if g['sources'][0]['url'] not in entry['sources']:
            raise ValueError('R5 kickoff source URL differs: ' + g['id'])
        by_archive[entry['archive']][digest].append(g)
    clocks = {}
    for name, groups in by_archive.items():
        path = root / 'evidence' / name
        if path not in checked_archives:
            check_sha(path, manifest['files']['evidence/' + name])
            checked_archives.add(path)
        with tarfile.open(path) as bundle:
            for digest, fixtures in groups.items():
                entry = source_index[digest]
                data = gzip.decompress(bundle.extractfile(entry['member']).read())
                if len(data) != entry['bytes']:
                    raise ValueError('R5 source byte count differs')
                raw = csv_rows(data, digest)
                for g in fixtures:
                    clocks[g['id']] = source_clock(g, raw)
    return clocks


def extension_clocks(root, grouped):
    sources = {s['league']: s for s in control.read(root / 'manifest.json')['sources']}
    with (root / 'market-rows.csv').open(encoding='utf-8-sig', newline='') as stream:
        market = {r['id']: r for r in csv.DictReader(stream)}
    clocks = {}
    for code in CODES:
        source = sources[code]
        raw = csv_rows((root / source['file']).read_bytes(), source['sha256'])
        for g in grouped[code]:
            row = market[g['id']]
            clock = source_clock(g, raw, extension=True)
            ref, = g['sources']
            if (source['sha256'] != ref['sha256'] or source['url'] != ref['url']
                    or int(row['csv_row_physical']) != ref['row'] or row['source_url'] != ref['url']
                    or row['csv_kickoff_time_local'] != clock['source_time']
                    or row['acquired_at_utc'] != source['acquired_at_utc']):
                raise ValueError('extension clock or source differs: ' + g['id'])
            clocks[g['id']] = dict(clock, timezone=row['csv_local_timezone'], utc=row['kickoff_utc'],
                                   acquired_at_utc=source['acquired_at_utc'])
    return clocks


def check_design(games, x, metadata):
    if (x.shape != (len(games), 3) or not np.isfinite(x).all() or not np.all(x[:, 0] == 1.)
            or len(metadata) != len(games)):
        raise ValueError('misaligned historical design')
    for g, meta in zip(games, metadata, strict=True):
        if (meta['competition'], meta['date'], meta['cutoff']) != (g['competition'], g['date'], g['date']):
            raise ValueError('historical design target or cutoff differs: ' + g['id'])
        for side in ('home', 'away'):
            detail = meta[side]
            entries = detail['games']
            days = [(date.fromisoformat(g['date']) - date.fromisoformat(e['date'])).days for e in entries]
            if (len(entries) > 8 or len({e['source_id'] for e in entries}) != len(entries)
                    or any(d < 1 or d > 150 for d in days)
                    or detail['usable'] != sum(e['usable'] for e in entries)):
                raise ValueError('invalid as-of historical exposure: ' + g['id'])


def probabilities(score, wdl, picks, actual):
    cells = [tuple(map(int, s.split('-'))) for s in picks['top3']]
    values = [float(score[cell]) for cell in cells]
    return dict(p_observed=float(score[tuple(actual)]), p_first=values[0], p_three=sum(values),
                p_ordered=values, wdl=np.bincount(base.RESULT.ravel(), weights=wdl.ravel(), minlength=3).tolist())


def record(game, window, prior, x, meta, fits, doses, original, market, clock, *, r10_row=None):
    line = base.actual_line(game)
    actual = [game['home_goals'], game['away_goals']]
    alternatives = {arm: model.matrix(prior, x, fits[arm]) for arm in ARMS}
    forecasts = dict(r7=original, market=market)
    quality = dict(r7=probabilities(prior, prior, original, actual))
    if r10_row is not None:
        forecasts['r10'] = r10_row['forecast']['candidate']
        quality['r10'] = r10_row['quality']['candidate']
    if game['paired_market'] and market.get('top3'):
        raw, _, _, _ = base.baseline(game, np.full_like(prior, 1. / prior.size))
        quality['market'] = probabilities(raw, raw, market, actual)
    variants = [('candidate', 'context', doses['context']), ('constant', 'constant', doses['constant']),
                ('constant_matched', 'constant', doses['context'])]
    for arm in ARMS:
        variants.extend([(label + '_' + arm, arm, dict.fromkeys(HEADS, dose))
                         for label, dose in (('half', .5), ('fixed', 1.))])
    for key, arm, strengths in variants:
        pick, heads = decision.decisions(prior, alternatives[arm], strengths, line, original)
        forecasts[key] = pick
        quality[key] = probabilities(heads['score'], heads['wdl'], pick, actual)
    return dict(id=game['id'], cohort=game['competition'], stage=game['stage'], date=game['date'],
                home=game['home'], away=game['away'], actual=actual, line=line, window=window,
                source_sha256=game['source_sha256'], sources=game['sources'], bookmaker=game['bookmaker'],
                market_time_basis=game['market_time_basis'], prices=dict(h2h=game['h2h'], total=game['total']),
                r7_basis='published_heldout_row' if r10_row is not None else 'frozen_R7_recipe_on_extension',
                kickoff=clock, features=x.tolist(), process=meta, forecast=forecasts, quality=quality)


def rank_batches(records):
    """Rank every entrant within its competition and calendar day before settling selected seats."""
    groups = defaultdict(list)
    for r in records:
        groups[r['cohort'], r['window'], r['date']].append(r)
    batches = []
    for (code, window, day), rows in sorted(groups.items()):
        def kickoff_key(r):
            return r['kickoff']['source_time'], r['id']
        start_order = {r['id']: i for i, r in enumerate(sorted(rows, key=kickoff_key), 1)}
        ranked = sorted(rows, key=lambda r: (-r['quality']['candidate']['p_first'], *kickoff_key(r)))
        selected = []
        scores = {}
        for rank, r in enumerate(ranked, 1):
            r.update(batch_rank=rank, batch_size=len(rows), seat=rank if rank <= 3 else None,
                     kickoff_order=start_order[r['id']])
            if rank <= 3:
                pick = r['forecast']['candidate']
                _, outcomes, settlement = _decisions(pick, r['actual'], r['line'])
                scores[r['id']] = '-'.join(map(str, r['actual']))
                selected.append(dict(match_id=r['id'], seat=rank, first_probability=r['quality']['candidate']['p_first'],
                                     kickoff=r['kickoff'], **pick, actual=r['actual'], outcomes=outcomes,
                                     total_settlement=settlement))
        counts = sweep(selected, scores)
        batches.append(dict(cohort=code, window=window, date=day, batch_size=len(rows),
                            ordered_ids=[r['id'] for r in ranked], selected=selected, short=len(rows) < 3,
                            primary_sweep=bool(counts['primary_sweeps']) if len(rows) >= 3 else None,
                            primary_hits=counts['primary_hits'], three_hits=counts['displayed_three_hits']))
    return batches


def settle_batches(batches):
    seats = {}
    for seat in (1, 2, 3):
        rows = [b['selected'][seat-1] for b in batches if len(b['selected']) >= seat]
        metrics = {}
        for key in ('score_top1', 'score_top3', 'wdl', 'actual_ou'):
            eligible = [s['outcomes'][key] for s in rows if s['outcomes'][key] is not None]
            metrics[key] = dict(hits=sum(eligible), eligible=len(eligible))
        seats[str(seat)] = dict(occupied=len(rows), metrics=metrics)
    full = [b for b in batches if not b['short']]
    short = [b for b in batches if b['short']]
    return dict(batches=len(batches), full_batches=len(full), short_batches=len(short),
                planned_slots=3*len(batches), occupied_slots=sum(len(b['selected']) for b in batches),
                three_first_sweeps=sum(b['primary_sweep'] for b in full),
                short_sizes=dict(sorted(Counter(str(b['batch_size']) for b in short).items())),
                per_seat=seats)


def summarize(rows, batches):
    tracks, paired = {}, {}
    for candidate, baseline in PAIRS:
        stats = compare(rows, candidate=candidate, baseline=baseline)
        tracks.update(stats['tracks'])
        paired[candidate + '_vs_' + baseline] = stats['paired']
    exposure = Counter((r['process']['home']['usable'] > 0) + (r['process']['away']['usable'] > 0) for r in rows)
    return dict(population=len(rows), tracks=tracks, paired=paired, bootstrap=stats['bootstrap'],
                side_exposure={str(k): exposure[k] for k in (0, 1, 2)}, batch_results=settle_batches(batches))


def replay(code, games, extra, observations, refs, settings, recipe, baseline_sha, clocks, output):
    all_games = games + extra
    keys = [(g['date'], g['id']) for g in all_games]
    if (not extra or extra[0]['date'] <= games[-1]['date'] or keys != sorted(keys)
            or len(set(g['id'] for g in all_games)) != len(all_games)
            or any(g['competition'] != code for g in all_games)):
        raise ValueError('incomplete, overlapping or unordered fixture population: ' + code)
    cut, test, recent = (sum(g['date'] < b for g in games) for b in BOUNDS)
    if (not 0 < cut < test < recent < len(games) or tuple(settings['bounds']) != BOUNDS
            or settings['training_through'] != games[cut-1]['date'] or recipe['through'] != games[-1]['date']):
        raise ValueError('R7 historical or forward training boundary differs: ' + code)
    held_ids = [g['id'] for g in games[test:]]
    r5_refs, r7_refs, r10_refs = (index_rows(part, held_ids) for part in refs)
    x, metadata = model.Design(observations, code).walk(all_games)
    check_design(all_games, x, metadata)
    y = np.array([[g['home_goals'], g['away_goals']] for g in all_games])
    anchors, z, _ = r7.inputs(all_games, code)
    priors = control.matrices(anchors[cut:len(games)], z[cut:len(games)], settings)
    validation = priors[:test-cut]
    fits, doses, trials = {}, {}, {}
    for arm in ARMS:
        fits[arm] = model.fit(anchors[:cut], x[:cut], y[:cut], constant=arm == 'constant')
        alternatives = [model.matrix(m, features, fits[arm]) for m, features in zip(validation, x[cut:test], strict=True)]
        doses[arm], trials[arm] = decision.choose(games[cut:test], validation, alternatives)

    output.mkdir(parents=True)
    fit_path = output / 'fit.json'
    write_new(fit_path, dict(version=model.VERSION, competition=code, bounds=list(BOUNDS), train_n=cut,
                             validation_n=test-cut, training_through=games[cut-1]['date'],
                             validation_through=games[test-1]['date'], fitted=fits, doses=doses, trials=trials))
    records = []
    for i, g in enumerate(games[test:], test):
        mid = g['id']
        window = WINDOWS[0] if i < recent else WINDOWS[1]
        original, market, _ = original_controls(g, window, r5_refs[mid], r7_refs[mid], r10_refs[mid])
        records.append(record(g, window, priors[i-cut], x[i], metadata[i], fits, doses, original, market,
                              clocks[mid], r10_row=r10_refs[mid]))
    for i, g in enumerate(extra, len(games)):
        m = r7_forward.matrix(g, recipe)
        raw, _, _, _ = base.baseline(g, np.full_like(m, 1. / m.size))
        records.append(record(g, WINDOWS[2], m, x[i], metadata[i], fits, doses, base.pick(m, base.actual_line(g)),
                              archive.market_control(g, raw), clocks[g['id']]))
    batches = rank_batches(records)
    archive.put(output / 'forecasts.jsonl.gz', records)
    archive.put(output / 'seats.jsonl.gz', batches)
    archive.put(output / 'exposure.jsonl.gz', (
        dict(id=g['id'], cohort=code, date=g['date'], home=g['home'], away=g['away'],
             features=features.tolist(), process=detail)
        for g, features, detail in zip(all_games, x, metadata, strict=True)))
    full = {arm: model.fit(anchors, x, y, constant=arm == 'constant') for arm in ARMS}
    write_new(output / 'forward.json', dict(version=model.VERSION, competition=code, through=extra[-1]['date'],
              baseline_sha256=baseline_sha, fit_sha256=sha(fit_path), fitted=full, doses=doses))
    return records, batches


def run(r5, r7_dir, r10, r11, events, out):
    if out.exists():
        raise FileExistsError(out)
    check_sha(PROTOCOL, PROTOCOL_SHA)
    spec = control.read(PROTOCOL)
    if spec['version'] != model.VERSION or tuple(spec['competitions']) != CODES:
        raise ValueError('model version or leagues differ from preregistration')
    check_sha(events, EVENTS_SHA)
    observed = list(read_lines(events))
    roots = dict(r5=r5, r7=r7_dir, r10=r10, r11=r11)
    manifests = {}
    for key, folder in roots.items():
        check_sha(folder / 'manifest.json', MANIFESTS[key])
        manifests[key] = control.read(folder / 'manifest.json')
    index_path = r5 / 'evidence/source-index.json'
    check_sha(index_path, manifests['r5']['files']['evidence/source-index.json'])
    source_index = control.read(index_path)
    checked_archives = set()
    inputs = dict(version=model.VERSION, protocol_sha256=PROTOCOL_SHA, events_sha256=EVENTS_SHA,
                  extension_manifest_sha256=MANIFESTS['r11'], source_events=len(observed),
                  archive_manifest_sha256=MANIFESTS, extension_sha256=manifests['r11']['files']['extension.tar.gz'],
                  competitions={})
    all_rows, all_batches = [], []
    metrics = dict(version=model.VERSION, evidence='date-held historical research replay', formal_kpi_eligible=False,
                   ranking='within competition and date: descending probability of issued first, CSV kickoff minute, fixture ID',
                   r10_coverage='original R10 held-out rows in evaluation and recent only',
                   competitions={}, overall={})
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out.parent) as temp:
        work = Path(temp)
        stage = work / 'study'
        verified_archive(r11, manifests['r11'], 'extension.tar.gz', work / 'ext')
        verified_archive(r10, manifests['r10'], 'replay/main.tar.gz', work / 'r10')
        extra = extensions(work / 'ext')
        clocks = extension_clocks(work / 'ext', extra)
        for code in CODES:
            folder = work / code
            name = 'models/' + code + '.tar.gz'
            for key in ('r5', 'r7'):
                verified_archive(roots[key], manifests[key], name, folder / key)
            panel = folder / 'r5/panel.jsonl'
            old = folder / 'r7/main'
            ten = work / 'r10/main' / code
            files = dict(panel=panel, r7_fit=old/'fit.json', r7_forward=old/'forward.json',
                         r7_forecasts=old/'forecasts.jsonl.gz', r10_fit=ten/'fit.json', r10_forecasts=ten/'forecasts.jsonl.gz',
                         r5_evaluation=folder/'r5/study/evaluation.jsonl', r5_recent=folder/'r5/study/recent.jsonl')
            hashes = {key: sha(path) for key, path in files.items()}
            settings, recipe, shot = (control.read(files[key]) for key in ('r7_fit', 'r7_forward', 'r10_fit'))
            if (any(s['competition'] != code for s in (settings, recipe, shot))
                    or settings['version'] != r7.VERSION or recipe['version'] != r7.VERSION or shot['version'] != decision.VERSION
                    or settings['source_panel_sha256'] != hashes['panel'] or shot['panel_sha256'] != [hashes['panel']]
                    or recipe['fit_sha256'] != hashes['r7_fit'] or tuple(shot['bounds']) != BOUNDS):
                raise ValueError('archived fit, recipe or panel differs: ' + code)
            inputs['competitions'][code] = hashes
            games = panels.check_panel(panel, code)
            held = [g for g in games if g['date'] >= BOUNDS[1]]
            clocks.update(original_clocks(held, r5, manifests['r5'], source_index, checked_archives))
            r5_rows = [dict(r, window=w) for w in WINDOWS[:2] for r in read_lines(files['r5_' + w])]
            refs = (r5_rows, archive.read_gzip(files['r7_forecasts']), archive.read_gzip(files['r10_forecasts']))
            rows, batches = replay(code, games, extra[code], observed, refs, settings, recipe, hashes['r7_forward'],
                                   clocks, stage / 'main' / code)
            metrics['competitions'][code] = {
                w: summarize([r for r in rows if r['window'] == w], [b for b in batches if b['window'] == w])
                for w in WINDOWS}
            all_rows.extend(rows)
            all_batches.extend(batches)
            print(code, len(games), '+', len(extra[code]), 'historical fixtures;', len(rows), 'test rows', flush=True)
        for w in WINDOWS:
            metrics['overall'][w] = summarize([r for r in all_rows if r['window'] == w],
                                              [b for b in all_batches if b['window'] == w])
        check_sha(events, EVENTS_SHA)
        write_new(stage / 'inputs.json', inputs)
        write_new(stage / 'metrics.json', metrics)
        stage.rename(out)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ('r5', 'r7', 'r10', 'r11', 'events', 'out'):
        parser.add_argument('--' + field, required=True, type=Path)
    a = parser.parse_args()
    run(a.r5, a.r7, a.r10, a.r11, a.events, a.out)


if __name__ == '__main__':
    main()
