import copy
import importlib.util
import json
import unittest

import numpy as np

from research_program import transformer_score as model


def sample_weights():
    rng = np.random.default_rng(7)
    width, feed = model.CONFIG['d_model'], model.CONFIG['feedforward']
    weights = {'positions': rng.normal(size=(model.WINDOW, width)) / 3}
    for name, fan_in, fan_out in [
        ('input_projection', 8, width), ('encoder.self_attn.out_proj', width, width),
        ('encoder.linear1', width, feed), ('encoder.linear2', feed, width), ('readout', width + 3, 1),
    ]:
        weights[name + '.weight'] = rng.normal(size=(fan_out, fan_in)) / np.sqrt(fan_in)
        weights[name + '.bias'] = np.zeros(fan_out)
    weights['encoder.self_attn.in_proj_weight'] = rng.normal(size=(3 * width, width)) / np.sqrt(width)
    weights['encoder.self_attn.in_proj_bias'] = np.zeros(3 * width)
    for name in ('encoder.norm1', 'encoder.norm2'):
        weights[name + '.weight'] = np.ones(width)
        weights[name + '.bias'] = np.zeros(width)
    return weights


class TransformerTests(unittest.TestCase):
    def setUp(self):
        self.weights = sample_weights()
        self.artifact = {'version': model.VERSION, 'max_abs_tilt': model.MAX_TILT, 'weights': self.weights}
        self.matrix = np.array([[.1, .15, 0.], [.3, .2, .25]])
        self.sequence = np.zeros((2, 5, 4))
        self.sequence[0, 3:] = [[0, 2, 1, 1], [1, 2, 0, 1]]
        self.sequence[1, -1] = [2, 0, 1, 1]

    def test_mask_track_preserves_one_team_history_and_excludes_padded_keys(self):
        panels, context, _ = model._features(self.sequence, self.matrix)
        reference = model._forward(self.weights, panels, context)
        changed = copy.deepcopy(self.weights)
        changed['positions'][:3] += 100
        np.testing.assert_array_equal(model._forward(changed, panels, context), reference)
        changed['positions'][3] += 1
        changed['positions'][3, 0] += 1
        self.assertFalse(np.allclose(model._forward(changed, panels, context), reference))
        empty = np.zeros_like(self.sequence)
        _, empty_context, _ = model._features(empty, self.matrix)
        static_only = empty_context @ self.weights['readout.weight'][0, -3:] + self.weights['readout.bias']
        np.testing.assert_allclose(model.residuals(self.artifact, self.matrix, empty), static_only)

    def test_registered_log_tilt_preserves_support_and_normalization(self):
        self.weights['readout.weight'][:] = 0
        self.weights['readout.bias'][:] = 0
        np.testing.assert_array_equal(model.apply(self.artifact, self.matrix, self.sequence), self.matrix)
        self.weights['readout.weight'][0, -3] = 100.
        expected = self.matrix * np.exp(np.indices(self.matrix.shape)[0] * model.MAX_TILT)
        result = model.apply(self.artifact, self.matrix, self.sequence)
        np.testing.assert_allclose(result, expected / expected.sum())
        self.assertEqual(result[0, 2], 0.)

    def test_invalid_or_unavailable_result_does_not_enter_training(self):
        row = {'match_id': 'm', 'game_date': '2026-03-31', 'cutoff_at': '2026-03-31T19:00Z',
               'kickoff_at': '2026-03-31T20:00Z', 'observed_at': '2026-04-01T15:00Z'}
        with self.assertRaisesRegex(ValueError, 'no training fixtures'):
            model.fit([row], cutoff_at='2026-04-01T12:00Z')
        with self.assertRaisesRegex(ValueError, 'chronology'):
            model.fit([dict(row, kickoff_at='2026-03-31T02:00Z')], cutoff_at='2026-04-01T12:00Z')

    @unittest.skipUnless(importlib.util.find_spec('torch'), 'optional training stack')
    def test_pytorch_fit_reproduces_full_mask_and_partial_histories_offline(self):
        rows = [{'match_id': str(i), 'game_date': '2025-05-12', 'baseline_matrix': self.matrix,
                 'sequence': self.sequence * int(i % 4 != 0), 'home_goals': i % 3, 'away_goals': i % 2}
                for i in range(20)]
        later = dict(rows[0], game_date='2025-08-01')
        artifact = model.fit(rows + [later], cutoff_at='2025-08-01T12:00Z')
        self.assertEqual(artifact['training_matches'], 20)
        self.assertLess(artifact['max_native_replay_error'], 1e-10)
        restored = json.loads(json.dumps(artifact, allow_nan=False))
        for seq in (self.sequence, 0 * self.sequence):
            result = model.apply(restored, self.matrix, seq)
            self.assertAlmostEqual(result.sum(), 1.)
            self.assertFalse(np.allclose(result, self.matrix))


if __name__ == '__main__':
    unittest.main()
