import unittest

import numpy as np

from research_program import handicap_matrix_study as study


class AsianProjectionTests(unittest.TestCase):
    def test_home_quarter_and_integer_results(self):
        differences = np.array([-1, 0, 1, 2])
        np.testing.assert_array_equal(study.stake_result(differences, -0.25), [-1.0, -0.5, 1.0, 1.0])
        np.testing.assert_array_equal(study.stake_result(differences, 0.25), [-1.0, 0.5, 1.0, 1.0])
        np.testing.assert_array_equal(study.stake_result(differences, -1.0), [-1.0, -1.0, 0.0, 1.0])

    def test_soft_share_improves_without_changing_wdl_or_ou(self):
        side = np.array([0.3, 0.4, 0.2, 0.1])
        prior = np.outer(side, side)
        market = {
            'bookmaker': 'b',
            'h2h': {'home': 2.0, 'draw': 3.4, 'away': 4.0},
            'total': {'line': 2.5, 'over': 1.9, 'under': 2.01},
        }
        handicap = {'bookmaker': 'b', 'line': -1.0, 'home': 1.8, 'away': 2.1}
        matrices, info = study.project(prior, market, handicap)
        before, after = matrices.values()
        h, a = np.indices(prior.shape)
        self.assertLess(
            abs(info['home_adjusted_share'] - info['home_target_share']),
            abs(info['home_reference_share'] - info['home_target_share']),
        )
        for mask in (h > a, h == a, h < a, h + a > 2.5):
            self.assertAlmostEqual(before[mask].sum(), after[mask].sum(), places=11)
        with self.assertRaises(ValueError):
            study.project(prior, market, dict(handicap, bookmaker='other'))


if __name__ == '__main__':
    unittest.main()
