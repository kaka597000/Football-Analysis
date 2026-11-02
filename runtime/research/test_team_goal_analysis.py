import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np

from team_goal_analysis import (CALIBRATION, attach_pair, review, summarize_matrix,
                                summarize_prediction)


class TeamGoalTests(unittest.TestCase):
    def test_asymmetric_axes_means_marginals_and_key_probabilities(self):
        matrix = [[.1, .2, .1], [.05, .25, .3]]
        original = copy.deepcopy(matrix)
        summary = summarize_matrix(matrix)
        self.assertEqual(matrix, original)
        self.assertEqual(summary['matrix_axes'], {'home_goals': 0, 'away_goals': 1})
        self.assertEqual(summary['matrix_shape'], [2, 3])
        self.assertEqual(summary['calibration_status'], CALIBRATION)
        np.testing.assert_allclose(summary['home']['marginal'], [.4, .6])
        np.testing.assert_allclose(summary['away']['marginal'], [.15, .45, .4])
        self.assertAlmostEqual(summary['home']['mean'], .6)
        self.assertAlmostEqual(summary['away']['mean'], 1.25)
        self.assertAlmostEqual(summary['home']['p0'], .4)
        self.assertEqual(summary['home']['p_ge_2'], 0)
        self.assertAlmostEqual(summary['away']['p_ge_2'], .4)
        self.assertEqual(summary['away']['p_ge_3'], 0)
        canonical = json.dumps(matrix, sort_keys=True, separators=(',', ':')).encode()
        self.assertEqual(summary['source_matrix_sha256'], hashlib.sha256(canonical).hexdigest())

    def test_discrete_equal_tail_interval_has_actual_model_mass(self):
        summary = summarize_matrix([[.1, .7, .1, .1]])
        interval = summary['away']['central_interval']
        self.assertEqual((interval['lower'], interval['upper']), (0, 2))
        self.assertEqual(interval['nominal_coverage'], .8)
        self.assertAlmostEqual(interval['matrix_coverage'], .9)
        self.assertEqual(interval['width'], 2)
        # A degenerate distribution and leading zero mass have deterministic ties.
        for coverage in (.8, .5, 1 - 1e-15):
            point = summarize_matrix([[0, 0, 1]], coverage)['away']['central_interval']
            self.assertEqual((point['lower'], point['upper']), (2, 2))
            self.assertEqual(point['matrix_coverage'], 1)
        interior = summarize_matrix([[.05, .15, .6, .15, .05]])['away']
        self.assertEqual(interior['central_interval']['lower'], 1)
        self.assertEqual(interior['central_interval']['upper'], 3)
        self.assertAlmostEqual(interior['central_interval']['matrix_coverage'], .9)
        self.assertAlmostEqual(interior['p_ge_3'], .2)
        narrower = summarize_matrix([[.05, .15, .6, .15, .05]], coverage=.5)['away']
        self.assertEqual((narrower['central_interval']['lower'],
                          narrower['central_interval']['upper']), (2, 2))
        self.assertAlmostEqual(narrower['central_interval']['matrix_coverage'], .6)

    def test_invalid_matrices_and_coverage_are_rejected(self):
        invalid = [[], [[]], [1], 1, [[.5], [.5, 0]], [[[1]]], [[-.1, 1.1]],
                   [[float('nan')]], [[float('inf')]], [[0]], [[2]], [[.5]],
                   [['1']], [[True]], [[1e308, 1e308]], [[1 + 0j]]]
        for matrix in invalid:
            with self.subTest(matrix=matrix), self.assertRaises(ValueError):
                summarize_matrix(matrix)
        for coverage in (0, 1, -.1, 1.1, float('nan'), float('inf'), '0.8', True):
            with self.subTest(coverage=coverage), self.assertRaises(ValueError):
                summarize_matrix([[1]], coverage)
        # Within floating-point tolerance is accepted but never rescaled.
        matrix = [[1 - 1e-10]]
        summary = summarize_matrix(matrix)
        self.assertEqual(summary['home']['p0'], matrix[0][0])
        self.assertEqual(summary['matrix_mass'], matrix[0][0])

    def test_unavailable_abstain_observe_and_hash_validation(self):
        for prediction in ({}, {'matrix': None}, {'status': 'ABSTAIN'},
                           {'status': 'ABSTAIN_NO_DATA', 'matrix': [[1]]}):
            result = summarize_prediction(prediction)
            self.assertEqual(result['status'], 'UNAVAILABLE')
            self.assertNotIn('home', result)
            self.assertNotIn('source_matrix_sha256', result)
        observed = summarize_prediction({'status': 'OBSERVE', 'matrix': [[1]]})
        self.assertEqual(observed['status'], 'AVAILABLE')
        self.assertEqual(summarize_prediction({'matrix': [[1]],
                                              'matrix_hash': observed['source_matrix_sha256']}),
                         observed)
        with self.assertRaises(ValueError):
            summarize_prediction({'matrix': [[1]], 'matrix_hash': 'incorrect'})

    def test_review_separates_teams_errors_interval_hit_and_width(self):
        summary = summarize_matrix([[.05, .15, .6, .15, .05]])
        result = review(summary, home_goals=0, away_goals=4)
        self.assertEqual(result['home']['mean_error'], 0)
        self.assertTrue(result['home']['interval_hit'])
        self.assertEqual(result['home']['interval_width'], 0)
        self.assertAlmostEqual(result['away']['mean_error'], -2)
        self.assertAlmostEqual(result['away']['absolute_error'], 2)
        self.assertAlmostEqual(result['away']['squared_error'], 4)
        self.assertFalse(result['away']['interval_hit'])
        self.assertEqual(result['away']['interval_width'], 2)
        for actual in (-1, 1.5, True):
            with self.assertRaises(ValueError):
                review(summary, home_goals=actual, away_goals=1)
        self.assertEqual(review(summarize_prediction({}), home_goals=0, away_goals=0)
                         ['status'], 'UNAVAILABLE')
        self.assertNotIn('top1', result)

    def test_pair_is_independent_and_invalid_pair_cannot_partly_attach(self):
        pair = {'current_model': {'matrix': [[1]], 'primary': '0-0'},
                'test_model': {'matrix': [[0], [1]], 'primary': '1-0'}}
        before = copy.deepcopy(pair)
        attach_pair(pair)
        for key in before:
            unchanged = dict(pair[key]); unchanged.pop('team_goal_analysis')
            self.assertEqual(unchanged, before[key])
        self.assertIsNot(pair['current_model']['team_goal_analysis'],
                         pair['test_model']['team_goal_analysis'])
        bad = copy.deepcopy(before); bad['test_model']['matrix'] = [[2]]
        with self.assertRaises(ValueError):
            attach_pair(bad)
        self.assertNotIn('team_goal_analysis', bad['current_model'])

    def test_sidecar_cli_never_rewrites_source_or_existing_output(self):
        script = str(Path(__file__).with_name('team_goal_analysis.py'))
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / 'frozen-research-candidate.json'
            source.write_text(json.dumps({'status': 'PREDICTED', 'matrix': [[0, 1]],
                                          'primary': [0, 1]}))
            raw = source.read_bytes()
            target = folder / 'new-summary.json'
            command = [sys.executable, script, str(source), '--out', str(target)]
            subprocess.run(command, check=True, capture_output=True, text=True)
            summary = json.loads(target.read_text())
            self.assertEqual(summary['team_goal_analysis']['away']['mean'], 1)
            self.assertEqual(summary['source_file_sha256'], hashlib.sha256(raw).hexdigest())
            self.assertEqual(source.read_bytes(), raw)
            issued = target.read_bytes()
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
            same = [sys.executable, script, str(source), '--out', str(source)]
            self.assertNotEqual(subprocess.run(same, capture_output=True).returncode, 0)
            self.assertEqual(target.read_bytes(), issued)
            self.assertEqual(source.read_bytes(), raw)


if __name__ == '__main__':
    unittest.main()
