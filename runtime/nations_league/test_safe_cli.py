import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nations_league import model, safe_cli
from nations_league import test_model as model_test_fixtures


class SafeNationsTests(unittest.TestCase):
    def fixtures(self, root, count=1):
        result = []
        for i in range(count):
            folder = root / ('source-' + str(i))
            folder.mkdir()
            fixture = model_test_fixtures.NationsModelTests().fixture(folder)
            event_id = str(101 + i)
            raw_path = Path(fixture['phase_evidence']['source_path'])
            raw = model.read(raw_path)
            raw['header']['id'] = event_id
            raw['header']['competitions'][0]['id'] = event_id
            raw_path.write_text(json.dumps(raw))
            fixture.update(match_id='espn:' + event_id, source_event_id=event_id)
            fixture['phase_evidence'].update(source_sha256=model.sha(raw_path),
                source_url='https://site.api.espn.com/apis/site/v2/sports/soccer/uefa.nations/summary?event=' + event_id)
            result.append(fixture)
        return result

    def index(self, root, fixture):
        dependency = root / 'dependency.json'
        model.write(dependency, {})
        routes = {}
        for role in ('current', 'test'):
            core = {'intercept': 0., 'home_advantage': .1, 'train_end': '2025-01-01'}
            if role == 'test':
                core.update(attack={'espn:1': .1}, defence={'espn:1': .1})
            artifact = {'schema': 'WCPP-NATIONS-POISSON-1', 'version': 'SYNTHETIC-' + role,
                        'role': role, 'competition': 'uefa.nations', 'cohort': fixture['cohort'],
                        'league_level': fixture['league_level'], 'stage': fixture['stage'],
                        'core': core, 'train_end': core['train_end'], 'created_at': '2025-01-02T00:00:00Z',
                        'engine_path': str(Path(model.__file__).resolve()),
                        'engine_sha256': model.sha(model.__file__)}
            for name in ('dataset', 'protocol'):
                artifact[name + '_path'] = str(dependency)
                artifact[name + '_sha256'] = model.sha(dependency)
            path = root / (role + '-artifact.json')
            model.write(path, artifact)
            routes[role] = {'path': str(path), 'sha256': model.sha(path)}
        path = root / 'index.json'
        model.write(path, {'schema': 'WCPP-NATIONS-PAIRED-CANDIDATES-1', 'routes': {'A': routes}})
        return path, routes

    def test_rejects_wrong_event_id_and_every_supported_label_alias(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.fixtures(Path(directory))[0]
            safe_cli.validate_future(fixture)
            for change in ({'match_id': 'espn:999'}, *({name: 0} for name in safe_cli.TARGET_LABELS)):
                with self.subTest(change=change), self.assertRaises(ValueError):
                    safe_cli.validate_future({**fixture, **change})

    def test_guard_preserves_old_matrix_and_records_executed_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = self.fixtures(root)[0]
            _, refs = self.index(root, fixture)
            for role in ('current', 'test'):
                path = refs[role]['path']
                before = Path(path).read_bytes()
                original = model.predict(path, fixture)
                result = safe_cli.predict(path, fixture, expected_role=role)
                self.assertEqual(result['matrix'], original['matrix'])
                self.assertEqual(result['expected_goals'], original['expected_goals'])
                self.assertEqual(result['execution_guard']['validator']['sha256'], model.sha(safe_cli.__file__))
                self.assertEqual(Path(path).read_bytes(), before)

    def test_later_bad_artifact_never_starts_freezing_or_creates_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixtures = self.fixtures(root, 2)
            index, _ = self.index(root, fixtures[0])
            original = safe_cli.predict

            def invalid_second(path, fixture, expected_role=None):
                if fixture['match_id'] == fixtures[1]['match_id'] and expected_role == 'test':
                    raise ValueError('synthetic later candidate failure')
                return original(path, fixture, expected_role)

            with patch.object(safe_cli, 'predict', side_effect=invalid_second), patch.object(model, 'save') as save:
                with self.assertRaisesRegex(ValueError, 'later candidate'):
                    safe_cli.freeze_pairs(index, fixtures, root / 'out', root / 'freezes')
                save.assert_not_called()
            self.assertFalse((root / 'out').exists())
            self.assertFalse((root / 'freezes').exists())

    def test_wrong_role_in_index_fails_before_any_freeze(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixtures = self.fixtures(root)
            index, refs = self.index(root, fixtures[0])
            value = model.read(index)
            value['routes']['A']['test'] = refs['current']
            index.write_text(json.dumps(value))
            with patch.object(model, 'save') as save:
                with self.assertRaisesRegex(ValueError, 'role'):
                    safe_cli.freeze_pairs(index, fixtures, root / 'out', root / 'freezes')
                save.assert_not_called()

    def test_later_unpublishable_payload_is_rejected_before_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixtures = self.fixtures(root, 2)
            index, _ = self.index(root, fixtures[0])
            for extra in ({'research_note': float('nan')}, {'api_key': 'SYNTHETIC_TEST_VALUE'}):
                bad = [fixtures[0], {**fixtures[1], **extra}]
                with self.subTest(extra=tuple(extra)), patch.object(model, 'save') as save:
                    with self.assertRaises(ValueError):
                        safe_cli.freeze_pairs(index, bad, root / 'out', root / 'freezes')
                    save.assert_not_called()
                self.assertFalse((root / 'out').exists())

    def test_full_batch_freezes_with_guard_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixtures = self.fixtures(root, 2)
            index, _ = self.index(root, fixtures[0])
            original = copy.deepcopy(fixtures)
            result = safe_cli.freeze_pairs(index, fixtures, root / 'out', root / 'freezes')
            self.assertEqual(result['frozen'], 2)
            self.assertEqual(fixtures, original)
            plan = model.read(root / 'out' / 'protocol.json')
            self.assertEqual(plan['execution_guard']['version'], safe_cli.GUARD_VERSION)
            receipts = model.read(root / 'out' / 'receipts.json')
            for receipt in receipts:
                record = model.read(receipt['path'])
                self.assertEqual(record['track'], 'nations-paired-candidates')
                self.assertEqual(record['payload']['execution_guard']['version'], safe_cli.GUARD_VERSION)
                self.assertFalse(record['payload']['formal_kpi_eligible'])
            with patch.object(model, 'save') as save:
                with self.assertRaises(FileExistsError):
                    safe_cli.freeze_pairs(index, fixtures, root / 'out', root / 'freezes')
                save.assert_not_called()


if __name__ == '__main__':
    unittest.main()
