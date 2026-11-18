"""Export and restore the immutable domestic and UCL forward-validation bundles."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

# The retained validator modules support their original top-level runtime imports.
runtime_path = str(Path(__file__).resolve().parents[1])
if runtime_path not in sys.path:
    sys.path.insert(0, runtime_path)

from ..publication import atomic_json, deliver
from ..research.analysis_freeze import digest, verify
from ..research_program import future_validation as fv
from ..research_program.future_validation import read, sha

SCHEMA = 'WCPP-PORTABLE-FUTURE-BUNDLE-1'
UCL_SCHEMA = 'WCPP-PORTABLE-UCL-REVIEW-BUNDLE-1'


def _allowed_original(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('absolute normalized original path required')
    allowed = ('/workspace/WCPP/', '/workspace/recovered-workspace-',
               '/workspace/scope18-audit-', '/workspace/Football-Analysis/')
    if not str(path).startswith(allowed):
        raise ValueError('original capture path outside allowlist')
    return path


class _BundleWriter:
    def __init__(self, root):
        self.root = root
        (root / 'objects').mkdir(parents=True)
        self.objects = {}
        self.aliases = {}
        self.assets = []

    def add(self, path, *, asset=False):
        path = Path(path)
        content = path.read_bytes()
        checksum = hashlib.sha256(content).hexdigest()
        if checksum not in self.objects:
            relative = 'objects/' + checksum
            (self.root / relative).write_bytes(content)
            self.objects[checksum] = {'path': relative, 'bytes': len(content), 'sha256': checksum}
        for alias in (str(path), str(path.resolve())):
            if self.aliases.setdefault(alias, checksum) != checksum:
                raise ValueError('alias content conflict')
            if asset:
                _allowed_original(alias)
                if alias not in self.assets:
                    self.assets.append(alias)

    def finish(self, manifest):
        manifest.update(objects=self.objects, aliases=self.aliases, capture_assets=self.assets,
                        created_at=datetime.now(timezone.utc).isoformat(), automatic_promotion=False)
        manifest['manifest_sha256'] = digest(manifest)
        atomic_json(self.root / 'manifest.json', manifest)
        return manifest


@contextmanager
def _new_bundle(destination):
    destination = Path(destination).resolve()
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='.bundle-', dir=destination.parent) as staging:
        root = Path(staging) / 'bundle'
        yield _BundleWriter(root)
        if destination.exists():
            raise FileExistsError(destination)
        root.rename(destination)


def export_bundle(dest, protocol_path, plans_path, packages_path, *, queue_path=None,
                  dependencies_manifest=None, freeze_root=fv.DEFAULT_ROOT):
    with _new_bundle(dest) as writer:
        protocol = read(protocol_path)
        fv.check_protocol(protocol)
        plans, packages = read(plans_path), read(packages_path)
        if plans['protocol_sha256'] != protocol['protocol_sha256']:
            raise ValueError('plan protocol mismatch')
        writer.add(protocol['registry_path'], asset=True)
        registry = read(protocol['registry_path'])
        for cohort, entry in protocol['cohorts'].items():
            writer.add(entry['candidate_path'], asset=True)
            for key in ('artifact_path', 'test_artifact_path'):
                path = registry['models'][cohort].get(key)
                if path:
                    writer.add(path, asset=True)
        for path in protocol['source_files_sha256']:
            writer.add(path, asset=True)
        if dependencies_manifest:
            for entry in read(dependencies_manifest)['entries']:
                original = Path(entry['original_path'])
                if not original.exists() or sha(original) != entry['sha256']:
                    raise ValueError('dependency original unavailable or mismatched')
                writer.add(original, asset=entry['kind'] not in ('ACTUAL_FREEZE', 'SOURCE_EVIDENCE'))
        for receipt in plans['plans']:
            if verify(read(receipt['path']))['record_sha256'] != receipt['sha256']:
                raise ValueError('plan receipt checksum')
            writer.add(receipt['path'])
        for package in packages:
            for role in ('current', 'candidate'):
                receipt = package[role + '_receipt']
                if verify(read(receipt['path']))['record_sha256'] != receipt['sha256']:
                    raise ValueError('pair receipt checksum')
                writer.add(receipt['path'])
        protected = []
        for path in Path(freeze_root).glob('*/000001.json'):
            rec = verify(read(path))
            if rec['stage'] == 'prematch' and rec['track'] in ('current-test-pair', 'challenger-independent'):
                protected.append({'match_id': rec['match_id'], 'record_sha256': rec['record_sha256']})
        queue = read(queue_path) if queue_path else []
        if not isinstance(queue, list):
            raise ValueError('fixture queue must be a list')
        return writer.finish({'schema': SCHEMA, 'protocol': protocol, 'plans': plans,
                              'packages': packages, 'queue': queue, 'protected_first_slots': protected,
                              'historical_asof_certified': False})


def export_ucl_bundle(dest, protocol_path, plan_path, packages_path):
    from ..research_program import ucl_refresh_forward as forward, ucl_refresh_review as reviewer
    protocol = read(protocol_path)
    forward.check(protocol)
    receipt = read(plan_path)
    plan = verify(read(receipt['path']))
    if plan['record_sha256'] != receipt['sha256']:
        raise ValueError('UCL plan receipt binding')
    packages = read(packages_path)
    reviewer.review(protocol, plan, packages, [])
    with _new_bundle(dest) as writer:
        for path in protocol['files_sha256']:
            writer.add(path, asset=True)
        writer.add(receipt['path'])
        for package in packages:
            for role in ('baseline', 'refreshed'):
                writer.add(package[role]['path'])
        return writer.finish({'schema': UCL_SCHEMA, 'protocol': protocol, 'plan': receipt,
                              'packages': packages, 'state_directory': 'ucl-state',
                              'review_source_sha256': sha(reviewer.__file__)})


def check_bundle(root, *, schema=SCHEMA):
    root = Path(root).resolve()
    manifest = read(root / 'manifest.json')
    if (manifest.get('schema') != schema or
            digest({k: v for k, v in manifest.items() if k != 'manifest_sha256'}) != manifest['manifest_sha256']):
        raise ValueError('bundle manifest integrity')
    for checksum, entry in manifest['objects'].items():
        path = root / entry['path']
        if (not path.resolve().is_relative_to(root) or path.is_symlink()
                or path.stat().st_size != entry['bytes'] or sha(path) != checksum):
            raise ValueError('bundle object integrity')
    return manifest


def resolve(root, manifest, original):
    root = Path(root)
    checksum = manifest['aliases'].get(str(original))
    if checksum is not None:
        return root / manifest['objects'][checksum]['path']
    # A later capture is in private state. Only its receipt path is relocatable.
    state_name = manifest.get('state_directory', 'state')
    if state_name not in ('state', 'ucl-state'):
        raise ValueError('unsupported private state directory')
    _, marker, suffix = str(original).partition('/future/' + state_name + '/')
    if marker:
        state = (root.parent / state_name).resolve()
        path = (state / suffix).resolve()
        if path.is_relative_to(state) and path.is_file():
            return path
    raise ValueError('reference missing from bundle')


def load_receipt(root, manifest, receipt):
    """Return relocated receipt metadata and its verified, unmodified frozen record."""
    path = resolve(root, manifest, receipt['path'])
    record = verify(read(path))
    if record['record_sha256'] != receipt['sha256']:
        raise ValueError('resolved receipt checksum')
    return dict(receipt, path=str(path)), record


def _restore_assets(root, manifest):
    for alias in manifest['capture_assets']:
        dest = _allowed_original(alias)
        expected = manifest['aliases'][alias]
        if not dest.exists():
            content = resolve(root, manifest, alias).read_bytes()
            if hashlib.sha256(content).hexdigest() != expected:
                raise ValueError('bundle object integrity')
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                deliver(dest, content)
                continue
            except FileExistsError:
                # A concurrent restore may have just installed the same artifact.
                pass
        if not dest.is_file() or sha(dest) != expected:
            raise ValueError('capture destination conflict: ' + str(dest))


def _check_live_source(original, expected, mismatch):
    _, marker, suffix = original.partition('/runtime/')
    if not marker:
        raise ValueError('unsupported executable source path')
    live = Path(__file__).resolve().parents[1] / suffix
    if not live.is_file() or sha(live) != expected:
        raise ValueError(mismatch)


def restore_domestic(root, manifest):
    """Restore capture assets after the caller has verified the domestic bundle."""
    for alias, expected in manifest['protocol']['source_files_sha256'].items():
        _check_live_source(alias, expected, 'live executable differs from protocol')
    _restore_assets(root, manifest)
    fv.check_protocol(manifest['protocol'])


def hydrate(root):
    """Verify a standalone domestic bundle and restore the original capture paths."""
    root = Path(root).resolve()
    manifest = check_bundle(root)
    restore_domestic(root, manifest)
    return manifest


def restore_ucl(root, manifest):
    from ..research_program import ucl_refresh_review as reviewer
    if sha(reviewer.__file__) != manifest['review_source_sha256']:
        raise ValueError('live UCL review source changed')
    for alias, expected in manifest['protocol']['files_sha256'].items():
        if alias.startswith('/workspace/Football-Analysis/runtime/'):
            _check_live_source(alias, expected, 'live UCL source differs from protocol')
    _restore_assets(root, manifest)
