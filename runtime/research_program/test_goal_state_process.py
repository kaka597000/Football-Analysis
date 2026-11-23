import unittest

import numpy as np
from scipy.stats import poisson

from research_program import goal_state_process as model


class GoalStateTests(unittest.TestCase):
    def test_uniformization_matches_independent_poisson_even_at_boundary(self):
        def marginal(rate, size):
            return np.r_[poisson.pmf(np.arange(size-1), rate), poisson.sf(size-2, rate)]
        for size in (3, 18):
            mass = model.simulate([1.75, 1.13], np.ones((3, 3)), size)
            np.testing.assert_allclose(mass, np.outer(marginal(1.75, size), marginal(1.13, size)), atol=2e-13)
            self.assertAlmostEqual(mass.sum(), 1.)

    def test_state_effect_follows_scorer_and_swaps_sides(self):
        factors = [[1.2, .75, .9], [1.4, .95, .7], [1.55, 1.4, .7]]
        a = model.simulate([2., 1.], factors, 25)
        b = model.simulate([1., 2.], factors, 25)
        np.testing.assert_allclose(a, b.T, atol=3e-13)
        self.assertGreater(abs(a[1, 1] - np.exp(-3)*2), .0001)
        self.assertGreaterEqual(float(a.min()), 0.)

    def test_stoppage_precedes_second_half_and_credit_follows_beneficiary(self):
        row = dict(actual=[1, 1], goals=[dict(half=1, seconds=2800., side='away'),
                                        dict(half=2, seconds=10., side='home')])
        exposure, counts = np.zeros((3, 3)), np.zeros((3, 3), int)
        model.accumulate(row, [2., 1.], exposure, counts)
        self.assertAlmostEqual(exposure.sum(), 3.)
        self.assertAlmostEqual(exposure[1, 0], 20/5400)
        self.assertAlmostEqual(exposure[1, 2], 10/5400)
        self.assertEqual(counts.tolist(), [[0, 0, 0], [1, 1, 0], [0, 0, 0]])
        with self.assertRaises(ValueError):
            model.accumulate(dict(row, actual=[2, 0]), [2, 1], exposure, counts)

    def test_zero_zero_is_exposure_and_same_day_never_trains_its_own_fixture(self):
        game = dict(match_id='g', date='2017-01-01', home='a', away='b', cohort='eng.1',
                    actual=[0, 0], goals=[])
        prior = model.fit([game], '2017-01-02', 'a'*64)
        self.assertEqual(prior['n'], 1)
        np.testing.assert_allclose(np.array(prior['expected_exposure'])[:, 1], [.9]*3)
        with self.assertRaises(ValueError):
            model.fit([game], '2017-01-01', 'a'*64)
        with self.assertRaises(ValueError):
            model.fit([game, game], '2017-01-02', 'a'*64)

    def test_first_and_backups_are_distinct_and_direction_heads_independent(self):
        first = dict(primary='1-1', top3=['1-1','1-0','0-0'], wdl='draw', total_side='under', total_line=2.75)
        other = dict(primary='2-1', top3=['2-1','1-1','3-1'], wdl='home', total_side='over', total_line=2.75)
        choice = dict(first='a', backups='b', wdl='b', total='a')
        result = model.combine_heads(dict(a=first, b=other), choice)
        self.assertEqual(result['top3'], ['1-1','2-1','3-1'])
        self.assertEqual((result['wdl'], result['total_side'], result['total_line']), ('home','under',2.75))

    def test_market_projection_preserves_regulation_and_real_quarter_settlements(self):
        artifact = dict(version=model.VERSION, factors=[[.8, .7, 1.], [1.2, 1.1, .9], [1.25, 1.3, 1.1]],
                        train_before='2019-07-01')
        odds = dict(home=2.05, draw=3.5, away=3.75)
        total = dict(line=2.75, over=1.88, under=2.01)
        matrices = model.matrices(artifact, odds, total, '2019-08-01')
        ref, adjusted = matrices['market'], matrices['process_projected']
        self.assertAlmostEqual(np.tril(ref, -1).sum(), np.tril(adjusted, -1).sum(), places=11)
        self.assertAlmostEqual(ref.trace(), adjusted.trace(), places=11)
        goals = np.add.outer(np.arange(len(ref)), np.arange(len(ref)))
        net = (np.sign(goals-2.5) + np.sign(goals-3.))/2
        share = (adjusted * np.maximum(net, 0)).sum()/(adjusted * abs(net)).sum()
        self.assertAlmostEqual(share, total['under']/(total['over']+total['under']), places=11)
        with self.assertRaises(ValueError):
            model.matrices(artifact, odds, total, '2019-06-30')


if __name__ == '__main__':
    unittest.main()
