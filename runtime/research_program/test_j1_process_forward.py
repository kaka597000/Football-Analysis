import unittest

import numpy as np

from research.analysis_freeze import aware
from research_program.j1_process_forward import matched_observations, process_arms


class J1ProcessForwardTests(unittest.TestCase):
    def test_goal_and_xg_controls_share_the_deduplicated_observations(self):
        before = '2026-11-25T10:00:00+00:00'
        cutoff = aware('2026-11-26T05:00:00+00:00')
        home = dict(match_id='fixture-1', side='home', goals=[0, 1], xg=[.75, .95], kickoff_at=before)
        away = dict(home, side='away', goals=[1, 0], xg=[.95, .75])
        missing = dict(home, match_id='fixture-2', xg=[None, None])
        pair = matched_observations(dict(fixtures=[home, missing]), dict(fixtures=[away]), cutoff)
        self.assertEqual(pair, [(0, .75)])

    def test_missing_chance_history_keeps_prior_and_large_changes_are_bounded(self):
        prior = np.array([[.4, .2], [.2, .2]])
        unchanged, exposure = process_arms(prior, [[], []])
        self.assertEqual(exposure['n'], [0, 0])
        for matrix in unchanged.values():
            np.testing.assert_allclose(matrix, prior, atol=1e-15)
        updated, exposure = process_arms(prior, [[(0, 4.)] * 5, [(0, .1)] * 5])
        self.assertEqual(exposure['ratios'][0][1], 1.25)
        self.assertEqual(exposure['ratios'][1][1], .8)
        self.assertGreater(updated['xg_history'][1, 0], updated['goals_history'][1, 0])
        self.assertAlmostEqual(updated['xg_history'].sum(), 1.)


if __name__ == '__main__':
    unittest.main()
