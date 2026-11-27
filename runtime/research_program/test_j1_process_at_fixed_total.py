import unittest

import numpy as np

from research_program.j1_process_at_fixed_total import match_goal_totals


class FixedTotalProcessTests(unittest.TestCase):
    def test_exact_total_distribution_and_new_within_total_ratios_are_both_preserved(self):
        prior = np.array([[.20, .20, .10], [.10, .20, .10], [.05, .04, .01]])
        home, away = np.indices(prior.shape)
        shaped = prior * np.exp(.2 * (home - away))
        shaped /= shaped.sum()
        matrix, error = match_goal_totals(prior, shaped)
        self.assertLessEqual(error, 1e-15)
        self.assertGreater(matrix[1, 0], prior[1, 0])
        self.assertAlmostEqual(matrix[1, 0] / matrix[0, 1], shaped[1, 0] / shaped[0, 1])
        self.assertAlmostEqual(matrix.sum(), prior.sum())
        self.assertAlmostEqual(matrix[0, 0], prior[0, 0])
