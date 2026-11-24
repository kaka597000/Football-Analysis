import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from audit_open_quotes import archived_quote


class CaptureBindingTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.report = Path(temp.name) / 'reports' / 'night-batch'
        self.raw = self.report / 'facts-rest' / 'raw' / 'Home-Away-espn-summary.json'
        self.raw.parent.mkdir(parents=True)
        self.sidecar = self.raw.with_suffix('.json.meta.json')
        self.source = {
            'raw_file': '/old/snapshot/WCPP/reports/night-batch/facts-rest/raw/' + self.raw.name,
            'source_url': 'https://site.api.espn.com/summary?event=123',
            'query_time': '2026-11-07T11:30:00Z',
        }
        self.quote = {'provider': {'name': 'DraftKings'}}
        self.fixture = {'cutoff_at': '2026-11-07T12:00:00Z', 'sources': [self.source]}
        self.capture('123')

    def capture(self, event):
        raw = json.dumps({'header': {'id': event}, 'odds': [self.quote]}).encode()
        self.raw.write_bytes(raw)
        self.source['response_sha256'] = hashlib.sha256(raw).hexdigest()
        self.meta = {'sha256': self.source['response_sha256'], 'url': self.source['source_url'],
                     'query_time': self.source['query_time']}
        self.sidecar.write_text(json.dumps(self.meta))

    def read(self):
        return archived_quote(self.report, self.fixture, '123')

    def test_frozen_team_named_member_and_renamed_lookalike(self):
        self.assertEqual(self.read(), ('paired', self.quote))
        self.raw.rename(self.raw.parent / 'espn-summary-123.json')
        self.assertEqual(self.read(), ('missing_bound_body', None))

    def test_wrong_batch_traversal_and_symlink_escape(self):
        original = self.source['raw_file']
        for bad in (original.replace('night-batch', 'other-batch'),
                    original.replace('facts-rest/', 'untrusted/../facts-rest/')):
            with self.subTest(bad=bad):
                self.source['raw_file'] = bad
                with self.assertRaisesRegex(ValueError, 'outside'):
                    self.read()
        self.source['raw_file'] = original
        # A same-content external sidecar still falls outside the restored report.
        outside = self.report.parent / 'unrelated.meta.json'
        self.sidecar.rename(outside)
        try:
            self.sidecar.symlink_to(outside)
        except OSError as exc:
            self.skipTest(f'symlink unsupported on test filesystem: {exc}')
        with self.assertRaisesRegex(ValueError, 'outside'):
            self.read()

    def test_missing_and_conflicting_capture_metadata(self):
        self.sidecar.unlink()
        self.assertEqual(self.read(), ('missing_capture_metadata', None))
        for change in ({'sha256': 'bad'}, {'url': self.meta['url'] + '0'},
                       {'query_time': '2026-11-07T11:59:00Z'}):
            with self.subTest(change=change):
                self.sidecar.write_text(json.dumps(dict(self.meta, **change)))
                with self.assertRaisesRegex(ValueError, 'metadata conflicts'):
                    self.read()

    def test_corrupt_bytes_or_different_event_even_if_hash_agrees(self):
        self.raw.write_bytes(self.raw.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'checksum conflicts'):
            self.read()
        self.capture('456')
        with self.assertRaisesRegex(ValueError, 'event conflicts'):
            self.read()

    def test_ambiguous_or_too_late_source(self):
        self.fixture['sources'] = [self.source, self.source.copy()]
        self.assertEqual(self.read(), ('missing_bound_source', None))
        self.fixture['sources'] = [dict(self.source, query_time=self.fixture['cutoff_at'])]
        self.assertEqual(self.read(), ('missing_bound_source', None))


if __name__ == '__main__':
    unittest.main()
