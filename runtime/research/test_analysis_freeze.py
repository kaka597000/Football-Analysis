import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
import sys
from unittest.mock import patch

from analysis_freeze import save, verify


class FreezeTests(unittest.TestCase):
    def fixture(self):
        now = datetime.now(timezone.utc)
        return {'match_id': 'synthetic-test-only',
                'kickoff_at': (now + timedelta(hours=1)).isoformat(),
                'cutoff_at': (now - timedelta(minutes=1)).isoformat()}

    def test_revisions_preserve_original_and_separate_stages(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = self.fixture()
            first = save({'primary': '1-0'}, fixture, stage='prematch', track='comprehensive', root=root)
            original = Path(first['path']).read_bytes()
            second = save({'primary': '1-1'}, fixture, stage='prematch', track='comprehensive', root=root)
            self.assertEqual(Path(first['path']).read_bytes(), original)
            self.assertNotEqual(first['freeze_id'], second['freeze_id'])
            self.assertEqual(first['evaluation_slot'], 'FIRST_PREMATCH')
            self.assertNotEqual(second['evaluation_slot'], 'FIRST_PREMATCH')
            row = verify(json.loads(Path(second['path']).read_text()))
            self.assertEqual(row['previous_sha256'], first['sha256'])
            third = save({'status': 'ABSTAIN'}, fixture, stage='prematch', track='current-test-pair', root=root)
            self.assertEqual(third['evaluation_slot'], 'FIRST_PREMATCH')

    def test_timing_and_credentials_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = self.fixture()
            late = copy.deepcopy(fixture)
            late['kickoff_at'] = (datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()
            for item, stage, payload in [(late, 'prematch', {}), (fixture, 'live', {}),
                                         (fixture, 'prematch', {'api_key': 'synthetic'})]:
                with self.assertRaises(ValueError):
                    save(payload, item, stage=stage, track='test', root=root)
            saved = save({}, late, stage='live', track='test', root=root)
            self.assertNotEqual(saved['evaluation_slot'], 'FIRST_PREMATCH')

    def test_tamper_blocks_next_revision(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = self.fixture()
            receipt = save({}, fixture, stage='prematch', track='test', root=root)
            path = Path(receipt['path'])
            row = json.loads(path.read_text()); row['payload'] = {'primary': '9-9'}
            path.write_text(json.dumps(row))
            with self.assertRaises(ValueError):
                save({}, fixture, stage='prematch', track='test', root=root)

    def test_default_cli_saves_exact_pair_before_output(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import predict_active
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            fixture = self.fixture()
            source = folder / 'fixture.json'; source.write_text(json.dumps(fixture))
            target = folder / 'output.json'
            pair = {'match_id': fixture['match_id'],
                    'current_model': {'primary': '1-0', 'matrix': [[0, 0], [1, 0]]},
                    'test_model': {'primary': '0-0', 'matrix': [[1, 0], [0, 0]]}}
            arguments = ['predict_active.py', str(source), '--out', str(target),
                         '--freeze-root', str(folder / 'freezes')]
            with patch.object(predict_active, 'run', return_value=copy.deepcopy(pair)), \
                 patch.object(sys, 'argv', arguments), patch('builtins.print'):
                predict_active.main()
            output = json.loads(target.read_text())
            frozen = verify(json.loads(Path(output['freeze_receipt']['path']).read_text()))
            issued = {key: value for key, value in output.items() if key != 'freeze_receipt'}
            self.assertEqual(frozen['payload'], issued)
            for key in ('current_model', 'test_model'):
                actual = dict(output[key])
                summary = actual.pop('team_goal_analysis')
                audit = actual.pop('score_distribution_audit')
                self.assertEqual(actual, pair[key])
                self.assertEqual(audit['status'], 'AVAILABLE')
                self.assertEqual(summary['status'], 'AVAILABLE')
                self.assertEqual(summary, frozen['payload'][key]['team_goal_analysis'])
            self.assertEqual(output['current_model']['team_goal_analysis']['home']['mean'], 1)
            self.assertEqual(output['test_model']['team_goal_analysis']['home']['mean'], 0)
            self.assertNotEqual(output['current_model']['team_goal_analysis']['source_matrix_sha256'],
                                output['test_model']['team_goal_analysis']['source_matrix_sha256'])
            with patch.object(predict_active, 'run') as mocked, \
                 patch.object(sys, 'argv', arguments):
                with self.assertRaises(FileExistsError):
                    predict_active.main()
                mocked.assert_not_called()


if __name__ == '__main__':
    unittest.main()
