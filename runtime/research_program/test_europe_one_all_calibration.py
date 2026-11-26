import unittest

import numpy as np

from research_program import europe_one_all_calibration as study


class TestEuropeanOneAllCalibration(unittest.TestCase):
    def setUp(self):
        self.grid = np.array([[.03, .05, .07], [.14, .25, .06], [.20, .09, .11]])
        self.original = dict(primary='1-1', top3=['1-1', '2-0', '1-0'], wdl='home',
                             total_side='under', total_line=2.5)

    def test_update_changes_only_one_all_odds_and_keeps_other_cells_in_proportion(self):
        offset = study.fit(np.full(8, .25), np.zeros(8))
        updated = study.adjust(self.grid, offset)
        self.assertLess(offset, 0.)
        self.assertAlmostEqual(float(updated.sum()), 1.)
        self.assertAlmostEqual(float(self.grid[1, 1]), .25)
        self.assertAlmostEqual(float(updated[1, 1] / updated[0, 0]), float(.25 / .03 * np.exp(offset)))
        mask = np.ones(self.grid.shape, dtype=bool)
        mask[1, 1] = False
        np.testing.assert_allclose(updated[mask] / self.grid[mask], updated[0, 0] / self.grid[0, 0])

    def test_replay_preserves_original_direction_line_and_ineligible_picks(self):
        row = dict.fromkeys(study.IDENTITY)
        row.update(actual=[2, 2], line=2.5, forecast=dict(candidate=self.original, market={}))
        state = study.eligibility(self.original, self.grid)
        self.assertEqual(state, 'eligible')
        replay = study.record(row, self.grid, state, -1.5)
        self.assertEqual(replay['forecast']['r13'], self.original)
        self.assertEqual(replay['forecast']['full'], dict(self.original, primary='2-0', top3=['2-0', '1-0', '2-2']))
        self.assertTrue(replay['actual_outcome_in_three'])
        self.assertFalse(replay['original_three_exact'])
        self.assertEqual(replay['minimum_goal_error'], 2)
        row['forecast']['candidate'] = dict(self.original, wdl='draw')
        state = study.eligibility(row['forecast']['candidate'], self.grid)
        self.assertEqual(state, 'no_independent_win_preference')
        self.assertEqual(study.record(row, self.grid, state, -1.5)['forecast']['full'], row['forecast']['candidate'])
