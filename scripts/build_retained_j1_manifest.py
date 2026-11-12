"""Build an explicit J1 recovery crosswalk from retained source events, new file only."""
import argparse
from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
from research_program.retained_j1 import load_manifest


def checksum(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def build(current_path, test_path, source_root, evidence_dir, source_commit, out):
    current_path, test_path, source_root, evidence_dir, out = map(
        lambda p: Path(p).resolve(), (current_path, test_path, source_root, evidence_dir, out))
    if out.exists():
        raise FileExistsError(out)
    current = json.loads(current_path.read_text(encoding='utf-8'))
    test = json.loads(test_path.read_text(encoding='utf-8'))
    proofs = defaultdict(list)
    for row in current['history']:
        raw_path = evidence_dir / (row['match_id'] + '.json')
        if not raw_path.is_file():
            continue
        raw = json.loads(raw_path.read_text(encoding='utf-8'))
        event = raw['data']['header']
        if str(event['id']) != row['match_id'] or len(event['competitions']) != 1:
            raise ValueError('retained event identity conflict')
        game = event['competitions'][0]
        for side in ('home', 'away'):
            candidates = [t for t in game['competitors'] if t['homeAway'] == side]
            if len(candidates) != 1:
                raise ValueError('ambiguous retained team side')
            team = candidates[0]
            tid = str(team['team']['id'])
            if tid != row[side] or str(team['score']) != str(row[side + '_goals']):
                raise ValueError('retained team or score conflict')
            canonical = 'espn:' + tid
            if canonical not in test['core']['teams']:
                raise ValueError('retained identity absent from Test artifact')
            proofs[canonical].append({'source_file': str(raw_path),
                'source_sha256': checksum(raw_path), 'event_id': row['match_id'],
                'side': side, 'team_id': tid, 'team_name': team['team']['displayName']})
    mapping = {}
    for tid in current['core']['baseline']['teams']:
        canonical = 'espn:' + tid
        unique = {p['event_id']: p for p in proofs[canonical]}
        if len(unique) < 2:
            raise ValueError('two distinct retained events required for each Current team')
        mapping[canonical] = {'current_id': tid, 'test_id': canonical,
                              'proof': list(unique.values())[:2]}
    manifest = {'schema': 'WCPP-RETAINED-J1-1', 'source_commit': source_commit,
        'source_commit_is_receipt_metadata': True, 'source_root': str(source_root),
        'source_files': {str(p.relative_to(source_root)): checksum(p)
                         for p in sorted(source_root.rglob('*.py'))},
        'current_artifact': str(current_path), 'current_sha256': checksum(current_path),
        'test_artifact': str(test_path), 'test_sha256': checksum(test_path),
        'team_mapping': mapping, 'active_modified': False}
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('x', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write('\n')
    # Source SHA and original loader/dataset hashes, not the asserted commit label,
    # establish numerical dependency identity. Validation failure stays explicit.
    load_manifest(out, 'test')
    return {'manifest': str(out), 'sha256': checksum(out), 'mapped_teams': len(mapping)}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('current-artifact', 'test-artifact', 'source-root', 'evidence-dir', 'source-commit', 'out'):
        p.add_argument('--' + name, required=True)
    a = p.parse_args()
    print(json.dumps(build(a.current_artifact, a.test_artifact, a.source_root,
                           a.evidence_dir, a.source_commit, a.out), ensure_ascii=False))
