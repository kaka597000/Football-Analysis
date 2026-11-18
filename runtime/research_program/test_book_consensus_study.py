import unittest

import numpy as np

from research_program.book_consensus_study import book_panel, evaluate, forecast, project_panel
from research_program.sequence_history import fixed_market_baseline


class BookConsensusTests(unittest.TestCase):
    def test_weights_measure_overround_after_devig_and_cap_near_zero(self):
        books = {'B': [2., 3., 6.], 'C': [1.8, 3., 4.5], 'A': [2.1, 3., 3.6]}
        panel = book_panel(books)
        odds = np.array([books[k] for k in sorted(books)])
        implied = 1 / odds
        p = implied / implied.sum(axis=1)[:, None]
        weights = 1 / np.maximum(implied.sum(axis=1) - 1, .0001)
        np.testing.assert_allclose(panel['equal_wdl'], p.mean(axis=0))
        np.testing.assert_allclose(panel['inverse_margin_wdl'], np.average(p, axis=0, weights=weights))
        self.assertGreater(panel['weights']['B'], .99)
        self.assertEqual(panel['floor_brands'], ['B'])
        self.assertEqual(panel, book_panel(dict(reversed(list(books.items())))))
        with self.assertRaises(ValueError):
            book_panel({'A': [2, 3, 1]})

    def test_same_total_and_baseline_fallback(self):
        market = {'h2h': dict(home=2., draw=3.1, away=4.), 'total': dict(over=2., under=2., line=2.5)}
        base = fixed_market_baseline([1.4, 1.22], market)
        odds = {'X': [2., 3.1, 4.], 'Y': [2.2, 3.5, 3.1], 'Z': [3.4, 3.1, 2.2]}
        panel = book_panel(odds)
        for arm, mat in project_panel(base, panel, 2.5, .5).items():
            pred = forecast(mat, 2.5)
            np.testing.assert_allclose(pred['wdl_p'], panel[arm + '_wdl'])
            self.assertAlmostEqual(pred['over_p'], .5)
            self.assertEqual(pred['total'], 'over')
        del odds['Z']
        insufficient = book_panel(odds)
        self.assertEqual(insufficient['bucket'], 'insufficient')
        for mat in project_panel(base, insufficient, 2.5, .5).values():
            np.testing.assert_array_equal(mat, base)

    def test_result_is_not_a_model_input(self):
        prices = {'h2h': dict(home=2., draw=3.1, away=4.), 'total': dict(over=1.8, under=2.1, line=2.5)}
        base = forecast(fixed_market_baseline([1.4, 1.1], prices), 2.5)
        archive = {'primary': base['scores'][0], 'primary_probability': base['score_p'][0], 'wdl': base['wdl'],
                   'wdl_probabilities': dict(zip(('home', 'draw', 'away'), base['wdl_p'])),
                   'total': {'direction': base['total']}}
        target = dict(matchday='2026-04-11', teams=['a', 'b'], raw_prices=prices, goal_rates=[1.4, 1.1],
                      match_id='fixture', source=['t', 2], cohort='test', forecasts={'baseline': archive})
        raw = dict(HomeTeam='a', AwayTeam='b', Date='11/04/2026', B365CH='2.0', B365CD='3.1', B365CA='4.0',
                   BVCH='1.9', BVCD='3.2', BVCA='3.9', BWCH='1.85', BWCD='3.4', BWCA='4.2')
        raw.update({'B365C>2.5': '1.8', 'B365C<2.5': '2.1'})
        first = evaluate(dict(target, actual=[0, 0]), dict(raw, FTHG='0', FTAG='0'))
        other = evaluate(dict(target, actual=[4, 1]), dict(raw, FTHG='4', FTAG='1'))
        self.assertEqual(first['forecasts'], other['forecasts'])
        self.assertNotEqual(first['outcomes'], other['outcomes'])
