"""Checks specific to the isolated total-goal calibration."""
import unittest
import numpy as np

from replay_frozen_goal_totals import adjust, fit, grade, picks


class TotalCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.matrix = np.array([[.20, .25, 0.], [.25, .18, .04], [0., .04, .04]])

    def test_no_evidence_keeps_exact_distribution_and_home_first_ties(self):
        self.assertEqual(fit([], 50, .35), 0.)
        m = adjust(self.matrix, 0.)
        np.testing.assert_array_equal(m, self.matrix)
        self.assertEqual(picks(m), ['0-1', '1-0', '0-0'])

    def test_tilt_preserves_support_and_odds_within_each_total(self):
        m = adjust(self.matrix, .1)
        self.assertAlmostEqual(m.sum(), 1.)
        np.testing.assert_array_equal(m == 0., self.matrix == 0.)
        self.assertAlmostEqual(m[0, 1] / m[1, 0], self.matrix[0, 1] / self.matrix[1, 0])
        self.assertAlmostEqual((m[2, 2] / m[0, 0]) / (.04 / .20), np.exp(.4))
        score = grade(m, picks(m), [2, 2])
        self.assertAlmostEqual(score['p_four_plus'], m[2, 2])
        self.assertAlmostEqual(score['score_log_loss'], -np.log(m[2, 2]))

    def test_fit_is_signed_bounded_and_minimizes_penalized_exact_score_loss(self):
        rows = [(self.matrix, total) for total in (2, 3, 4)]
        beta = fit(rows, 50, .35)
        self.assertGreater(beta, 0.)
        g = np.indices(self.matrix.shape).sum(axis=0)
        derivative = sum((adjust(m, beta) * g).sum() - total for m, total in rows) + 50 * beta
        self.assertAlmostEqual(float(derivative), 0., places=8)
        for actual, expected in [(0, -.35), (4, .35)]:
            self.assertEqual(fit([(self.matrix, actual)] * 100, 50, .35), expected)


if __name__ == '__main__':
    unittest.main()
