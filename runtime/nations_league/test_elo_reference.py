import unittest

import numpy as np

from nations_league import elo_reference as elo


class EloReferenceTests(unittest.TestCase):
    def test_same_day_updates_do_not_leak_across_fixtures(self):
        games = [
            {'date':'2024-02-01', 'home':'A', 'away':'B', 'neutral':True, 'score':[1,0]},
            {'date':'2024-01-01', 'home':'A', 'away':'B', 'neutral':True, 'score':[2,0]},
            {'date':'2024-01-01', 'home':'B', 'away':'C', 'neutral':True, 'score':[2,0]},
        ]
        history = elo.dated_ratings(games, ['2024-01-01', '2024-01-02', '2024-02-01', '2024-02-02'])
        self.assertEqual(history['2024-01-01'], {})
        self.assertEqual(history['2024-01-02'], {'A':1510., 'B':1500., 'C':1490.})
        self.assertEqual(history['2024-02-01'], history['2024-01-02'])
        self.assertGreater(history['2024-02-02']['A'], 1510.)

    def test_independent_neutral_rating_symmetry_and_standardized_line(self):
        artifact = {'include_elo':True, 'coefficients':[.2, .1, .5]}
        a = elo.forecast(artifact, [1700,1400], True)
        b = elo.forecast(artifact, [1400,1700], True)
        np.testing.assert_allclose(a, b.T)
        pred = elo.selections(a, (2,1))
        self.assertAlmostEqual(pred['p_over_2_5'], sum(a[i,j] for i,j in zip(*np.where(np.indices(a.shape).sum(axis=0)>=3))))
        self.assertEqual(pred['total_hit'], (pred['total_2_5']=='over'))
        self.assertEqual(pred['wdl'],'home')


if __name__=='__main__':
    unittest.main()
