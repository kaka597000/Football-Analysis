import unittest

import numpy as np

from research_program import j1_local_score_calibration as model
from research_program.all_scope_model import SIZE


class J1LocalScoreCalibrationTests(unittest.TestCase):
    def test_quarter_line_training_and_projection_preserve_settlement_groups(self):
        prior = np.zeros((SIZE, SIZE))
        prior[2, 1], prior[3, 0], prior[2, 0], prior[1, 2], prior[1, 1] = .2, .1, .3, .1, .3
        game = dict(id='test-j1', home_goals=2, away_goals=1, total=dict(line=2.25))

        observed, expected = model.training_vectors(prior[None], [game])
        cell_21 = model.calibration.CELL[2 * SIZE + 1]
        cell_30 = model.calibration.CELL[3 * SIZE]
        self.assertEqual(observed[0, cell_21], 1.)
        self.assertEqual(np.count_nonzero(observed), 1)
        self.assertEqual(np.count_nonzero(expected), 2)
        np.testing.assert_allclose(expected[0, [cell_21, cell_30]], [2. / 3., 1. / 3.])

        offsets = np.zeros(model.BUCKETS)
        offsets[cell_21] = np.log(1.5)
        offsets[model.calibration.CELL[2 * SIZE]] = np.log(1.4)
        changed, error = model.adjusted(prior, offsets, game['total']['line'])
        desired = prior.copy()
        desired[2, 1], desired[3, 0] = .225, .075
        np.testing.assert_allclose(changed, desired, rtol=0., atol=1e-15)
        self.assertLessEqual(error, 1e-15)


if __name__ == '__main__':
    unittest.main()
