"""Rolling-origin backtest entrypoint for the Egidi blend research candidate.

Example:
    python runtime/research/egidi_blend/backtest.py \\
        --download-football-data /tmp/wcpp-egidi-fd \\
        --out /tmp/egidi-blend-report.json

This does not call predict_active and does not read or write the Champion registry.
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

_DIR = Path(__file__).resolve().parent
if str(_DIR) not in sys.path:
    sys.path.insert(0, str(_DIR))

import numpy as np

from constants import VERSION, Config
from load import (
    download_public_football_data,
    field_coverage,
    load_football_data_dir,
    load_jsonl,
    load_sqlite,
    merge_matches,
)
from walkforward import walk_forward


DEFAULT_SQLITE = Path('/workspace/WCPP/data/unified/WCPP_统一赛事库.sqlite')


def _jsonable(value):
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.bool_):
        return bool(value)
    return value


def _headline(block):
    if not block or not block.get('n'):
        return {'n': 0}
    out = {'n': block['n']}
    for name in ('candidate', 'dixon_coles', 'market_shin', 'always_1_1'):
        part = block.get(name) or {}
        out[name] = {'n': part.get('n'), 'top1': part.get('top1'), 'top3': part.get('top3'), 'rps': part.get('rps')}
    out['mcnemar_top1'] = block.get('mcnemar_top1')
    return out


def parse_args(argv):
    parser = argparse.ArgumentParser(description='Egidi blend research backtest (not a live model).')
    parser.add_argument('--jsonl', action='append', default=[], help='JSONL match file. Repeatable. Wins over later sources on the same fixture.')
    parser.add_argument('--sqlite', default=None, help='Read-only canonical sqlite. Defaults to the WCPP path when that file exists.')
    parser.add_argument('--football-data-dir', default=None, help='Directory of football-data.co.uk CSVs.')
    parser.add_argument('--download-football-data', default=None,
                        help='Download public E0/SP1/I1/D1/F1 CSVs into this directory and load them. Does not fetch Asian leagues.')
    parser.add_argument('--download-start', type=int, default=1993)
    parser.add_argument('--download-end', type=int, default=None)
    parser.add_argument('--out', required=True, help='Summary JSON path.')
    parser.add_argument('--save-rows', default=None, help='Optional JSONL of per-match hit rows.')
    parser.add_argument('--min-prior-seasons', type=int, default=3)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    download_report = None
    fd_dir = args.football_data_dir
    if args.download_football_data:
        download_report = download_public_football_data(
            args.download_football_data, start=args.download_start, end=args.download_end or date.today().year)
        fd_dir = args.download_football_data
        print(json.dumps({'downloaded': len(download_report['downloaded']),
                          'skipped_existing': len(download_report['skipped_existing']),
                          'failed': len(download_report['failed'])}), flush=True)
    groups = []
    sources = []
    for path in args.jsonl:
        rows = load_jsonl(path)
        groups.append(rows)
        sources.append({'kind': 'jsonl', 'path': path, 'rows': len(rows)})
    sqlite_path = args.sqlite
    if sqlite_path is None and DEFAULT_SQLITE.exists():
        sqlite_path = str(DEFAULT_SQLITE)
    if sqlite_path:
        if not Path(sqlite_path).exists():
            raise SystemExit('sqlite not found: ' + sqlite_path)
        rows = load_sqlite(sqlite_path)
        groups.append(rows)
        sources.append({'kind': 'sqlite', 'path': sqlite_path, 'rows': len(rows)})
    fd_files = []
    if fd_dir:
        rows, fd_files = load_football_data_dir(fd_dir)
        groups.append(rows)
        sources.append({'kind': 'football-data.co.uk', 'path': fd_dir, 'rows': len(rows), 'files': len(fd_files)})
    matches = merge_matches(groups)
    coverage = field_coverage(matches)
    config = Config()
    config.min_prior_seasons = args.min_prior_seasons
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if not matches:
        report = {
            'version': VERSION, 'role': 'RESEARCH_CANDIDATE', 'champion_unchanged': True,
            'wired_into_predict_active': False, 'formal_promotion': False,
            'blocker': 'NO_MATCH_ROWS',
            'inputs': sources, 'download': download_report, 'coverage': coverage,
            'expected_inputs': {
                'jsonl': 'one match per line with league, date, home, away, home_goals, away_goals; optional odds_1x2, odds_ou, odds_cs, home_xg, away_xg, lineup_known',
                'sqlite': str(DEFAULT_SQLITE),
                'football_data_dir': 'CSVs named like 2024_E0.csv from football-data.co.uk divisions E0 SP1 I1 D1 F1',
                'download_command': 'python runtime/research/egidi_blend/backtest.py --download-football-data /tmp/wcpp-egidi-fd --out /tmp/egidi-blend-report.json',
                'asian_ood': 'aus.1, jpn.1 and kor.1 are evaluated only when present in jsonl or sqlite. This entrypoint does not call ESPN and does not invent a Korea slug.',
            },
        }
        out.write_text(json.dumps(_jsonable(report), ensure_ascii=False, indent=2))
        print('NO_MATCH_ROWS written to ' + str(out), flush=True)
        return 2
    report, scored = walk_forward(matches, config)
    report['inputs'] = sources
    report['download'] = None if download_report is None else {
        'downloaded': len(download_report['downloaded']),
        'skipped_existing': len(download_report['skipped_existing']),
        'failed': download_report['failed'][:30],
        'failed_n': len(download_report['failed']),
        'espn_requested': False,
    }
    report['coverage'] = coverage
    report['headline'] = {
        'europe_pooled': _headline(report['metrics']['europe_pooled']),
        'ood_asia_pooled': _headline(report['metrics']['ood_asia_pooled']),
    }
    if coverage['ood_asia_leagues_missing']:
        report['ood_note'] = (
            'No rows were loaded for ' + ', '.join(coverage['ood_asia_leagues_missing'])
            + '. They are out-of-domain tracks and are not filled from Europe or from an invented ESPN Korea feed. '
            + 'Pass JSONL or the canonical sqlite that actually contains those leagues.'
        )
    out.write_text(json.dumps(_jsonable(report), ensure_ascii=False, indent=2))
    if args.save_rows:
        row_path = Path(args.save_rows)
        row_path.parent.mkdir(parents=True, exist_ok=True)
        with row_path.open('w', encoding='utf-8') as handle:
            for row in scored:
                handle.write(json.dumps(_jsonable(row), ensure_ascii=False) + '\n')
    print(json.dumps(_jsonable(report['headline']), ensure_ascii=False, indent=2), flush=True)
    print('wrote ' + str(out), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
