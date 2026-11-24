"""Assemble a metadata-only roster from the three registered first-freeze archives."""
import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import tarfile
import tempfile

PACKAGES = {
    '20261105-europa-conference-analysis': ('2470d20a20ca1b346792b6d9b1725e32255e047ca899a8baf81143f81796e299', {'uefa.europa', 'uefa.europa.conf'}),
    '20261106-aleague-analysis': ('7ccf7f258131eedcc1ea187ffee93c666df4a21db1c2e6e77eccda918052b9ba', {'aus.1'}),
    '20261107-night19-55matches': ('07c79bc107db14c8113cadab6ed6194bbe946f82cdbea9e2ea61c0a30bd237ac', None),
}
CUTOFF = '2026-11-08T04:00:00+00:00'


def record_sha(record):
    body = {k: v for k, v in record.items() if k != 'record_sha256'}
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    return sha256(raw).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--archive-root', type=Path, required=True, help='directory containing the three package directories')
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    if a.out.exists():
        raise SystemExit('refusing to overwrite roster')
    rows = {}
    counts = Counter()
    for package, (expected_sha, competitions) in PACKAGES.items():
        root = (a.archive_root / package).resolve()
        manifest = json.loads((root / 'manifest.json').read_bytes())
        if manifest.get('archive_sha256') != expected_sha:
            raise ValueError(f'unexpected archive pin: {package}')
        with tempfile.NamedTemporaryFile() as f:
            h = sha256(); size = 0
            for part in manifest['ordered_parts']:
                file = (root / part['path']).resolve()
                if not file.is_relative_to(root):
                    raise ValueError('archive part outside package')
                data = file.read_bytes()
                if len(data) != part['bytes'] or sha256(data).hexdigest() != part['sha256']:
                    raise ValueError(f'part checksum mismatch: {package}')
                f.write(data); h.update(data); size += len(data)
            f.flush()
            if h.hexdigest() != expected_sha or size != manifest['archive_bytes']:
                raise ValueError(f'archive checksum mismatch: {package}')
            with tarfile.open(f.name, 'r:gz') as tar:
                for member in tar:
                    if not member.isfile() or '/issued-analyses/' not in member.name or not member.name.endswith('.json'):
                        continue
                    record = json.load(tar.extractfile(member))
                    if (record.get('track') != 'comprehensive' or record.get('stage') != 'prematch'
                            or record.get('evaluation_slot') != 'FIRST_PREMATCH' or record.get('revision') != 1):
                        continue
                    if record_sha(record) != record.get('record_sha256'):
                        raise ValueError(f'freeze digest mismatch: {member.name}')
                    fixture = record['fixture']
                    if competitions is not None and fixture.get('competition') not in competitions:
                        continue
                    mid = record['match_id']
                    if mid in rows or fixture.get('match_id') != mid:
                        raise ValueError(f'duplicate or inconsistent fixture ID: {mid}')
                    row = {
                        'id': mid,
                        'competition': fixture.get('competition'),
                        'cohort': fixture.get('cohort'),
                        'stage': fixture.get('stage'),
                        'home_id': fixture.get('home'),
                        'away_id': fixture.get('away'),
                        'kickoff_at': fixture.get('kickoff_at'),
                        'cutoff_at': fixture.get('cutoff_at'),
                        'score_basis': fixture.get('score_basis', 'regulation_90'),
                        'freeze_id': record['freeze_id'],
                        'freeze_sha256': record['record_sha256'],
                        'track': record['track'],
                        'evaluation_slot': record['evaluation_slot'],
                        'revision': record['revision'],
                        'identity_verified': fixture.get('identity_verified'),
                        'package': package,
                        'package_archive_sha256': expected_sha,
                        'label_cutoff_at': CUTOFF,
                    }
                    rows[mid] = row
                    counts[package] += 1
    content = ''.join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n'
                      for row in sorted(rows.values(), key=lambda x: (x['kickoff_at'] or '', x['id'])))
    a.out.write_text(content, encoding='utf-8', newline='\n')
    print(json.dumps({'rows': len(rows), 'per_package': dict(counts), 'sha256': sha256(content.encode()).hexdigest()}))


if __name__ == '__main__':
    main()
