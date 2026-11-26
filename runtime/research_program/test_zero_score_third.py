import unittest

import numpy as np

from research_program.zero_score_third import record


class ZeroScoreThirdTests(unittest.TestCase):
    def setUp(self):
        self.grid = np.array([
            [.04, .11, .09, .07],
            [.01, .13, .12, .02],
            [.01, .125, .09, .02],
            [.05, .04, .055, .02],
        ])
        self.original = dict(primary='1-1', top3=['1-1', '2-1', '1-2'], wdl='home',
                             total_line=2.5, total_side='under')
        self.ref = dict(id='fixture', cohort='uefa.europa', stage='league-phase', date='2026-10-01',
                        home='home', away='away', actual=[0, 1], line=2.5, source_sha256='identity',
                        forecast=dict(candidate=self.original, market={}))

    def test_supported_zero_score_changes_only_the_last_backup(self):
        row = record(self.ref, self.grid)
        self.assertEqual(row['forecast']['third'], dict(self.original, top3=['1-1', '2-1', '0-1']))
        self.assertEqual(row['added_paths'], ['home_zero'])
        self.assertEqual(row['actual_zero_paths_excluded'], ['home_zero'])
        self.assertAlmostEqual(row['probability_of_any_excluded_zero'], .38)
        self.assertAlmostEqual(row['probability_cost'], .01)

    def test_original_order_takes_precedence_on_a_reference_disagreement(self):
        self.original['top3'] = ['1-1', '1-2', '2-1']
        row = record(self.ref, self.grid)
        self.assertFalse(row['grid_matches_order'])
        self.assertEqual(row['forecast']['third'], self.original)
        self.assertEqual(row['added_paths'], [])


if __name__ == '__main__':
    unittest.main()
