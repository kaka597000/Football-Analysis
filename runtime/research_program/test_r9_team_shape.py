import unittest

import numpy as np

from research_program import all_scope_model as base
from research_program import r9_team_shape as model


class TeamShapeTests(unittest.TestCase):
    def setUp(self):
        self.prior = base.poisson_matrix([1.4, 1.2], (base.SIZE, base.SIZE))

    def test_no_same_day_leakage_and_phase_partition(self):
        def match(day, stage):
            return dict(date=day, stage=stage, home='A', away='B', home_goals=4, away_goals=0)
        rows = [match('2024-08-01', 'regular'), match('2024-08-01', 'regular'),
                match('2024-08-02', 'regular'), match('2024-08-02', 'cup')]
        design, exposure, state = model.history(rows, [self.prior] * 4, 'aus.1')
        np.testing.assert_array_equal(design[[0, 1, 3]], 0.)
        self.assertGreater(exposure[2][0], 1.9)
        self.assertGreater(design[2, 20, 0], design[2, 0, 0])
        modified = [dict(g, away_goals=8) if i >= 2 else g for i, g in enumerate(rows)]
        replay, _, _ = model.history(modified, [self.prior] * 4, 'aus.1')
        np.testing.assert_array_equal(replay, design)
        with self.assertRaises(ValueError):
            model.features(state, rows[-1], state['cup']['A'][0], 270.)

    def test_conditional_tail_normalization_and_zero_dose(self):
        x = np.random.default_rng(26).normal(size=(25, 2))
        corrected = model.adjust(self.prior, x, [.1, -.2], 1.)
        self.assertAlmostEqual(corrected.sum(), 1.)
        self.assertAlmostEqual(corrected[5, 0] / corrected[4, 0], self.prior[5, 0] / self.prior[4, 0])
        self.assertIs(model.adjust(self.prior, x, [1., 1.], 0.), self.prior)

    def test_rank_agrees_with_published_ties_and_includes_tail(self):
        matrix = np.zeros_like(self.prior)
        matrix[0, 2] = matrix[2, 0] = .4
        matrix[5, 4] = .2
        for rank, cell in enumerate([(0, 2), (2, 0), (5, 4)], start=1):
            self.assertEqual(model.actual_rank(matrix, cell), rank)
            self.assertEqual(base.pick(matrix, None)['top3'][rank-1], '%d-%d' % cell)
        issued = ['2-0', '0-2', '0-0']
        self.assertEqual(model.published_rank(matrix, (2, 0), issued), 1)
        self.assertEqual(model.published_rank(matrix, (5, 4), issued), 4)
        self.assertEqual(model.published_rank(matrix, (0, 1), issued), 5)

    def test_training_recovers_direction_and_validation_tie_favours_zero(self):
        x = np.zeros((25, 2))
        x[0, 0] = 1.
        design = np.repeat(x[None], 30, axis=0)
        beta = model.fit([self.prior] * 30, design, np.zeros((30, 2), int))
        self.assertGreater(beta[0], 0.)
        self.assertAlmostEqual(beta[1], 0.)
        dose, trials = model.choose([self.prior], [x], [0., 0.], [(0, 0)])
        self.assertEqual(dose, 0.)
        self.assertEqual(len(trials), 3)
        self.assertEqual(model.choose([], [], beta, [])[0], 0.)


if __name__ == '__main__':
    unittest.main()
