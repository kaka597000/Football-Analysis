import unittest

import numpy as np

from research_program import j1_four_goal_path as study


class FourGoalPathTests(unittest.TestCase):
    def setUp(self):
        self.prior = np.zeros_like(study.TOTAL, dtype=float)
        for home, away, p in ((1, 1, .19), (2, 1, .17), (1, 0, .16), (3, 1, .151),
                              (2, 2, .09), (4, 0, .065), (0, 1, .12), (0, 0, .054)):
            self.prior[home, away] = p

    def test_change_high_goal_mass_within_same_quarter_line_settlement(self):
        group = study.partitions.partitions(2.25)
        shifted = study.adjust(self.prior, np.array([[.5, .2], [.4, 0.]]), .3, 2.25)
        self.assertGreater(shifted[3, 1], self.prior[3, 1])
        self.assertLess(shifted[2, 1], self.prior[2, 1])
        np.testing.assert_allclose(np.bincount(group, weights=shifted.ravel()),
                                   np.bincount(group, weights=self.prior.ravel()), rtol=0., atol=1e-15)

    def test_guard_uses_highest_four_plus_score_only_within_budget(self):
        original = dict(primary='1-1', top3=['1-1', '2-1', '1-0'])
        offered, cost = study.guarded_third(self.prior, original)
        self.assertEqual(offered['top3'], ['1-1', '2-1', '3-1'])
        self.assertAlmostEqual(cost, .009)
        less_certain = self.prior.copy()
        less_certain[3, 1] -= .02
        less_certain[2, 2] += .02
        retained, cost = study.guarded_third(less_certain, original)
        self.assertIs(retained, original)
        self.assertGreater(cost, .015)
