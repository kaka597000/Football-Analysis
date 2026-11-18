"""Compare named-book consensus with the frozen historical football score baseline."""
import argparse
from collections import Counter
import csv
from datetime import datetime
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile

import numpy as np

from .sequence_history import fixed_market_baseline, SOURCE_PREFIX
from .three_arm_model import _market_matrix, _quote_values, score_picks

BRANDS = ('B365', 'BFD', 'BMGM', 'BV', 'BW', 'CL', 'LB', 'PS', 'PP', 'SKB')
ARMS = ('baseline', 'equal', 'inverse_margin')
SIDES = ('home', 'draw', 'away')
HITS = ('top1', 'top3', 'wdl', 'over_under')
LOSS = ('score_log_loss', 'wdl_log_loss', 'wdl_brier', 'over_under_brier')
MIN_BRANDS = 3
OVERROUND_FLOOR = 0.0001


def book_panel(books):
    """Use one complete decimal home/draw/away triple per named brand at the caller's cutoff."""
    if not books or any(not str(k).strip() for k in books):
        raise ValueError('named quotes required')
    names = sorted(books)
    odds = np.asarray([books[k] for k in names], dtype=float)
    if odds.shape != (len(names), 3) or not np.isfinite(odds).all() or (odds <= 1).any():
        raise ValueError('complete decimal H/D/A above 1 required')
    inverse = 1 / odds
    total = inverse.sum(axis=1)
    probabilities = inverse / total[:, None]
    raw_weight = 1 / np.maximum(total - 1, OVERROUND_FLOOR)
    weight = raw_weight / raw_weight.sum()
    q1, q3 = np.quantile(probabilities, [.25, .75], axis=0)
    iqr = float(max(q3 - q1))
    bucket = 'lt_1pp' if iqr < .01 else ('1_to_lt3pp' if iqr < .03 else 'ge_3pp')
    median_odds = np.median(odds, axis=0)
    spread = (odds.max(axis=0) - median_odds) / median_odds
    favorite = int(np.argmax(probabilities.mean(axis=0)))
    fav_spread = float(spread[favorite])
    cutpoints = (0.02, 0.05, 0.10)
    labels = ('lt2pct', '2_to_lt5pct', '5_to_lt10pct', 'ge10pct')
    fav_group = labels[sum(fav_spread >= edge for edge in cutpoints)]
    return {'n_books': len(names), 'equal_wdl': probabilities.mean(axis=0).tolist(),
            'inverse_margin_wdl': (weight @ probabilities).tolist(), 'iqr': iqr,
            'bucket': bucket if len(names) >= MIN_BRANDS else 'insufficient',
            'decimal_spreads': spread.tolist(), 'panel_favorite': SIDES[favorite],
            'favorite_spread_group': fav_group if len(names) >= MIN_BRANDS else 'insufficient',
            'max_weight': float(weight.max()), 'weights': dict(zip(names, weight.tolist())),
            'floor_brands': [k for k, m in zip(names, total - 1) if m <= OVERROUND_FLOOR]}


def project_panel(matrix, panel, line, over_share):
    """Preserve the original Asian-total fair win-share while replacing only W/D/L."""
    if panel['n_books'] < MIN_BRANDS:
        return {name: matrix.copy() for name in ARMS[1:]}
    return {arm: _market_matrix(matrix, (np.asarray(panel[arm + '_wdl']), line, over_share))
            for arm in ARMS[1:]}


def checked(path, expected):
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError(f'archived checksum differs: {path}')
    return data


def records(data):
    return [json.loads(line) for line in gzip.decompress(data).splitlines()]


def panel_inputs(root):
    updates = root / 'workspace-updates'
    seq = updates / '20261118-sequence/history'
    study = updates / '20261118-transformer'
    history = study / 'history'
    transfer = study / 'transfer'
    seq_info = json.loads((seq / 'sequence-inputs.json').read_bytes())
    training = json.loads((history / 'season-validation.json').read_bytes())
    current = json.loads((transfer / 'europe-2627-validation.json').read_bytes())
    names = set(f'2526_{league}.csv' for league in ('D1', 'E0', 'F1', 'I1', 'SP1'))
    sources = {}
    blob = checked(root / seq_info['archive_path'], seq_info['archive_sha256'])
    with tarfile.open(fileobj=io.BytesIO(blob)) as archive:
        for name in names:
            raw = archive.extractfile(SOURCE_PREFIX + name).read()
            if hashlib.sha256(raw).hexdigest() != seq_info['csv_sha256'][name]:
                raise ValueError('original closing source changed: ' + name)
            sources[name] = raw
    for source in current['source_csv']:
        name = source['file']
        sources[name] = checked(transfer / 'raw' / name, source['sha256'])
    csv_rows = {name: list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))) for name, raw in sources.items()}
    early_inputs = records(checked(seq / 'samples-2526.jsonl.gz', seq_info['files']['samples-2526.jsonl.gz']['sha256']))
    early = records(checked(history / 'season-holdout.jsonl.gz', training['outputs_sha256']['season-holdout.jsonl.gz']))
    later = records(checked(transfer / 'europe-2627-predictions.jsonl.gz', current['prediction_archive_sha256']))
    if len(early) != len(early_inputs) or [r['match_id'] for r in early] != [r['match_id'] for r in early_inputs]:
        raise ValueError('old held-out reference identity mismatches')
    for target, row in zip(early, early_inputs):
        target.update(source=[row['source_file'], row['source_line']], teams=[row['home'], row['away']],
                      goal_rates=row['rates'], raw_prices=row['market'])
    if (len(early), len(later), len({r['match_id'] for r in early + later})) != (1737, 481, 2218):
        raise ValueError('registered match populations changed')
    hashes = {k: hashlib.sha256(v).hexdigest() for k, v in sorted(sources.items())}
    return csv_rows, [('2526', early), ('2627', later)], hashes


def source_prices(raw, target):
    expected = (datetime.strptime(raw['Date'], '%d/%m/%Y').date().isoformat(),
                raw['HomeTeam'], raw['AwayTeam'], int(raw['FTHG']), int(raw['FTAG']))
    if expected != (target['matchday'], *target['teams'], *target['actual']):
        raise ValueError('date, teams or final score conflict for ' + target['match_id'])
    books = {}
    for brand in BRANDS:
        cols = [raw.get(brand + 'C' + k) for k in 'HDA']
        if not all(cols):
            continue
        odds = [float(value) for value in cols]
        if min(odds) > 1 and np.isfinite(odds).all():
            books[brand] = odds
    if (books['B365'] != [target['raw_prices']['h2h'][k] for k in SIDES]
            or [float(raw['B365C>2.5']), float(raw['B365C<2.5'])] !=
            [target['raw_prices']['total'][k] for k in ('over', 'under')]):
        raise ValueError('reference closing prices differ for ' + target['match_id'])
    return books


def forecast(matrix, line):
    picks = score_picks(matrix, line=line)
    flat = np.argsort(-matrix.ravel(), kind='stable')[:3]
    leaders = np.transpose(np.unravel_index(flat, matrix.shape))
    return {'scores': [f'{h}-{a}' for h, a in leaders], 'score_p': matrix.ravel()[flat].tolist(),
            'wdl': picks['wdl'], 'wdl_p': [picks['wdl_probabilities'][k] for k in SIDES],
            'total': picks['total']['direction'], 'over_p': picks['total']['settlements']['over']['win']}


def outcomes(matrix, pred, result):
    home, away = result
    actual = f'{home}-{away}'
    side = 0 if home > away else (2 if home < away else 1)
    over = home + away > 2.5
    return {'top1': int(pred['scores'][0] == actual), 'top3': int(actual in pred['scores']),
            'wdl': int(pred['wdl'] == SIDES[side]), 'over_under': int((pred['total'] == 'over') == over),
            'score_log_loss': float(-np.log(matrix[home, away])),
            'wdl_log_loss': float(-np.log(pred['wdl_p'][side])),
            'wdl_brier': float(np.sum((np.array(pred['wdl_p']) - np.eye(3)[side])**2)),
            'over_under_brier': (pred['over_p'] - over)**2}


def evaluate(target, raw):
    books = source_prices(raw, target)
    panel = book_panel(books)
    base = fixed_market_baseline(target['goal_rates'], target['raw_prices'])
    _, line, over = _quote_values(target['raw_prices'], None, proxy=True)
    if line != 2.5:
        raise ValueError('registered common O/U 2.5 evaluation requires 2.5')
    arms = {'baseline': base, **project_panel(base, panel, line, over)}
    preds = {k: forecast(m, line) for k, m in arms.items()}
    saved, actual = target['forecasts']['baseline'], target['actual']
    first = preds['baseline']
    if ((first['scores'][0], first['wdl'], first['total']) !=
            (saved['primary'], saved['wdl'], saved['total']['direction'])
            or not np.allclose(first['wdl_p'], [saved['wdl_probabilities'][k] for k in SIDES], atol=1e-12, rtol=0)
            or abs(first['score_p'][0] - saved['primary_probability']) > 1e-12):
        raise ValueError('could not reconstruct archived market baseline')
    for arm in ARMS:
        candidate = preds[arm]
        if abs(candidate['over_p'] - over) > 1e-11 or candidate['total'] != first['total']:
            raise ValueError('Asian total baseline changed')
        if arm != 'baseline' and panel['n_books'] >= MIN_BRANDS:
            if not np.allclose(candidate['wdl_p'], panel[arm + '_wdl'], atol=1e-11, rtol=0):
                raise ValueError('target W/D/L not achieved')
    return {**{k: target[k] for k in ('match_id', 'matchday', 'cohort', 'teams', 'source', 'goal_rates')},
            'actual': actual, 'line': line, 'over_share': over, 'quotes': books, 'panel': panel,
            'forecasts': preds, 'outcomes': {k: outcomes(m, preds[k], actual) for k, m in arms.items()}}


def summary(rows):
    return {'n': len(rows), 'arms': {arm: {
        'hits': {metric: sum(r['outcomes'][arm][metric] for r in rows) for metric in HITS},
        'mean_loss': {metric: float(np.mean([r['outcomes'][arm][metric] for r in rows]))
                      if rows else None for metric in LOSS}} for arm in ARMS}}


def paired(rows, candidate, reference):
    days = sorted({r['matchday'] for r in rows})
    indices = {d: i for i, d in enumerate(days)}
    labels = np.asarray([indices[r['matchday']] for r in rows])
    counts = np.bincount(labels)
    rng = np.random.default_rng(20261118)
    samples = rng.integers(0, len(days), (2000, len(days)))
    totals = counts[samples].sum(axis=1)
    results = {}
    for m in HITS + LOSS:
        delta = np.asarray([r['outcomes'][candidate][m] - r['outcomes'][reference][m] for r in rows])
        daily = np.bincount(labels, weights=delta)
        result = {'mean_delta': float(delta.mean()),
                  'paired_day_95pct': np.quantile(daily[samples].sum(axis=1) / totals, [.025, .975]).tolist()}
        if m in HITS:
            result.update(gained=int((delta > 0).sum()), lost=int((delta < 0).sum()), net=int(delta.sum()))
        results[m] = result
    return results


def grouped(rows):
    result = summary(rows)
    dispersion = {}
    for k in ('lt_1pp', '1_to_lt3pp', 'ge_3pp', 'insufficient'):
        selected = [r for r in rows if r['panel']['bucket'] == k]
        cell = summary(selected)
        if len({r['matchday'] for r in selected}) >= 2:
            cell['paired_vs_baseline'] = {arm: paired(selected, arm, 'baseline') for arm in ARMS[1:]}
        dispersion[k] = cell
    result.update(complete_panel_only=summary([r for r in rows if r['panel']['n_books'] >= MIN_BRANDS]),
                  by_dispersion=dispersion,
                  by_favorite_decimal_spread={k: summary([r for r in rows if r['panel']['favorite_spread_group'] == k])
                                              for k in ('lt2pct', '2_to_lt5pct', '5_to_lt10pct', 'ge10pct', 'insufficient')},
                  by_cohort={k: summary([r for r in rows if r['cohort'] == k]) for k in sorted({r['cohort'] for r in rows})},
                  n_brands=dict(sorted(Counter(r['panel']['n_books'] for r in rows).items())),
                  reference_fallback=sum(r['panel']['n_books'] < MIN_BRANDS for r in rows),
                  at_margin_floor=sum(bool(r['panel']['floor_brands']) for r in rows),
                  majority_weight=sum(r['panel']['max_weight'] > .5 for r in rows),
                  paired={f'{a}_minus_{b}': paired(rows, a, b) for a, b in
                          (('equal', 'baseline'), ('inverse_margin', 'baseline'), ('inverse_margin', 'equal'))})
    return result


def equivalent(original, replay):
    """Archive hashes commit original bytes; replay tolerates only floating arithmetic."""
    if isinstance(original, dict) and isinstance(replay, dict):
        return original.keys() == replay.keys() and all(equivalent(original[k], replay[k]) for k in original)
    if isinstance(original, list) and isinstance(replay, list):
        return len(original) == len(replay) and all(equivalent(a, b) for a, b in zip(original, replay))
    if isinstance(original, float) and isinstance(replay, (int, float)):
        return abs(original - replay) <= 1e-11
    return type(original) is type(replay) and original == replay


def run(data, out, verify=False):
    csv_rows, cohorts, hashes = panel_inputs(data)
    report = {'source_csv_sha256': hashes, 'seasons': {}}
    all_rows = []
    for season, targets in cohorts:
        rows = []
        for target in targets:
            filename, line = target['source']
            raw = csv_rows[filename][line - 2]
            rows.append(dict(season=season, **evaluate(target, raw)))
        report['seasons'][season] = grouped(rows)
        all_rows.extend(rows)
    report['policy'] = {'version': 'book-consensus-v1', 'historical_closing_proxy': True, 'automatic_promotion': False,
                        'proxy_warning': 'Source closing columns have no individual quote timestamps. No live or blind claim.',
                        'formula': 'Normalize each H/D/A inverse-odds triple. Compare equal with weights proportional to 1/max(sum(inverse_odds)-1,0.0001).',
                        'dispersion': 'Maximum across sides of interquartile range of de-vigged named-brand probabilities. Fixed 1pp and 3pp cutpoints.',
                        'additional_diagnostic': 'Descriptive author-style (max minus median)/median of decimal prices by side; report consensus favorite in 2%, 5%, 10% groups. No effect on predictions.',
                        'book_columns': list(BRANDS), 'min_complete_brands': MIN_BRANDS,
                        'paired_intervals': '2000 seeded resamples of whole matchdays within each season. Deltas per match; negative loss differences favor candidate.'}
    report_path, rows_path = out / 'report.json', out / 'predictions.jsonl.gz'
    if verify:
        saved = json.loads(report_path.read_bytes())
        archived = checked(rows_path, saved.pop('prediction_archive_sha256'))
        if not equivalent(records(archived), all_rows) or not equivalent(saved, json.loads(json.dumps(report))):
            raise ValueError('archived bookmaker-consensus predictions or report differ from replay')
    else:
        out.mkdir(parents=True, exist_ok=True)
        packed = gzip.compress(b''.join(json.dumps(r, sort_keys=True, separators=(',', ':'), allow_nan=False).encode() + b'\n'
                                        for r in all_rows), mtime=0)
        rows_path.write_bytes(packed)
        report['prediction_archive_sha256'] = hashlib.sha256(packed).hexdigest()
        report_path.write_text(json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + '\n')
    return {season: result['arms'] for season, result in report['seasons'].items()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    print(json.dumps(run(args.data, args.out, args.verify), sort_keys=True))
