import importlib.util
import unittest

import numpy as np

from research_program import sequence_models as model


class SequenceModelTests(unittest.TestCase):
    def setUp(self):
        self.s = np.zeros((2, 5, 4))
        self.s[0, 4] = [3, 1, 1, 1]
        self.s[1, 4] = [0, 2, 0, 1]
        self.m = np.array([[.05, .10, .20], [.30, .25, .10]])
        self.lstm = {'gate_weights': np.zeros((9, 4)).tolist(), 'gate_biases': [0.] * 4,
                     'output_weights': [0., 1., 0., 0.], 'output_bias': 0.}
        self.artifact = {'version': model.VERSION, 'max_abs_tilt': model.MAX_TILT,
                         'lstm': self.lstm, 'lightgbm': {'trees': [0.]}}

    def test_side_orientation_shared_scaling_and_observed_padding(self):
        panel, context, mu = model._features(self.s, self.m)
        np.testing.assert_array_equal(panel[0, :, :4], panel[1, :, 4:])
        self.assertEqual(tuple(panel[0, 4]), (1., 1/3, 1., 1., 0., 2/3, 0., 1.))
        np.testing.assert_allclose(mu, [.65, .95])
        np.testing.assert_array_equal(context[:, 0], [1., 0.])
        self.s[1, 0, 0] = 1
        with self.assertRaisesRegex(ValueError, 'padding'):
            model._features(self.s, self.m)

    def test_residual_after_quote_preserves_support_identity_and_higher_order_interactions(self):
        self.m[0, 1] = 0.
        self.m[0, 0] += .1
        arms = model.apply(self.artifact, self.m, self.s)
        np.testing.assert_array_equal(arms['lightgbm'], self.m)
        np.testing.assert_array_equal(arms['baseline'], self.m)
        tilted = arms['lstm']
        self.assertEqual(tilted[0, 1], 0.)
        self.assertAlmostEqual(tilted.sum(), 1.)
        self.assertAlmostEqual(tilted[1, 0] / tilted[0, 0], np.exp(.35) * self.m[1, 0] / self.m[0, 0])
        self.assertAlmostEqual(tilted[1, 2] / tilted[1, 0], self.m[1, 2] / self.m[1, 0])

    def test_later_observations_excluded_and_original_times_reconciled(self):
        row = {'game_date': '2026-10-31', 'cutoff_at': '2026-10-31T02:00Z',
               'kickoff_at': '2026-10-31T03:00Z', 'observed_at': '2026-11-01T05:00Z', 'match_id': '1'}
        with self.assertRaisesRegex(ValueError, 'no training fixtures'):
            model.fit([row], cutoff_at='2026-11-01T00:00Z')
        with self.assertRaisesRegex(ValueError, 'chronology'):
            model.fit([dict(row, kickoff_at='2026-10-31T01:00Z')], cutoff_at='2026-11-01T00:00Z')

    @unittest.skipUnless(all(importlib.util.find_spec(p) for p in ('torch', 'lightgbm')), 'optional training stack')
    def test_real_training_exports_both_native_predictions_and_excludes_cutoff_day(self):
        rng = np.random.default_rng(51)
        rows = []
        for i in range(140):
            seq = rng.integers(0, 4, size=(2, 5, 4))
            seq[:, :, 2] %= 2
            seq[:, :, 3] = 1
            rows.append({'match_id': str(i), 'game_date': '2025-02-01', 'sequence': seq,
                         'baseline_matrix': self.m, 'home_goals': int(seq[0, 4, 0]), 'away_goals': int(seq[1, 4, 0])})
        artifact = model.fit(rows + [dict(rows[0], game_date='2026-01-01')], cutoff_at='2026-01-01T10:00Z')
        self.assertEqual(artifact['training_matches'], len(rows))
        self.assertGreater(len(artifact['lightgbm']['trees']), 1)
        self.assertEqual(len(artifact['lstm']['gate_biases']), 24)
        estimates = model.apply(artifact, self.m, rows[3]['sequence'])
        reversed_steps = model.apply(artifact, self.m, rows[3]['sequence'][:, ::-1])
        for key in ('lstm', 'lightgbm'):
            self.assertFalse(np.allclose(estimates[key], reversed_steps[key]))
            self.assertTrue(np.all(estimates[key] >= 0))
            self.assertAlmostEqual(estimates[key].sum(), 1.)
            self.assertFalse(np.array_equal(self.m, estimates[key]))
