"""Portable, locked-label extractor for fixed forecast rosters.

No model fit or scoring occurs here. Only exact roster IDs are considered, and
result records are decoded only after their trusted capture clock is shown to
fall before the roster's exclusive label cutoff. The canonical Data trainer
verifier checks the raw ESPN summary hash and regulation-period identity.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys

ID_RE = re.compile(rb'"match_id"\s*:\s*"([^\"]+)"')
CAP_RE = re.compile(rb'"captured_at"\s*:\s*"([^\"]+)"')
KNOWN_RE = re.compile(rb'"known_at"\s*:\s*"([^\"]+)"')
START_RE = re.compile(rb'"started_at"\s*:\s*"([^\"]+)"')
ARRAY_KEY = b'"new_rows"'


def digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def aware(value: str) -> datetime:
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('timezone required')
    return stamp.astimezone(timezone.utc)


def git(repo: Path, *args: str) -> bytes:
    return subprocess.check_output(['git', '-C', str(repo), *args])


def pinned_blob(repo: Path, commit: str, path: str) -> bytes:
    return git(repo, 'show', f'{commit}:{path}')


def verify_data_snapshot(args) -> tuple[bytes, str, str, str, object]:
    repo = args.results_repo.resolve()
    commit = args.data_commit
    git(repo, 'cat-file', '-e', f'{commit}^{{commit}}')
    relative = args.results_path.as_posix()
    pinned = pinned_blob(repo, commit, relative)
    snapshot_file = (args.data / args.results_path).resolve()
    if not snapshot_file.is_file():
        raise ValueError('data snapshot results file is missing')
    actual = snapshot_file.read_bytes()
    if actual != pinned:
        raise ValueError('working data snapshot differs byte-for-byte from pinned Git blob')
    # Pin verifier source independently from the Data snapshot.
    source_repo = args.source_repo.resolve()
    source_commit = args.source_commit
    git(source_repo, 'cat-file', '-e', f'{source_commit}^{{commit}}')
    verifier_rel = 'runtime/automation/trainer.py'
    verifier_pinned = pinned_blob(source_repo, source_commit, verifier_rel)
    verifier_path = (source_repo / verifier_rel).resolve()
    if verifier_path.read_bytes() != verifier_pinned:
        raise ValueError('result verifier worktree differs from pinned source commit')
    runtime = str((source_repo / 'runtime').resolve())
    automation = str((source_repo / 'runtime/automation').resolve())
    project_root = str(source_repo.resolve())
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    if runtime not in sys.path:
        sys.path.insert(0, runtime)
    if automation not in sys.path:
        sys.path.insert(0, automation)
    spec = importlib.util.spec_from_file_location('_pinned_label_trainer', verifier_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return actual, digest(actual), digest(verifier_pinned), source_commit, module._verify_raw_result


def read_roster(path: Path, mode: str, lock_path: Path | None, forecast_path: Path | None, protocol_path: Path | None):
    raw = path.read_bytes()
    roster_sha = digest(raw)
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    if not rows:
        raise ValueError('empty input roster')
    all_by_id = {}
    for row in rows:
        if {'score', 'home_goals', 'away_goals', 'result'} & row.keys():
            raise ValueError('outcome field in input roster')
        mid = row.get('id', row.get('match_id'))
        if not isinstance(mid, str) or not mid or mid in all_by_id:
            raise ValueError('roster IDs must be unique non-empty strings')
        all_by_id[mid] = row
    lock = None
    forecast_sha = None
    selected = rows
    if mode == 'train':
        tagged = [r for r in rows if r.get('split') is not None or r.get('role') is not None]
        if tagged:
            selected = [r for r in tagged if r.get('split', r.get('role')) == 'train']
    if mode == 'test':
        if lock_path is None:
            raise ValueError('--test requires --lock')
        lock = json.loads(lock_path.read_bytes())
        forecast_path = forecast_path or (lock_path.parent / 'forecasts.jsonl')
        protocol_path = protocol_path or (lock_path.parent / 'protocol.json')
        if lock.get('panel') != 'native':
            raise ValueError('test lock is not panel=native')
        if lock.get('input_sha256') != roster_sha:
            raise ValueError('study.seal input_sha256 mismatch')
        forecast_raw = forecast_path.read_bytes()
        forecast_sha = digest(forecast_raw)
        if lock.get('forecasts_sha256') != forecast_sha:
            raise ValueError('study.seal forecasts_sha256 mismatch')
        protocol_raw = protocol_path.read_bytes()
        if lock.get('protocol_sha256') != digest(protocol_raw):
            raise ValueError('study.seal protocol_sha256 mismatch')
        forecast_rows = [json.loads(line) for line in forecast_raw.splitlines() if line.strip()]
        if len(forecast_rows) != lock.get('listed'):
            raise ValueError('study.seal listed count differs from forecasts.jsonl')
        if any({'score', 'home_goals', 'away_goals', 'result'} & r.keys() for r in forecast_rows):
            raise ValueError('outcome field in sealed forecasts')
        listed_ids = [r.get('id') for r in forecast_rows]
        if any(not isinstance(mid, str) for mid in listed_ids) or len(set(listed_ids)) != len(listed_ids):
            raise ValueError('duplicate or invalid ID in forecasts.jsonl')
        if set(listed_ids) - set(all_by_id):
            raise ValueError('forecasts.jsonl contains IDs absent from original input roster')
        selected_ids = {r['id'] for r in forecast_rows if isinstance(r.get('forecasts'), dict) and r['forecasts']}
        selected = [all_by_id[mid] for mid in sorted(selected_ids)]
        if any(all_by_id[mid].get('split') not in (None, 'test') for mid in selected_ids):
            raise ValueError('actual forecast ID is not in the test roster')
        forecast_by_id = {r['id']: r for r in forecast_rows}
        for mid in selected_ids:
            forecast = forecast_by_id[mid]
            original = all_by_id[mid]
            if forecast.get('competition') and forecast['competition'] != original.get('competition'):
                raise ValueError('forecast competition conflicts with original input identity')
            if forecast.get('date') and forecast['date'] != original.get('date'):
                raise ValueError('forecast date conflicts with original kickoff identity')
    for row in selected:
        mid = row.get('id', row.get('match_id'))
        for key in ('competition', 'kickoff_at'):
            if not row.get(key):
                raise ValueError(f'selected roster item missing {key}: {mid}')
        if not row.get('home_id') or not row.get('away_id'):
            # Keep unidentifiable entries in the roster; they cannot produce labels.
            row['_identity_incomplete'] = True
    return selected, roster_sha, lock, forecast_sha


def iter_json_array_objects(raw: bytes, key: bytes):
    """Yield byte slices of objects in one JSON array without decoding siblings."""
    pos = raw.find(key)
    if pos < 0:
        return
    pos = raw.find(b'[', pos + len(key))
    if pos < 0:
        return
    i = pos + 1
    n = len(raw)
    while i < n:
        while i < n and raw[i] in b' \t\r\n,':
            i += 1
        if i >= n or raw[i] == ord(']'):
            return
        if raw[i] != ord('{'):
            raise ValueError('unexpected JSON array item in run receipt')
        start = i
        depth = 0
        quoted = False
        escape = False
        while i < n:
            byte = raw[i]
            if quoted:
                if escape:
                    escape = False
                elif byte == ord('\\'):
                    escape = True
                elif byte == ord('"'):
                    quoted = False
            else:
                if byte == ord('"'):
                    quoted = True
                elif byte == ord('{'):
                    depth += 1
                elif byte == ord('}'):
                    depth -= 1
                    if depth == 0:
                        yield raw[start:i + 1]
                        i += 1
                        break
            i += 1
        else:
            raise ValueError('truncated JSON object in run receipt')


def receipt_index(run_dir: Path | None, ids: set[str], cutoffs: dict[str, datetime]):
    if run_dir is None:
        return {}, Counter()
    index = defaultdict(list)
    counts = Counter()
    for path in sorted(run_dir.glob('*-collect.json')):
        raw = path.read_bytes()
        started = START_RE.search(raw)
        if not started:
            continue
        started_at = started.group(1).decode('ascii')
        # Do not inspect result rows from runs beginning at/after the earliest applicable cutoff.
        relevant = [cutoffs[mid] for mid in ids]
        if relevant and aware(started_at) >= min(relevant):
            continue
        for item_bytes in iter_json_array_objects(raw, ARRAY_KEY):
            match = ID_RE.search(item_bytes)
            if not match:
                continue
            mid = match.group(1).decode('utf-8', 'strict')
            if mid not in ids:
                continue
            capture_match = CAP_RE.search(item_bytes)
            capture = capture_match.group(1).decode('ascii') if capture_match else None
            if not capture or aware(capture) >= cutoffs[mid]:
                counts['late_or_missing_receipt_not_decoded'] += 1
                continue
            item = json.loads(item_bytes)
            index[mid].append({'row': item, 'file': path.name,
                               'file_sha256': digest(raw), 'run_started_at': started_at})
    return index, counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True, help='fixed result-state directory, including raw/ files')
    parser.add_argument('--results-repo', type=Path, required=True, help='Git repository containing pinned Data snapshot')
    parser.add_argument('--source-repo', type=Path, required=True, help='Git repository containing the pinned result verifier')
    parser.add_argument('--source-commit', required=True, help='immutable source-code commit SHA')
    parser.add_argument('--data-commit', required=True, help='immutable commit SHA for normalized results and verifier')
    parser.add_argument('--results-path', type=Path, default=Path('normalized-results.jsonl'))
    parser.add_argument('--inputs', type=Path, required=True, help='standard assembled JSONL fixture roster')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--train', action='store_true')
    mode.add_argument('--test', action='store_true')
    parser.add_argument('--lock', type=Path, help='required in test mode')
    parser.add_argument('--forecast', type=Path, help='exact forecasts.jsonl file bound by test lock (defaults beside lock)')
    parser.add_argument('--protocol', type=Path, help='exact protocol.json bound by study.seal lock (defaults beside lock)')
    parser.add_argument('--runs', type=Path, help='optional collection-run receipt directory')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    mode_name = 'test' if args.test else 'train'
    rows, roster_sha, lock, forecast_sha = read_roster(args.inputs, mode_name, args.lock, args.forecast, args.protocol)
    if args.train:
        cutoffs = {r.get('id', r.get('match_id')): aware(r['label_cutoff_at']) for r in rows}
    else:
        result_cutoff = lock.get('result_cutoff_at') or lock.get('stop_exclusive_at')
        protocol_path = args.protocol or (args.lock.parent / 'protocol.json')
        if not result_cutoff and protocol_path.is_file():
            protocol = json.loads(protocol_path.read_bytes())
            result_cutoff = protocol.get('native_stop_exclusive')
        if not result_cutoff:
            raise ValueError('locked test needs a result cutoff in lock or protocol')
        cutoffs = {r.get('id', r.get('match_id')): aware(result_cutoff) for r in rows}
    ids = set(cutoffs)
    run_receipts, receipt_counts = receipt_index(args.runs, ids, cutoffs)
    results_bytes, results_sha, verifier_sha, verifier_commit, verify_raw = verify_data_snapshot(args)
    out_rows = {}
    counts = Counter(receipt_counts)
    roster_by_id = {r.get('id', r.get('match_id')): r for r in rows}
    for line_number, line in enumerate(results_bytes.splitlines(), 1):
        match = ID_RE.search(line)
        if not match:
            continue
        mid = match.group(1).decode('utf-8', 'strict')
        if mid not in ids:
            continue
        cap_match = CAP_RE.search(line)
        cap = cap_match.group(1).decode('ascii') if cap_match else None
        if not cap or aware(cap) >= cutoffs[mid]:
            counts['late_or_missing_result_not_decoded'] += 1
            continue
        row = json.loads(line)
        roster = roster_by_id[mid]
        if not roster.get('home_id') or not roster.get('away_id') or roster.get('_identity_incomplete'):
            counts['roster_identity_incomplete'] += 1
            continue
        checks = {
            'competition': row.get('competition') == roster['competition'],
            'stage': (not roster.get('stage') or row.get('stage') == roster.get('stage')),
            'home': row.get('home') == roster['home_id'],
            'away': row.get('away') == roster['away_id'],
            'kickoff_at': aware(row.get('kickoff_at', '')) == aware(roster['kickoff_at']),
            'score_basis': row.get('score_basis') == 'regulation_90',
            'raw_capture': row.get('raw_reference', {}).get('captured_at') == cap,
            'known_at': row.get('known_at') == cap,
            'source_event': mid == 'espn:' + str(row.get('raw_reference', {}).get('source_url', '')).split('event=')[-1],
        }
        if not all(checks.values()):
            counts['identity_or_clock_reject'] += 1
            for field, ok in checks.items():
                if not ok:
                    counts['reject_' + field] += 1
            continue
        if aware(row['known_at']) < aware(roster['kickoff_at']):
            counts['capture_before_kickoff_reject'] += 1
            continue
        if not isinstance(row.get('home_goals'), int) or isinstance(row.get('home_goals'), bool) or not isinstance(row.get('away_goals'), int) or isinstance(row.get('away_goals'), bool):
            counts['score_type_reject'] += 1
            continue
        try:
            status = verify_raw(row, args.data.resolve(), {})
        except ValueError as exc:
            counts['raw_evidence_reject:' + str(exc)] += 1
            continue
        if status != 'VERIFIED_RAW_REGULATION_90':
            counts['raw_evidence_reject:' + status] += 1
            continue
        if args.runs:
            matches = [x for x in run_receipts.get(mid, [])
                       if x['row'].get('known_at') == row.get('known_at')
                       and x['row'].get('raw_reference') == row.get('raw_reference')
                       and x['row'].get('source_url') == row.get('source_url')
                       and x['row'].get('home_goals') == row.get('home_goals')
                       and x['row'].get('away_goals') == row.get('away_goals')]
            if len(matches) != 1:
                counts['capture_run_receipt_missing_or_ambiguous'] += 1
                continue
            receipt = matches[0]
        else:
            receipt = None
        ref = row['raw_reference']
        refs = {'raw_path': ref['path'], 'source_url': row['source_url']}
        if roster.get('freeze_sha256'):
            refs['freeze_sha256'] = roster['freeze_sha256']
        output = {
            'id': mid, 'source_event_ids': {'espn': mid.removeprefix('espn:')},
            'home_id': roster['home_id'], 'away_id': roster['away_id'],
            'kickoff_at': roster['kickoff_at'], 'score': [row['home_goals'], row['away_goals']],
            'observed_at': row['known_at'], 'evidence_sha256': ref['sha256'],
            'references': refs,
            'verification': status,
        }
        if receipt:
            output['capture_receipt'] = {'file': receipt['file'], 'file_sha256': receipt['file_sha256'],
                                         'run_started_at': receipt['run_started_at']}
        if mid in out_rows and out_rows[mid] != output:
            raise ValueError(f'conflicting authenticated labels: {mid}')
        out_rows[mid] = output
        counts['verified_labels'] += 1
    text = ''.join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n'
                   for row in sorted(out_rows.values(), key=lambda x: (x['kickoff_at'], x['id'])))
    if args.out.exists() or args.report.exists():
        raise ValueError('refusing to overwrite output')
    args.out.write_text(text, encoding='utf-8', newline='\n')
    report = {
        'schema': 'AUTHENTICATED-TRAINING-LABEL-EXPORT-1' if args.train else 'LOCKED-TEST-LABEL-EXPORT-1',
        'mode': mode_name, 'data_commit': args.data_commit, 'source_commit': verifier_commit,
        'normalized_results_sha256': results_sha, 'verifier_sha256': verifier_sha,
        'inputs_sha256': roster_sha, 'forecast_sha256': forecast_sha,
        'lock_sha256': digest(args.lock.read_bytes()) if args.lock else None,
        'capture_cutoff_values_utc': sorted({v.isoformat() for v in cutoffs.values()}),
        'roster_rows': len(rows), 'authenticated_labels': len(out_rows),
        'counts': dict(counts), 'output_sha256': digest(text.encode()),
        'raw_evidence_required': True, 'test_mode_requires_lock': True,
    }
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8', newline='\n')
    print(json.dumps({'mode': mode_name, 'roster_rows': len(rows), 'labels': len(out_rows),
                      'output_sha256': report['output_sha256'], 'counts': dict(counts)}, sort_keys=True))


if __name__ == '__main__':
    main()
