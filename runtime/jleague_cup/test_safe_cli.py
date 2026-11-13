import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from jleague_cup import database, evidence, model, quarterfinals, safe_cli, safe_evidence
from jleague_cup import test_cup as cup_cases, test_quarterfinals as qf_cases


class SafeCupTests(unittest.TestCase):
    def detail(self, root, listing='2:1 AfE', shown='3:1', status='After Extra Time',
               partial='(1:0, 0:1, 2:0)'):
        source, row = cup_cases.CupTests().capture(root, score=listing, heading='Final', year=2024)
        path = root / 'detail.html'
        path.write_text('<link rel="canonical" href="' + row['event_url'] + '">'
            '<input id="isFinished" value="1"><span id="js-eventstage">' + status + '</span>'
            '<span id="js-partial">' + partial + '</span><span id="js-score">' + shown + '</span>')
        detail = {'raw_path': str(path), 'raw_sha256': evidence.sha(path),
                  'source_url': row['event_url'], 'query_time': '2026-10-10T00:00:00Z'}
        row['period_evidence'] = detail
        return row, {'sources': [source, detail]}

    def edition_artifact(self, root):
        dep = root / 'dependency.json'
        dep.write_text('{}')
        scope = {'cohort': 'jpn.league_cup:edition-2025:round-of-128:single:0',
                 'era': 'edition-2025', 'stage': 'round-of-128', 'tie_format': 'single', 'leg': None}
        artifact = {**scope, 'schema': 'WCPP-JCUP-POISSON-1', 'version': 'SYNTHETIC', 'role': 'test',
                    'core': {'teams': [], 'coefficients': [0., 0., 0.], 'fit_end': '2025-01-01'}}
        for key, path in [('protocol', dep), ('dataset', dep), ('engine', model.__file__)]:
            artifact[key + '_path'] = str(path)
            artifact[key + '_sha256'] = evidence.sha(path)
        path = root / 'artifact.json'
        path.write_text(json.dumps(artifact))
        fixture = {**scope, 'competition': 'jpn.league_cup', 'gender': 'men', 'age': 'senior',
                   'date': '2025-02-01', 'neutral': None, 'home': 'a', 'away': 'b'}
        return path, fixture

    def qf_inputs(self, root):
        helper = qf_cases.QuarterfinalTests()
        _, cert = helper.cert(root)
        fixture = helper.fixture(root)
        dep = root / 'dependency.json'
        dep.write_text('{}')
        dependencies = {k: {'path': str(p), 'sha256': evidence.sha(p)} for k, p in {
            'certificate': cert, 'database': dep, 'protocol': dep, 'dataset': dep,
            'engine': quarterfinals.__file__, 'core_engine': model.__file__,
            'evidence_engine': evidence.__file__, 'database_engine': database.__file__}.items()}
        artifacts = {}
        for role in ('current', 'test'):
            artifact = {'schema': quarterfinals.SCHEMA, 'version': 'SYNTHETIC', 'role': role, 'leg': 1,
                        'core': {'coefficients': [0., 0., 0.], 'teams': [], 'fit_end': '2025-01-01'},
                        'built_at': '2025-02-01T00:00:00Z', 'dependencies': dependencies}
            path = root / (role + '.json')
            path.write_text(json.dumps(artifact))
            artifacts[role] = {'path': str(path), 'sha256': evidence.sha(path)}
        index = root / 'index.json'
        index.write_text(json.dumps({'schema': quarterfinals.SCHEMA, 'routes': {'1': {'artifacts': artifacts}}}))
        fixtures = root / 'fixtures.json'
        fixtures.write_text(json.dumps([fixture]))
        return index, fixtures, fixture

    def test_conflicting_completed_sources_reproduce_old_acceptance_and_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            row, _ = self.detail(root)
            self.assertEqual(evidence.verify_row(row)['home_90'], 1)
            with self.assertRaisesRegex(ValueError, 'listing/detail'):
                safe_evidence.verify_row(row)

    def test_listing_status_and_unknown_completion_cannot_borrow_detail_labels(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for listing in ('2:1 AfE', '2:1 Postp.', 'Cancelled'):
                row, _ = self.detail(root, listing=listing, shown='2:1',
                                     status='After Penalties', partial='(1:0, 0:1, 4:3)')
                self.assertEqual(evidence.verify_row(row)['home_90'], 1)
                with self.subTest(listing=listing), self.assertRaises(ValueError):
                    safe_evidence.verify_row(row)

    def test_valid_aet_afe_and_penalty_sources_keep_regulation_result(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for label in ('3:1 AET', '3:1 AfE'):
                row, _ = self.detail(root, listing=label)
                self.assertEqual(safe_evidence.verify_row(row), evidence.verify_row(row))
            row, _ = self.detail(root, listing='2:1 AfP', shown='2:1',
                                 status='After Penalties', partial='(1:0, 0:1, 4:3)')
            self.assertEqual(safe_evidence.verify_row(row)['home_90'], 1)

    def test_invalid_build_and_training_validate_before_output_or_trainer(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _, manifest = self.detail(root)
            with self.assertRaises(ValueError):
                safe_cli.build(manifest, root / 'rejected.sqlite')
            self.assertFalse((root / 'rejected.sqlite').exists())
            database.build(manifest, root / 'legacy.sqlite')
            with patch.object(model, 'train') as trainer, self.assertRaises(ValueError):
                safe_cli.train(root / 'legacy.sqlite', root / 'models')
            trainer.assert_not_called()
            self.assertFalse((root / 'models').exists())

    def test_valid_build_is_exclusive_and_training_records_guard(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source, _ = cup_cases.CupTests().capture(root)
            db = root / 'cup.sqlite'
            result = safe_cli.build({'sources': [source]}, db)
            self.assertEqual(result['execution_guard']['version'], safe_cli.GUARD_VERSION)
            before = db.read_bytes()
            with self.assertRaises(FileExistsError):
                safe_cli.build({'sources': [source]}, db)
            self.assertEqual(db.read_bytes(), before)
            # One verified row cannot meet the unchanged model's training minimum.
            result = safe_cli.train(db, root / 'models')
            self.assertEqual(result['routes'], {})
            self.assertEqual(safe_cli.read(root / 'models' / 'execution-guard.json')['strict_verified_rows'], 1)

    def test_prediction_keeps_matrix_and_adds_guard_and_unseen_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            path, fixture = self.edition_artifact(root)
            before = path.read_bytes()
            original = model.predict(path, fixture)
            result = safe_cli.predict(path, fixture)
            self.assertEqual(result['matrix'], original['matrix'])
            self.assertEqual(result['unseen_teams_using_fitted_prior'], ['a', 'b'])
            self.assertEqual(result['execution_guard']['artifact_sha256'], evidence.sha(path))
            self.assertEqual(path.read_bytes(), before)
            for label in [*safe_evidence.LABEL_FIELDS, 'HomeScore', 'awayGoals90', 'home-score90']:
                with self.subTest(label=label), self.assertRaisesRegex(ValueError, 'target labels'):
                    safe_cli.predict(path, {**fixture, label: 0})

    def test_late_batch_preflight_failure_writes_no_scope_or_predictions(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            index, fixtures, first = self.qf_inputs(root)
            second = copy.deepcopy(first)
            second['match_id'] = 'jleague:111102'
            original_raw = Path(first['phase_evidence']['raw_path'])
            raw = root / 'second.html'
            raw.write_text(original_raw.read_text().replace('111101/', '111102/'))
            second['phase_evidence'].update(raw_path=str(raw), raw_sha256=evidence.sha(raw),
                source_url=first['phase_evidence']['source_url'].replace('111101/', '111102/'))
            second['first_leg_record'] = {'competition': 'jpn.league_cup'}
            fixtures.write_text(json.dumps([first, second]))
            with patch('research.analysis_freeze.save') as save, self.assertRaises(KeyError):
                safe_cli.freeze_pairs(index, fixtures, root / 'out', root / 'freezes')
            save.assert_not_called()
            self.assertFalse((root / 'out').exists())
            self.assertFalse((root / 'freezes').exists())

    def test_index_role_and_leg_mismatch_fail_before_freezing(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            index, fixtures, _ = self.qf_inputs(root)
            original = safe_cli.read(index)
            for field, value in [('role', 'test'), ('leg', 2)]:
                ref = original['routes']['1']['artifacts']['current']
                path = Path(ref['path'])
                artifact = safe_cli.read(path)
                artifact[field] = value
                path.write_text(json.dumps(artifact))
                ref['sha256'] = evidence.sha(path)
                index.write_text(json.dumps(original))
                with patch('research.analysis_freeze.save') as save, self.assertRaises(ValueError):
                    safe_cli.freeze_pairs(index, fixtures, root / 'out', root / 'freezes')
                save.assert_not_called()
                self.assertFalse((root / 'out').exists())
                artifact[field] = 'current' if field == 'role' else 1
                path.write_text(json.dumps(artifact))

    def test_valid_qf_pair_keeps_old_prediction_and_freezes_only_in_temp(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            index, fixtures, fixture = self.qf_inputs(root)
            ref = safe_cli.read(index)['routes']['1']['artifacts']['test']
            old = quarterfinals.predict(ref['path'], fixture)
            safe = safe_cli.predict(ref['path'], fixture)
            self.assertEqual(old['matrix'], safe['matrix'])
            result = safe_cli.freeze_pairs(index, fixtures, root / 'out', root / 'freezes')
            self.assertEqual(len(result['receipts']), 1)
            record = safe_cli.read(result['receipts'][0]['path'])
            self.assertEqual(record['payload']['test']['matrix'], old['matrix'])
            self.assertEqual(record['payload']['execution_guard']['version'], safe_cli.GUARD_VERSION)
            self.assertFalse(record['payload']['test']['formal_kpi_eligible'])

    def test_batch_input_or_source_change_after_prediction_is_rejected_before_save(self):
        for changed in ('index', 'raw'):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                index, fixtures, fixture = self.qf_inputs(root)
                original = safe_cli.predict
                calls = []
                def changed_after_compute(path, value):
                    result = original(path, value)
                    calls.append(path)
                    if len(calls) == 2:
                        target = index if changed == 'index' else Path(fixture['phase_evidence']['raw_path'])
                        target.write_text(target.read_text() + ' ')
                    return result
                with patch.object(safe_cli, 'predict', side_effect=changed_after_compute), \
                        patch('research.analysis_freeze.save') as save, self.assertRaises(ValueError):
                    safe_cli.freeze_pairs(index, fixtures, root / 'out', root / 'freezes')
                save.assert_not_called()
                self.assertFalse((root / 'out').exists())

    def test_fixture_serialization_and_existing_credential_rule_preflight_before_save(self):
        for context in ({'measurement': float('nan')}, {'password': 'synthetic-only'}):
            with self.subTest(context=context), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                index, fixtures, fixture = self.qf_inputs(root)
                fixture['context'] = context
                fixtures.write_text(json.dumps([fixture]))
                with patch('research.analysis_freeze.save') as save, self.assertRaises(ValueError):
                    safe_cli.freeze_pairs(index, fixtures, root / 'out', root / 'freezes')
                save.assert_not_called()
                self.assertFalse((root / 'out').exists())

    def test_missing_first_leg_is_validated_then_deferred_without_predictions(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            index, fixtures, fixture = self.qf_inputs(root)
            fixture['leg'] = 2
            raw = Path(fixture['phase_evidence']['raw_path'])
            raw.write_text(raw.read_text().replace('第1戦', '第2戦'))
            fixture['phase_evidence']['raw_sha256'] = evidence.sha(raw)
            fixtures.write_text(json.dumps([fixture]))
            value = safe_cli.read(index)
            entry = value['routes'].pop('1')
            value['routes']['2'] = entry
            for ref in entry['artifacts'].values():
                path = Path(ref['path'])
                artifact = safe_cli.read(path)
                artifact['leg'] = 2
                path.write_text(json.dumps(artifact))
                ref['sha256'] = evidence.sha(path)
            index.write_text(json.dumps(value))
            with patch.object(safe_cli, 'predict') as predict:
                result = safe_cli.freeze_pairs(index, fixtures, root / 'out', root / 'freezes')
            predict.assert_not_called()
            self.assertEqual(result['receipts'], [])
            self.assertEqual(len(result['deferred']), 1)


if __name__ == '__main__':
    unittest.main()
