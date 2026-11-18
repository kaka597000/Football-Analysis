"""Storage regressions for the unchanged domestic and UCL bundle formats."""
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from runtime.automation import prospective_bundle as bundle
from runtime.automation import prospective_cycle as cycle


class BundleTests(unittest.TestCase):
    def test_interrupted_export_can_retry_without_changing_prior_exports(self):
        with TemporaryDirectory() as tmp, ExitStack() as mocks:
            root = Path(tmp)
            def write(name, data):
                path = root / name
                path.write_text(json.dumps(data))
                return path
            registry = write('registry', {'models': {}})
            protocol = write('protocol', {'protocol_sha256': 'locked', 'registry_path': str(registry),
                                          'cohorts': {}, 'source_files_sha256': {}})
            plans = write('plans', {'protocol_sha256': 'locked', 'plans': []})
            packages = write('packages', [])
            queue = write('queue', {})
            mocks.enter_context(patch.object(bundle.fv, 'check_protocol'))
            mocks.enter_context(patch.object(bundle, '_allowed_original', side_effect=Path))
            dest = root / 'published'
            def export():
                return cycle.export_bundle(dest, protocol, plans, packages, queue_path=queue, freeze_root=root)
            with self.assertRaisesRegex(ValueError, 'queue'):
                export()
            self.assertFalse(dest.exists())
            write('queue', [])
            saved = export()
            self.assertEqual(cycle.check_bundle(dest), saved)
            self.assertEqual(bundle.resolve(dest, saved, registry).read_bytes(), registry.read_bytes())
            with self.assertRaises(FileExistsError):
                export()
            self.assertEqual(cycle.check_bundle(dest), saved)

    def test_interrupted_asset_install_leaves_original_destination_retryable(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            content = b'locked artifact'
            checksum = hashlib.sha256(content).hexdigest()
            (root / 'object').write_bytes(content)
            alias = '/workspace/WCPP/asset'
            dest = root / 'restored' / 'asset'
            m = {'capture_assets': [alias], 'aliases': {alias: checksum}, 'objects': {checksum: {'path': 'object'}}}
            with patch.object(bundle, '_allowed_original', return_value=dest):
                with patch('os.link', side_effect=OSError('interrupted install')):
                    with self.assertRaisesRegex(OSError, 'interrupted install'):
                        bundle._restore_assets(root, m)
                self.assertFalse(dest.exists())
                self.assertEqual(list(dest.parent.iterdir()), [])
                bundle._restore_assets(root, m)
                self.assertEqual(dest.read_bytes(), content)
                previous = dest.stat().st_mtime_ns
                bundle._restore_assets(root, m)
                self.assertEqual(dest.stat().st_mtime_ns, previous)

    def test_concurrent_asset_install_reuses_equal_bytes_and_preserves_conflicts(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            content = b'expected'
            checksum = hashlib.sha256(content).hexdigest()
            (root / 'object').write_bytes(content)
            alias = '/workspace/WCPP/concurrent'
            m = {'capture_assets': [alias], 'aliases': {alias: checksum}, 'objects': {checksum: {'path': 'object'}}}
            for winner in (content, b'conflicting'):
                dest = root / winner.decode()
                def install_elsewhere(_temp, target):
                    target.write_bytes(winner)
                    raise FileExistsError(target)
                with self.subTest(winner=winner), patch.object(bundle, '_allowed_original', return_value=dest), \
                        patch('os.link', side_effect=install_elsewhere):
                    if winner == content:
                        bundle._restore_assets(root, m)
                    else:
                        with self.assertRaisesRegex(ValueError, 'destination conflict'):
                            bundle._restore_assets(root, m)
                    self.assertEqual(dest.read_bytes(), winner)

    def test_ordinary_cycle_checks_the_full_object_inventory_once(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / 'future' / 'bundle'
            root.mkdir(parents=True)
            m = {'schema': bundle.SCHEMA, 'objects': {}, 'queue': [], 'packages': [],
                 'plans': {'plans': []}, 'protected_first_slots': []}
            m['manifest_sha256'] = bundle.digest(m)
            (root / 'manifest.json').write_text(json.dumps(m))
            with patch.object(cycle, 'check_bundle', wraps=bundle.check_bundle) as checks:
                report = cycle.cycle(root, max_captures=0, max_result_requests=0)
            checks.assert_called_once_with(root)
            self.assertEqual(report['population'], 0)
