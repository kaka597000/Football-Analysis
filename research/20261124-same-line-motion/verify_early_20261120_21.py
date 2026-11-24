#!/usr/bin/env python3
"""Rebuild and verify the 33 original 2026-11-20/21 prematch freezes.

Usage:
  python verify_early_20261120_21.py --data /path/to/Football-Analysis-Data \
      --out early-20261120-21-33.jsonl

Uses only the pinned public Data Git object and Python's standard library.
It does not use or emit forecast payloads, labels, or match results.
"""
import argparse
import hashlib
import io
import json
import subprocess
import tarfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse

PIN = 'a6d45024cf6cf622f05e4b378ab53cfe2d6ae329'
ASSET_PATH = 'workspace-updates/20261123-kpi-optimization/source-assets.tar.gz'
ASSET_SHA256 = '7c106ee66a816da84dbbfbf254633b55b7c25e610e7d0f31bf168d68f61d9659'
BJT = timezone(timedelta(hours=8))
BATCH = '2026-11-20/21 fixed33'


def digest_record(record):
    body = {k: v for k, v in record.items() if k != 'record_sha256'}
    data = json.dumps(body, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')
    return hashlib.sha256(data).hexdigest()


def parse_time(value):
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('timestamp missing timezone')
    return dt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True,
                        help='local checkout of the public Football-Analysis-Data Git repository')
    parser.add_argument('--out', type=Path, required=True,
                        help='output metadata-only JSONL path')
    args = parser.parse_args()
    archive = subprocess.check_output(
        ['git', '-C', str(args.data), 'show', f'{PIN}:{ASSET_PATH}'])
    if hashlib.sha256(archive).hexdigest() != ASSET_SHA256:
        raise SystemExit('pinned source-assets archive SHA-256 mismatch')

    rows = []
    source_ref_count = 0
    clocked_refs = 0
    pre_cutoff_refs = 0
    raw_source_in_manifest = 0
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as bundle:
        manifest = json.load(bundle.extractfile('ASSET-MANIFEST.json'))
        available_hashes = set(manifest.get('files', {}).values())
        for match_id, member in manifest['wcpp_frozen_records'].items():
            blob = bundle.extractfile(member).read()
            file_sha = hashlib.sha256(blob).hexdigest()
            if manifest['files'].get(member) != file_sha:
                raise SystemExit(f'freeze object file hash mismatch: {member}')
            record = json.loads(blob)
            if record.get('record_sha256') != digest_record(record):
                raise SystemExit(f'freeze record hash mismatch: {member}')
            if (record.get('track') != 'comprehensive'
                    or record.get('stage') != 'prematch'
                    or record.get('evaluation_slot') != 'FIRST_PREMATCH'
                    or record.get('revision') != 1):
                continue
            fixture = record['fixture']
            kickoff = parse_time(fixture['kickoff_at'])
            local_day = kickoff.astimezone(BJT).date().isoformat()
            if not ('2026-11-20' <= local_day < '2026-11-22'):
                continue
            cutoff = parse_time(fixture['cutoff_at'])
            bound_espn = []
            for source in fixture.get('sources', []):
                host = (urlparse(source.get('source_url', '')).hostname or '').lower()
                if 'espn' not in host:
                    continue
                source_ref_count += 1
                query = source.get('query_time')
                response_sha = source.get('response_sha256')
                before = bool(query and parse_time(query) < cutoff)
                if query and response_sha:
                    clocked_refs += 1
                if before:
                    pre_cutoff_refs += 1
                if response_sha in available_hashes:
                    raw_source_in_manifest += 1
                bound_espn.append({
                    'source_file': Path(source.get('file', '')).name,
                    'response_sha256': response_sha,
                    'query_time': query,
                    'query_before_cutoff': before,
                })
            if bound_espn:
                absent = any(x['response_sha256'] not in available_hashes
                             for x in bound_espn)
                reason = ('original first freeze binds ESPN response hash and pre-cutoff query; '
                          'raw response absent from this pinned bundle, so DK fields are not '
                          'independently inspectable' if absent else '')
            else:
                reason = 'no ESPN summary citation in original first freeze'
            rows.append({
                'id': match_id,
                'date': local_day,
                'competition': fixture['cohort'],
                'provider': 'DraftKings',
                'home_id': fixture['home'],
                'away_id': fixture['away'],
                'source_ids': {
                    'freeze_member': member,
                    'freeze_file_sha256': file_sha,
                    'freeze_id': record['freeze_id'],
                    'espn': bound_espn,
                },
                'cutoff_at': fixture['cutoff_at'],
                'kickoff_at': fixture['kickoff_at'],
                'freeze_sha256': record['record_sha256'],
                'batch': BATCH,
                'excluded': reason,
            })
    rows.sort(key=lambda row: (row['date'], row['competition'], row['id']))
    if len(rows) != 33 or len({row['id'] for row in rows}) != 33:
        raise SystemExit(f'expected 33 unique original first freezes; found {len(rows)}')
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('w', encoding='utf-8', newline='\n') as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n')
    print(f'rows={len(rows)} ESPN_refs={source_ref_count} clocked_refs={clocked_refs} '
          f'pre_cutoff_refs={pre_cutoff_refs} raw_responses_in_manifest={raw_source_in_manifest}')
    print(f'output={args.out}')


if __name__ == '__main__':
    main()
