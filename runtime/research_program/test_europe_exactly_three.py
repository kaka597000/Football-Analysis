import unittest

import numpy as np

from research_program import europe_exactly_three as exact_three
from research_program import europe_three_goal_coverage as replay
from research_program.score_form import poisson_matrix


class ExactlyThreeGoalsTests(unittest.TestCase):
    def setUp(self):
        self.grid = poisson_matrix((1.65, 1.25), replay.TOTAL.shape)

    def test_only_higher_total_winning_scores_move(self):
        unchanged = ~replay.HIGH | (replay.base.RESULT == 1)
        updated = exact_three.adjust(self.grid, np.array([.4, 0., .4]))
        np.testing.assert_array_equal(updated[unchanged], self.grid[unchanged])
        np.testing.assert_allclose(replay.groups(updated), replay.groups(self.grid), rtol=0., atol=2e-12)
        self.assertGreater(updated[exact_three.EXACT].sum(), self.grid[exact_three.EXACT].sum())
        self.assertLess(updated[exact_three.FOUR_PLUS].sum(), self.grid[exact_three.FOUR_PLUS].sum())

    def test_fit_ignores_draws_and_lower_total_scores(self):
        actual = [(1, 2), (3, 0), (2, 1), (1, 3)] * 4
        values = exact_three.fit([self.grid] * len(actual), actual)
        extended = actual + [(1, 0), (2, 2), (0, 0), (0, 1)] * 6
        np.testing.assert_array_equal(exact_three.fit([self.grid] * len(extended), extended), values)
        self.assertGreater(values[0], 0.)
        self.assertEqual((values[1], values[2]), (0., values[0]))


if __name__ == '__main__':
    unittest.main()
