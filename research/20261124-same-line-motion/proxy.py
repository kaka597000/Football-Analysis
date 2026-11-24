"""Read the pinned Football-Data.co.uk Bet365 columns for a date-only development proxy."""

import argparse
import csv
from collections import Counter, defaultdict
from datetime import datetime
from hashlib import sha256
from io import StringIO
import json
import math
from pathlib import Path
import tarfile


DATA_COMMIT = 'a6d45024cf6cf622f05e4b378ab53cfe2d6ae329'
ARCHIVE_BLOB = '73985fb180885a54ee760b849b63096dd8f3e4e7'
ARCHIVE_SHA256 = '7c106ee66a816da84dbbfbf254633b55b7c25e610e7d0f31bf168d68f61d9659'
LEAGUES = {'E0': 'eng.1', 'SP1': 'esp.1', 'D1': 'ger.1', 'I1': 'ita.1',
           'F1': 'fra.1', 'N1': 'ned.1'}
TRAIN = ('1920', '2021', '2122')
TEST = ('2324', '2425', '2526')
IDENTITY = ('Div', 'Date', 'HomeTeam', 'AwayTeam')
PRICES = ('B365CH', 'B365CD', 'B365CA', 'B365>2.5', 'B365<2.5', 'B365C>2.5', 'B365C<2.5')
LABELS = ('FTHG', 'FTAG')
CLOCK = 'DATE_ONLY_EARLY_AND_CLOSING_LABELS_NO_QUOTE_CLOCK'


def as_date(text):
    for fmt in ('%d/%m/%Y', '%d/%m/%y', '%Y-%m-%d'):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError(f'unrecognized match date {text!r}')


def price(text):
    if not text.strip():
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) and value > 1 else None


def json_text(obj):
    return json.dumps(obj, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(',', ':'))


def project(raw, columns):
    # The archived CSV physically contains both prices and outcomes. Only the
    # requested column indexes are selected; the inputs path never selects goals.
    parsed = csv.reader(StringIO(raw.decode('utf-8-sig'), newline=''))
    header = next(parsed)
    if any(header.count(name) != 1 for name in columns):
        raise ValueError('required CSV column missing or ambiguous')
    indexes = [header.index(name) for name in columns]
    for line, values in enumerate(parsed, start=2):
        if values:
            yield line, {k: values[i] if i < len(values) else '' for k, i in zip(columns, indexes)}


def rows_for(raw, name, season, league, mode):
    split = 'train' if season in TRAIN else 'test'
    fields = IDENTITY + (PRICES if mode == 'inputs' else LABELS)
    start_year = 2000 + int(season[:2])
    for line, row in project(raw, fields):
        if (row['Div'] != league or not all(row[k].strip() for k in IDENTITY)
                or row['HomeTeam'] == row['AwayTeam']):
            raise ValueError(f'invalid fixture identity at {name}:{line}')
        day = as_date(row['Date'])
        if day.year not in (start_year, start_year + 1):
            raise ValueError(f'fixture outside listed season at {name}:{line}')
        if split == 'test' and (day.year != start_year or not 8 <= day.month <= 11):
            continue
        date = day.isoformat()
        # Same identity tuple and canonical digest as the project's closing_market_ablation.
        match = sha256(json_text([league, date, row['HomeTeam'], row['AwayTeam']]).encode()).hexdigest()
        item = dict(id=match, match_id=match, split=split, date=date, competition=LEAGUES[league],
                    season=season, home=row['HomeTeam'], away=row['AwayTeam'], source_file=name,
                    source_line=line)
        if mode == 'inputs':
            h, d, a, o, u, co, cu = (price(row[k]) for k in PRICES)
            item.update(provider='Bet365', clock=CLOCK, source='football-data.co.uk',
                        opening_quoted_at=None, current_quoted_at=None, decision_cutoff_at=None,
                        opening_total=dict(line=2.5, over=o, under=u),
                        current_total=dict(line=2.5, over=co, under=cu),
                        latest_h2h=dict(home=h, draw=d, away=a))
        else:
            scores = [int(row[k]) if row[k].strip() else None for k in LABELS]
            if any(g is not None and g < 0 for g in scores):
                raise ValueError(f'negative regular-time score at {name}:{line}')
            item.update(home_goals=scores[0], away_goals=scores[1],
                        score=scores if None not in scores else None)
        yield item


def completeness(row, counter):
    counter['roster'] += 1
    present = []
    for group, keys in [('latest_h2h', ('home', 'draw', 'away')),
                        ('opening_total', ('over', 'under')), ('current_total', ('over', 'under'))]:
        valid = [row[group][key] is not None for key in keys]
        counter[group + ('_full' if all(valid) else '_partial' if any(valid) else '_missing')] += 1
        present.append(all(valid))
    counter['complete'] += int(all(present))


def build(assets, mode):
    if sha256(assets.read_bytes()).hexdigest() != ARCHIVE_SHA256:
        raise ValueError('archive differs from locked Data commit')
    seasons = TRAIN if mode == 'train-labels' else TEST if mode == 'test-labels' else TRAIN + TEST
    files, by_id = {}, {}
    with tarfile.open(assets) as archive:
        manifest = json.load(archive.extractfile('ASSET-MANIFEST.json'))
        for season in seasons:
            for league in sorted(LEAGUES):
                leaf = f'{season}_{league}.csv'
                name = 'raw/close/' + leaf
                source = manifest['closing_book_raw_csv'][leaf]
                raw = archive.extractfile(name).read()
                fingerprint = sha256(raw).hexdigest()
                if (source['path'] != name or source['url'] != f'https://www.football-data.co.uk/mmz4281/{season}/{league}.csv'
                        or source.get('season', season) != season or source.get('league', league) != league
                        or fingerprint != source['sha256'] or fingerprint != manifest['files'][name]):
                    raise ValueError(f'unexpected file identity {name}')
                files[name] = {'sha256': fingerprint, 'archive_fetch_at': source['fetch_time']}
                for row in rows_for(raw, name, season, league, mode):
                    if row['id'] in by_id:
                        raise ValueError(f'duplicate fixture identity at {name}:{row["source_line"]}')
                    by_id[row['id']] = row
    ordered = sorted(by_id.values(), key=lambda row: (row['date'], row['competition'], row['id']))
    coverage = defaultdict(Counter)
    for row in ordered:
        for key in (row['split'], f"{row['split']}/{row['competition']}", f"{row['split']}/{row['season']}"):
            if mode == 'inputs':
                completeness(row, coverage[key])
            else:
                coverage[key]['roster'] += 1
                coverage[key]['score_present'] += int(row['score'] is not None)
    summary = dict(mode=mode, archive_sha256=ARCHIVE_SHA256, archive_blob=ARCHIVE_BLOB,
                   data_commit=DATA_COMMIT, source_files=files, clock=CLOCK, scope=LEAGUES,
                   archived_out_of_scope=['E1', 'P1'], train_seasons=TRAIN,
                   test_seasons=TEST, test_months=[8, 9, 10, 11], coverage=coverage)
    return ordered, summary


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('mode', choices=['inputs', 'train-labels', 'test-labels'])
    cli.add_argument('--assets', required=True, type=Path)
    cli.add_argument('--out', required=True, type=Path)
    cli.add_argument('--summary', type=Path)
    args = cli.parse_args()
    if args.summary and args.out.resolve() == args.summary.resolve():
        cli.error('choose separate destinations for rows and coverage')
    rows, summary = build(args.assets, args.mode)
    text = ''.join(json_text(row) + '\n' for row in rows)
    summary.update(output_sha256=sha256(text.encode()).hexdigest(), rows=len(rows))
    if args.out.exists() or (args.summary and args.summary.exists()):
        cli.error('choose fresh output files')
    with args.out.open('x', encoding='utf-8', newline='\n') as out:
        out.write(text)
    if args.summary:
        with args.summary.open('x', encoding='utf-8', newline='\n') as out:
            out.write(json.dumps(summary, indent=2, sort_keys=True) + '\n')
    print(json_text({k: summary[k] for k in ('mode', 'rows', 'output_sha256')}))


if __name__ == '__main__':
    main()
