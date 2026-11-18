import unittest

import numpy as np

from research_program import three_arm_model as model


class ThreeArmTests(unittest.TestCase):
    def setUp(self):
        self.cutoff = '2026-11-10T12:00:00Z'
        self.m = np.array([[.09, .14, .05, .02], [.11, .18, .06, .03],
                           [.08, .08, .04, .03], [.03, .03, .02, .01]])
        self.m /= self.m.sum()
        self.market = {'h2h': {'home': 2., 'draw': 3.5, 'away': 4.},
                       'total': {'line': 2.25, 'over': 1.8, 'under': 2.2}, 'quoted_at': self.cutoff}

    def historical(self, **kw):
        row = dict(match_id='past', cohort='bra.1', cutoff_at='2026-11-07T19:00:00Z',
                   kickoff_at='2026-11-07T19:01:00Z', observed_at='2026-11-07T21:00:00Z',
                   current_matrix=self.m, home_goals=1, away_goals=0, market=None)
        return dict(row, **kw)

    def test_simultaneous_wdl_and_asian_fair_stake_after_projection(self):
        for line in (2., 2.25, 2.5, 2.75):
            with self.subTest(line=line):
                market = dict(self.market, total=dict(self.market['total'], line=line))
                quote = model._quote_values(market, model.aware(self.cutoff))
                m = model._market_matrix(self.m, quote)
                np.testing.assert_allclose(model.probabilities(m), quote[0], atol=1e-12)
                totals = model.score_picks(m, line=line)['total']
                over = totals['settlements']['over']
                wins = over['win'] + .5 * over['half_win']
                losses = over['loss'] + .5 * over['half_loss']
                self.assertAlmostEqual(wins / (wins + losses), quote[2], places=11)
                self.assertAlmostEqual(m.sum(), 1.)
                self.assertTrue((m >= 0).all())

    def test_cutoff_excludes_future_labels_and_checks_each_quote_time(self):
        prior = self.historical()
        after = self.historical(match_id='future', observed_at='2026-11-11T10:00:00Z')
        artifact = model.fit([prior, after], cutoff_at=self.cutoff)
        self.assertEqual(artifact, model.fit([prior], cutoff_at=self.cutoff))
        for field, wrong in [('h2h_quoted_at', '2026-11-10T12:01:00Z'),
                             ('total_quoted_at', '2026-11-10T10:51:00Z')]:
            with self.assertRaisesRegex(ValueError, 'market quotes'):
                model.apply(artifact, self.m, dict(self.market, **{field: wrong}),
                            cohort='bra.1', cutoff_at=self.cutoff)
        with self.assertRaisesRegex(ValueError, 'training cutoff'):
            model.apply(artifact, self.m, None, cohort='bra.1', cutoff_at=prior['cutoff_at'])

    def test_proxy_only_requires_opt_in_and_cannot_be_used_as_live_quote(self):
        market = {k: v for k, v in self.market.items() if k != 'quoted_at'}
        row = dict(match_id='past', cohort='eng.1', historical_proxy=True, game_date='2026-11-07',
                   current_matrix=self.m, home_goals=3, away_goals=0, market=market)
        with self.assertRaisesRegex(ValueError, 'opt-in'):
            model.fit([row], cutoff_at=self.cutoff)
        artifact = model.fit([row], cutoff_at=self.cutoff, allow_historical_proxy=True)
        self.assertEqual(artifact['market_training_by_source']['date_only_proxy'], {'eng.1': 1})
        self.assertEqual(artifact['cohort_calibration_n'], 0)
        self.assertGreater(artifact['market_weight'], 0.)
        with self.assertRaisesRegex(ValueError, 'market quotes'):
            model.apply(artifact, self.m, market, cohort='eng.1', cutoff_at=self.cutoff)
        matrices = model.apply_historical_proxy(artifact, self.m, market, cohort='eng.1', game_date='2026-11-11')
        np.testing.assert_array_equal(matrices['market_cohort'], matrices['market'])
        with self.assertRaisesRegex(ValueError, 'later match day'):
            model.apply_historical_proxy(artifact, self.m, market, cohort='eng.1', game_date='2026-11-10')

    def test_zero_coefficients_preserve_all_pmf_bits_and_floating_total_tie(self):
        artifact = model.fit([], cutoff_at=self.cutoff)
        m = np.array([[.5 + 1e-14, .5 - 1e-14]]) * (1 - 1e-15)
        arms = model.apply(artifact, m, None, cohort='bra.1', cutoff_at=self.cutoff)
        self.assertEqual(arms['market_cohort'].tobytes(), arms['market'].tobytes())
        self.assertEqual(model.score_picks(m, line=.5)['total']['direction'], 'over')
        # Full-win tie at .75. A one-goal result has a half win for over.
        quarter_line = np.array([[.25, .5, .25]])
        self.assertEqual(model.score_picks(quarter_line, line=.75)['total']['direction'], 'over')
        self.assertEqual(model.score_picks(np.array([[.51, .49]]), line=.5)['total']['direction'], 'under')

    def test_small_cohort_shrinks_and_unseen_uses_pooled_frozen_data(self):
        rows = [self.historical(match_id=str(i), cohort='eng.1' if i < 10 else 'bra.1',
                                home_goals=2 if i < 10 else 0, away_goals=2) for i in range(11)]
        artifact = model.fit(rows, cutoff_at=self.cutoff)
        known = model.apply(artifact, self.m, None, cohort='bra.1', cutoff_at=self.cutoff)
        new = model.apply(artifact, self.m, None, cohort='ita.1', cutoff_at=self.cutoff)
        self.assertEqual(artifact['cohorts']['bra.1']['n'], 1)
        self.assertTrue(np.linalg.norm(np.array(artifact['cohorts']['bra.1']['coefficients'])
                                       - artifact['global_coefficients']) < .1)
        np.testing.assert_array_equal(known['current'], self.m)
        np.testing.assert_array_equal(known['market'], self.m)
        for key in ('market_cohort', 'current', 'market'):
            self.assertAlmostEqual(new[key].sum(), 1.)
        self.assertGreater(new['market_cohort'][2, 2], self.m[2, 2])
        self.assertFalse(np.array_equal(new['market_cohort'], known['market_cohort']))

    def test_receives_rectangular_matrix_and_separate_decisions(self):
        m = np.array([[.1, .16, .15], [.25, .17, .17]])
        picks = model.score_picks(m, line=1.75)
        self.assertEqual((picks['primary'], picks['wdl']), ('1-0', 'away'))
        self.assertAlmostEqual(sum(picks['total']['settlements']['over'].values()), 1)
        self.assertIsNone(model.score_picks(m)['total'])
