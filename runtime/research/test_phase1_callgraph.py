"""The Phase-1 call graph must keep matching the live entry source."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'runtime' / 'research' / 'phase1_callgraph.py'


class Phase1CallGraphTest(unittest.TestCase):
    def test_live_edges_still_exist_and_champion_path_is_absent(self):
        completed = subprocess.run([sys.executable, str(SCRIPT)], check=True,
                                   capture_output=True, text=True)
        payload = json.loads(completed.stdout)
        calls = {(row['file'], row['calls']) for row in payload['edges']}
        self.assertIn(('runtime/prediction_engine.py', 'legacy.predict'), calls)
        self.assertIn(('runtime/v6/ml_model.py', 'ml_matrix'), calls)
        self.assertEqual(payload['fusion']['alpha_grid'], [0.25, 0.5, 0.75, 1.0])
        self.assertIn('egidi', payload['absent_from_predict_active_run'])
        self.assertIn('learn_weights', payload['absent_from_predict_active_run'])
        self.assertTrue(all(row['status'] in {'LIVE', 'PARTIAL', 'DEAD'} for row in payload['edges']))


if __name__ == '__main__':
    unittest.main()
