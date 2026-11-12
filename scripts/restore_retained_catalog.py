"""Restore checksum-locked selected files from durable archives, without replacement."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tarfile
import urllib.request


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def safe_path(root, name):
    rel = Path(name)
    if rel.is_absolute() or '..' in rel.parts:
        raise ValueError('unsafe recovery member')
    path = root / rel
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('recovery path escapes workspace')
    return path


def materialize(group, cache, catalog_dir):
    target = cache / (group['sha256'] + '.tar.gz')
    if target.exists():
        if sha(target) != group['sha256']:
            raise ValueError('cached archive checksum mismatch')
        return target
    temporary = target.with_suffix('.pending')
    with temporary.open('wb') as out:
        for part in group['parts']:
            if 'relative_path' in part:
                source = safe_path(catalog_dir, part['relative_path']).open('rb')
            else:
                if not part['url'].startswith('https://raw.githubusercontent.com/'):
                    raise ValueError('immutable HTTPS archive URL required')
                source = urllib.request.urlopen(part['url'], timeout=90)
            h = hashlib.sha256(); size = 0
            with source:
                for block in iter(lambda: source.read(1024 * 1024), b''):
                    h.update(block); size += len(block); out.write(block)
            if size != part['bytes'] or h.hexdigest() != part['sha256']:
                raise ValueError('archive part checksum mismatch')
    if sha(temporary) != group['sha256']:
        raise ValueError('assembled archive checksum mismatch')
    temporary.replace(target)
    return target


def restore(catalog_path, workspace, cache):
    catalog_path, workspace, cache = map(Path, (catalog_path, workspace, cache))
    catalog = json.loads(catalog_path.read_text())
    workspace.mkdir(parents=True, exist_ok=True); cache.mkdir(parents=True, exist_ok=True)
    receipt = []
    for group in catalog['archives']:
        archive = materialize(group, cache, catalog_path.parent)
        wanted = {v['path']: v for v in group['members']}
        found = set()
        with tarfile.open(archive) as t:
            for member in t:
                if member.name not in wanted:
                    continue
                if not member.isfile() or member.name in found:
                    raise ValueError('duplicate or nonregular recovery member')
                found.add(member.name); spec = wanted[member.name]
                path = safe_path(workspace, spec.get('destination', member.name))
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_name(path.name + '.recovery-pending')
                try:
                    with temporary.open('xb') as out:
                        shutil.copyfileobj(t.extractfile(member), out)
                    if sha(temporary) != spec['sha256']:
                        raise ValueError('recovered member checksum mismatch')
                    if path.exists():
                        if sha(path) != spec['sha256']:
                            raise ValueError('existing runtime conflicts; no replacement: ' + str(path))
                    else:
                        # A complete checked file is published without overwriting.
                        import os
                        os.link(temporary, path)
                    receipt.append({'path': str(path), 'sha256': spec['sha256']})
                finally:
                    temporary.unlink(missing_ok=True)
        if found != set(wanted):
            raise ValueError('archive lacks required members')
    return {'schema': 'WCPP-DURABLE-RECOVERY-1', 'catalog_sha256': sha(catalog_path),
            'files': receipt, 'production_overwrites': 0, 'actions_download_required': False}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--catalog', required=True); p.add_argument('--workspace', required=True)
    p.add_argument('--cache', required=True); p.add_argument('--receipt', required=True)
    a = p.parse_args()
    if Path(a.receipt).exists():
        raise FileExistsError(a.receipt)
    result = restore(a.catalog, a.workspace, a.cache)
    with Path(a.receipt).open('x') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print('Verified restored files:', len(result['files']))
