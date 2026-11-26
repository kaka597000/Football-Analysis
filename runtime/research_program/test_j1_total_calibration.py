import unittest

import numpy as np

from research_program import j1_total_calibration as model


class TotalGoalsCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.prior = np.zeros(model.base.H.shape)
        self.prior[0, 0] = .15
        self.prior[1, 1] = .40
        self.prior[2, 0] = .20
        self.prior[2, 1] = .25

    def test_tilt_preserves_score_ratios_within_each_total(self):
        for dose in (.5, 1.):
            adjusted = model.adjust(self.prior, np.array([1., 0., 0.]), [.15, 0., 0.], dose)
            self.assertAlmostEqual(adjusted[1, 1] / adjusted[2, 0], 2.)
            self.assertAlmostEqual(adjusted.sum(), 1.)
            self.assertGreater(adjusted[2, 1], self.prior[2, 1])
            np.testing.assert_array_equal(adjusted[self.prior == 0], 0.)
        self.assertIs(model.adjust(self.prior, np.ones(3), [.15, 0., 0.], 0.), self.prior)

    def test_same_day_outcomes_and_previous_season_stay_out_of_features(self):
        rows = [dict(date=stamp, season=season, stage='regular', home='A', away=away, home_goals=4, away_goals=1)
                for stamp, season, away in [('2025-05-01','2025','B'), ('2025-05-01','2025','C'),
                                            ('2025-05-02','2025','B'), ('2026-09-01','2026-27','B')]]
        x, exposure, _ = model.history(rows, [self.prior] * len(rows))
        np.testing.assert_array_equal(x[[0, 1, 3]], np.tile([1., 0., 0.], (3, 1)))
        self.assertGreater(x[2, 1], 0.)
        self.assertGreater(x[2, 2], 0.)
        self.assertEqual(exposure[1], [0., 0.])
        self.assertTrue(all(value > 0 for value in exposure[2]))

    def test_likelihood_moves_mass_toward_high_observed_totals(self):
        scores = np.tile([2, 1], (20, 1))
        beta = model.fit([self.prior] * len(scores), np.tile([1., 0., 0.], (len(scores), 1)), scores)
        self.assertGreater(beta[0], 0.)
        self.assertEqual(beta[1:], [0., 0.])
