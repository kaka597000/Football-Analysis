"""Offline regressions: fail closed on identity conflicts and recover one freeze."""
from datetime import datetime, timedelta, timezone
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import publication
from research.analysis_freeze import verify


class PublicationRecoveryTests(unittest.TestCase):
    def fixture(self):
        now = datetime.now(timezone.utc)
        return {'match_id': 'SYNTHETIC_PUBLICATION',
                'cutoff_at': (now - timedelta(minutes=1)).isoformat(),
                'kickoff_at': (now + timedelta(days=1)).isoformat()}

    def prepared(self, base):
        out, root = base / 'out.json', base / 'freezes'
        with patch('publication.deliver', side_effect=OSError('synthetic interruption')):
            with self.assertRaises(OSError):
                publication.publish({'value': 1}, self.fixture(), out=out, freeze_root=root)
        return out, root, out.with_name(out.name + '.publication.json')

    def test_report_cannot_replace_recovery_journal(self):
        with tempfile.TemporaryDirectory() as d:
            out, root = Path(d) / 'out.json', Path(d) / 'freezes'
            with self.assertRaises(FileExistsError):
                publication.publish({}, self.fixture(), out=out, freeze_root=root,
                                    report=out.with_name(out.name + '.publication.json'))
            self.assertEqual(list(root.glob('*/*.json')), [])
            self.assertFalse(out.with_name(out.name + '.publication.json').exists())

    def test_fixture_and_model_input_identity_checked_before_freeze(self):
        for payload in ({'match_id': 'WRONG'}, {'current_model': {'input': {'match_id': 'WRONG'}}},
                        {'test_model': {'input': {'match_id': 'WRONG'}}}):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as d:
                out, root = Path(d) / 'out.json', Path(d) / 'freezes'
                with self.assertRaisesRegex(ValueError, 'identity mismatch'):
                    publication.publish(payload, self.fixture(), out=out, freeze_root=root)
                self.assertEqual(list(root.glob('*/*.json')), [])
                self.assertFalse(out.with_name(out.name + '.publication.json').exists())

    def test_explicit_model_times_cannot_be_replaced_by_outer_fixture(self):
        for field in ('kickoff_at', 'cutoff_at'):
            fixture = self.fixture()
            source = dict(fixture)
            source[field] = (datetime.fromisoformat(source[field]) - timedelta(days=2)).isoformat()
            payload = {'current_model': {'input': source}}
            with self.subTest(field=field), tempfile.TemporaryDirectory() as d:
                out, root = Path(d) / 'out.json', Path(d) / 'freezes'
                with self.assertRaisesRegex(ValueError, 'input time mismatch'):
                    publication.publish(payload, fixture, out=out, freeze_root=root)
                self.assertEqual(list(root.glob('*/*.json')), [])

    def test_equivalent_offsets_are_same_model_input_times(self):
        fixture = self.fixture()
        source = dict(fixture)
        for field in ('kickoff_at', 'cutoff_at'):
            source[field] = datetime.fromisoformat(source[field]).astimezone(
                timezone(timedelta(hours=8))).isoformat()
        with tempfile.TemporaryDirectory() as d:
            result = publication.publish({'current_model': {'input': source}}, fixture,
                                         out=Path(d) / 'out.json', freeze_root=Path(d) / 'freezes')
            self.assertEqual(result['current_model']['input'], source)

    def test_same_match_id_cannot_hide_conflicting_fixture_context(self):
        fixture = dict(self.fixture(), home='A', away='B', competition='eng.1',
                       cohort='eng.1', stage='regular', leg=1, neutral=False,
                       total_quote={'line': 2.5, 'bookmaker': 'synthetic'})
        changes = {'home': 'OTHER', 'away': 'OTHER', 'competition': 'esp.1',
                   'cohort': 'esp.1', 'stage': 'playoff', 'leg': 2, 'neutral': True,
                   'total_quote': {'line': 3.5, 'bookmaker': 'synthetic'}}
        for field, value in changes.items():
            source = copy.deepcopy(fixture)
            source[field] = value
            payload = {'current_model': {'input': copy.deepcopy(fixture)},
                       'test_model': {'input': source}}
            with self.subTest(field=field), tempfile.TemporaryDirectory() as d:
                out, root = Path(d) / 'out.json', Path(d) / 'freezes'
                with self.assertRaisesRegex(ValueError, 'input context mismatch'):
                    publication.publish(payload, fixture, out=out, freeze_root=root)
                self.assertEqual(list(root.glob('*/*.json')), [])
                self.assertFalse(out.with_name(out.name + '.publication.json').exists())

    def test_provided_input_cannot_omit_context_or_add_result_data(self):
        fixture = dict(self.fixture(), home='A', away='B')
        for source in ({k: v for k, v in fixture.items() if k != 'home'},
                       dict(fixture, home_goals=1)):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as d:
                with self.assertRaisesRegex(ValueError, 'input context mismatch'):
                    publication.publish({'current_model': {'input': source}}, fixture,
                                        out=Path(d) / 'out.json', freeze_root=Path(d) / 'freezes')

    def test_changed_journal_request_cannot_create_second_freeze(self):
        for field, value in [('id', 'changed-id'), ('output', {'value': 2}),
                             ('freeze_root', '/tmp/synthetic-wrong-freeze-root')]:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as d:
                out, root, journal = self.prepared(Path(d))
                state = json.loads(journal.read_text(encoding='utf-8'))
                state[field] = value
                journal.write_text(json.dumps(state), encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                    publication.publish(None, None, out=out, freeze_root=root, resume=True)
                self.assertEqual(len(list(root.glob('*/*.json'))), 1)
                self.assertFalse(out.exists())

    def test_receipt_is_verified_even_for_legacy_journal(self):
        with tempfile.TemporaryDirectory() as d:
            out, root, journal = self.prepared(Path(d))
            state = json.loads(journal.read_text(encoding='utf-8'))
            state.pop('request_sha256')
            state['receipt']['sha256'] = 'incorrect'
            journal.write_text(json.dumps(state), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'receipt mismatch'):
                publication.publish(None, None, out=out, freeze_root=root, resume=True)
            self.assertEqual(len(list(root.glob('*/*.json'))), 1)

    def test_corrupted_freeze_is_not_delivered(self):
        with tempfile.TemporaryDirectory() as d:
            out, root, journal = self.prepared(Path(d))
            record_path = next(root.glob('*/*.json'))
            record = json.loads(record_path.read_text(encoding='utf-8'))
            record['payload']['value'] = 999
            record_path.write_text(json.dumps(record), encoding='utf-8')
            with self.assertRaises(ValueError):
                publication.publish(None, None, out=out, freeze_root=root, resume=True)
            self.assertFalse(out.exists())

    def test_crash_between_freeze_and_receipt_journal_reuses_same_record(self):
        with tempfile.TemporaryDirectory() as d:
            out, root = Path(d) / 'out.json', Path(d) / 'freezes'
            original = publication.atomic_json
            def interrupt(path, state):
                if state['status'] == 'FROZEN':
                    raise OSError('synthetic crash before receipt journal')
                return original(path, state)
            with patch('publication.atomic_json', side_effect=interrupt), self.assertRaises(OSError):
                publication.publish({'value': 1}, self.fixture(), out=out, freeze_root=root)
            records = list(root.glob('*/*.json'))
            self.assertEqual(len(records), 1)
            original_record = records[0].read_bytes()
            result = publication.publish(None, None, out=out, freeze_root=root, resume=True)
            self.assertEqual(len(list(root.glob('*/*.json'))), 1)
            self.assertEqual(records[0].read_bytes(), original_record)
            self.assertEqual(result['freeze_receipt']['sha256'], verify(json.loads(original_record))['record_sha256'])

    def test_atomic_writes_and_identical_redelivery_sync_directory(self):
        with tempfile.TemporaryDirectory() as d, patch('publication.sync_directory') as sync:
            base = Path(d)
            publication.atomic_json(base / 'state.json', {'status': 'PREPARED'})
            publication.deliver(base / 'result.json', b'complete')
            publication.deliver(base / 'result.json', b'complete')
            self.assertEqual(sync.call_count, 3)
            self.assertTrue(all(call.args == (base,) for call in sync.call_args_list))
            with self.assertRaises(FileExistsError):
                publication.deliver(base / 'result.json', b'different')
            self.assertEqual((base / 'result.json').read_bytes(), b'complete')


if __name__ == '__main__':
    unittest.main()
