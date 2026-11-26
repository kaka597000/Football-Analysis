import unittest

import numpy as np

from research_program import europe_three_goal_coverage as coverage


class ThreeGoalCoverageTests(unittest.TestCase):
    def setUp(self):
        self.grid = np.full(coverage.TOTAL.shape, 1e-6)
        for index, mass in {
            (1, 1): .13, (1, 0): .12, (0, 1): .11, (0, 0): .09,
            (2, 1): .07, (3, 0): .065, (3, 1): .055, (4, 0): .045, (3, 2): .038,
            (2, 0): .031, (2, 2): .036, (1, 2): .045, (0, 3): .04,
        }.items():
            self.grid[index] = mass
        self.grid /= self.grid.sum()
        self.first = ['1-1', '1-0', '0-1']

    def test_calibration_preserves_all_outcome_masses_and_within_group_ratios(self):
        shifted = coverage.adjust(self.grid, np.array([.4, -.2, .4]))
        before, after = map(coverage.groups, (self.grid, shifted))
        np.testing.assert_allclose(after.sum(axis=1), before.sum(axis=1), rtol=0, atol=2e-12)
        self.assertGreater(after[0, 1], before[0, 1])
        self.assertLess(after[1, 1], before[1, 1])
        for group in range(6):
            mask = coverage.BUCKETS == group
            ratios = shifted[mask] / self.grid[mask]
            np.testing.assert_allclose(ratios, ratios[0], rtol=0, atol=2e-12)

    def test_third_only_preserves_first_two_and_needs_a_supported_new_path(self):
        self.assertEqual(coverage.high_third(self.grid, self.grid, self.first), ['1-1', '1-0', '2-1'])
        shifted = coverage.adjust(self.grid, np.array([-1., 0., -1.]))
        self.assertEqual(coverage.high_third(self.grid, shifted, self.first), self.first)
        covered = ['1-1', '1-0', '2-1']
        self.assertEqual(coverage.high_third(self.grid, self.grid, covered), covered)

    def test_fit_uses_a_shared_win_offset_and_a_separate_draw_offset(self):
        labels = [(1, 0), (0, 1), (2, 2)] * 8
        win, draw, away = coverage.fit([self.grid] * len(labels), labels)
        self.assertLess(win, 0.)
        self.assertGreater(draw, 0.)
        self.assertEqual(win, away)


if __name__ == '__main__':
    unittest.main()
