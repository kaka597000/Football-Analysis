"""Recovery checksum, path and no-overwrite guarantees on tiny synthetic archives."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from restore_retained_catalog import restore


class DurableRecoveryTests(unittest.TestCase):
    def setup_catalog(self, root, destination='WCPP/file.txt', wrong=False):
        archive = root / 'data.tar.gz'; body = b'original'
        with tarfile.open(archive, 'w:gz') as t:
            info = tarfile.TarInfo('WCPP/file.txt'); info.size = len(body)
            t.addfile(info, io.BytesIO(body))
        sha = hashlib.sha256(archive.read_bytes()).hexdigest()
        catalog = {'archives': [{'sha256': sha, 'parts': [{'relative_path': archive.name,
            'bytes': archive.stat().st_size, 'sha256': sha}], 'members': [{'path': 'WCPP/file.txt',
            'destination': destination, 'sha256': '0' * 64 if wrong else hashlib.sha256(body).hexdigest()}]}]}
        p = root / 'catalog.json'; p.write_text(json.dumps(catalog)); return p

    def test_restore_repeat_and_conflict(self):
        with tempfile.TemporaryDirectory() as d:
            r = Path(d); cat = self.setup_catalog(r); work = r / 'workspace'; cache = r / 'cache'
            self.assertEqual(len(restore(cat, work, cache)['files']), 1)
            self.assertEqual(len(restore(cat, work, cache)['files']), 1)
            p = work / 'WCPP/file.txt'; p.write_bytes(b'new runtime')
            with self.assertRaisesRegex(ValueError, 'no replacement'):
                restore(cat, work, cache)
            self.assertEqual(p.read_bytes(), b'new runtime')

    def test_hash_failure_never_publishes_file(self):
        with tempfile.TemporaryDirectory() as d:
            r = Path(d); cat = self.setup_catalog(r, wrong=True)
            with self.assertRaisesRegex(ValueError, 'checksum'):
                restore(cat, r / 'workspace', r / 'cache')
            self.assertFalse((r / 'workspace/WCPP/file.txt').exists())

    def test_path_escape_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            r = Path(d); cat = self.setup_catalog(r, destination='../outside')
            with self.assertRaisesRegex(ValueError, 'unsafe'):
                restore(cat, r / 'workspace', r / 'cache')
            self.assertFalse((r / 'outside').exists())


if __name__ == '__main__':
    unittest.main()
