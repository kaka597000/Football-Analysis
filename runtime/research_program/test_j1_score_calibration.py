import unittest

import numpy as np

from research_program import j1_score_calibration as calibration
from research_program.all_scope_model import SIZE
from research_program.ucl_zero_goals import totals


class J1ScoreCalibrationTests(unittest.TestCase):
    def test_projection_changes_cells_and_preserves_both_marginals(self):
        prior = np.zeros((SIZE, SIZE))
        for home, away, mass in [(0, 0, .1), (1, 1, .2), (2, 2, .1), (2, 0, .1),
                                 (0, 2, .1), (2, 1, .1), (1, 2, .1), (4, 0, .1), (0, 4, .1)]:
            prior[home, away] = mass
        beta = [-.4, .2, .3]
        full = calibration.adjust(prior, beta, 1.)
        half = calibration.adjust(prior, beta, .5)
        self.assertIs(calibration.adjust(prior, beta, 0.), prior)
        self.assertLess(full[1, 1], prior[1, 1])
        np.testing.assert_array_equal(full == 0., prior == 0.)
        np.testing.assert_allclose(half, .5 * (full + prior), atol=1e-15)
        np.testing.assert_allclose(totals(full), totals(prior), rtol=0., atol=3e-13)
        bucket = calibration.OUTCOME
        np.testing.assert_allclose(np.bincount(bucket, weights=full.ravel()),
                                   np.bincount(bucket, weights=prior.ravel()),
                                   rtol=0., atol=3e-13)

    def test_structural_zero_outcome_retains_mass_and_stays_finite(self):
        prior = np.zeros((SIZE, SIZE))
        prior[3, 0] = .55
        prior[2, 1] = .45
        changed = calibration.adjust(prior, [0., -.2, .4], 1.)
        self.assertGreater(changed[2, 1], changed[3, 0])
        self.assertAlmostEqual(changed.sum(), 1.)
        self.assertEqual(np.count_nonzero(changed), 2)


if __name__ == '__main__':
    unittest.main()
