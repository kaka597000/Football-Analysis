"""Export runtime asset identities, not credentials, data or model parameters."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess

REPO = Path(__file__).resolve().parents[1]


def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def describe(path):
    path = Path(path)
    row = {'path': str(path), 'exists': path.is_file()}
    if row['exists']:
        before = path.stat()
        row.update(bytes=before.st_size, sha256=sha256(path))
        after = path.stat()
        row['changed_during_hash'] = (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
        if row['changed_during_hash']:
            raise ValueError('asset changed while hashing; create a stable snapshot')
    return row


def checkpoint(root):
    root = Path(root)
    registry_path = root/'runtime/active-model.json'
    registry = json.loads(registry_path.read_text())
    routes = {}
    for cohort, route in sorted(registry['models'].items()):
        item = {}
        for prefix, name in (('', 'current'), ('test_', 'test')):
            value = route.get(prefix+'artifact_path')
            if not value:
                item[name] = {'status': 'NO_SEPARATE_ARTIFACT_REGISTERED'}
                continue
            artifact_path = Path(value)
            asset = describe(artifact_path)
            asset['registered_sha256'] = route.get(prefix+'artifact_sha256')
            asset['registry_hash_matches'] = asset.get('sha256') == asset['registered_sha256'] if asset['exists'] else False
            if asset['exists']:
                data = json.loads(artifact_path.read_text())
                # Deliberate allowlist: no model weights, data rows or arbitrary keys.
                asset['metadata'] = {k: data[k] for k in ('version', 'train_end', 'training_data_cutoff', 'training_n', 'cohort', 'created_at') if k in data}
                if isinstance(data.get('anchor'), dict):
                    asset['metadata']['anchor_train_end'] = data['anchor'].get('train_end')
                if isinstance(data.get('core'), dict):
                    asset['metadata']['core_training'] = {k: data['core'][k]
                        for k in ('fit_end', 'fit_n') if k in data['core']}
                asset['referenced_files'] = []
                for key in ('booster_file', 'history_file', 'dataset', 'dataset_manifest', 'data_path'):
                    if not isinstance(data.get(key), str):
                        continue
                    dependency = Path(data[key])
                    if not dependency.is_absolute():
                        dependency = artifact_path.parent/dependency
                    if dependency.is_dir():
                        dependency = dependency/'manifest.json'
                    asset['referenced_files'].append(dict(describe(dependency), reference_key=key))
            item[name] = asset
        item['current_engine'] = route.get('current_engine', 'legacy_weighted_anchor')
        item['test_engine'] = route.get('test_engine', route.get('test_version', 'v5.weighted_model.predict'))
        routes[cohort] = item
    databases = sorted(set(root.glob('data/unified/*.sqlite')))
    source_names = subprocess.check_output(['git', 'ls-files', '-c', '-o', '--exclude-standard', '-z', '--', 'runtime', 'scripts'], cwd=REPO).decode().split('\0')
    source_files = [dict(describe(REPO/name), relative_path=name)
                    for name in sorted(set(n for n in source_names if n))
                    if (REPO/name).is_file() and Path(name).suffix in ('.py', '.json')]
    return {'schema': 'WCPP-VERSION-CHECKPOINT-1',
            'captured_at': datetime.now(timezone(timedelta(hours=8))).isoformat(),
            'source_base_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip(),
            'source_worktree_dirty': bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=REPO)),
            'runtime_root': str(root), 'registry_version': registry['version'],
            'source_files': source_files,
            'registry': describe(registry_path), 'routes': routes,
            'databases': [describe(p) for p in databases],
            'database_sidecars': [describe(Path(str(p)+suffix))
                                  for p in databases for suffix in ('-wal', '-shm')],
            'scope': 'IDENTITIES_ONLY_NOT_RUNTIME_BACKUP_OR_COMPLETE_DEPENDENCY_CLOSURE',
            'accuracy_improvement_claimed': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', default='/workspace/WCPP')
    p.add_argument('--out', required=True)
    a = p.parse_args()
    if Path(a.out).exists():
        raise FileExistsError(a.out)
    result = checkpoint(a.root)
    with Path(a.out).open('x') as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2, allow_nan=False)
    missing = sum(not r[m].get('registry_hash_matches', False)
                  for r in result['routes'].values() for m in ('current', 'test'))
    print(json.dumps({'manifest': a.out, 'routes': len(result['routes']),
                      'missing_or_mismatched_model_assets': missing}))
    if missing:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
