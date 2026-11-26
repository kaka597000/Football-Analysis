import unittest

import numpy as np

from research_program import europe_winning_margin as margins
from research_program.score_form import poisson_matrix


class WinningMarginTests(unittest.TestCase):
    def setUp(self):
        self.grid = poisson_matrix((1.8, 0.85), margins.MARGIN.shape)
        self.mass = margins.group_masses(self.grid)

    def test_only_shifts_mass_between_margin_groups_within_wins(self):
        adjusted = margins.adjust(self.grid, self.mass, np.array([.4, 0.]))
        current = margins.group_masses(adjusted)
        np.testing.assert_allclose(current.sum(axis=1), self.mass.sum(axis=1), rtol=0, atol=2e-12)
        np.testing.assert_array_equal(adjusted.diagonal(), self.grid.diagonal())
        self.assertTrue(np.all(current[:, 0] > self.mass[:, 0]))
        for group in margins.WIN_CLASSES.reshape((-1, *self.grid.shape)):
            ratios = adjusted[group] / self.grid[group]
            np.testing.assert_allclose(ratios, ratios[0], rtol=0, atol=2e-12)
        self.assertIs(margins.adjust(self.grid, self.mass, np.zeros(2)), self.grid)

    def test_swapping_teams_swaps_the_adjusted_grid(self):
        coefficients = np.array([.1, .8])
        home = margins.adjust(self.grid, self.mass, coefficients)
        away = margins.adjust(self.grid.T, margins.group_masses(self.grid.T), coefficients)
        np.testing.assert_allclose(home.T, away, rtol=0, atol=2e-12)

    def test_fit_learns_a_winner_advantage_effect(self):
        examples = [(0., .4, True), (0., -.4, False)] * 10
        intercept, advantage = margins.fit(examples)
        self.assertAlmostEqual(intercept, 0.)
        self.assertGreater(advantage, 0.)


if __name__ == '__main__':
    unittest.main()
