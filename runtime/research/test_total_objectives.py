from copy import deepcopy
import unittest

import numpy as np

from analysis_freeze import digest
from total_objectives import decisions


class TotalObjectiveTests(unittest.TestCase):
    def prediction(self, line=2.25):
        m = np.zeros((4, 4))
        m[1, 0], m[1, 1], m[2, 1] = .3, .3, .4
        q = {'verified': True, 'match_id': 'synthetic', 'bookmaker': 'synthetic',
             'market': 'asian_total', 'line': line, 'over_odds': 1.9, 'under_odds': 1.9,
             'source_url': 'https://example.com/odds',
             'quoted_at': '2026-11-18T00:00:00Z', 'query_time': '2026-11-18T00:01:00Z'}
        return {'matrix': m.tolist(), 'matrix_hash': digest(m.tolist()),
                'input': {'match_id': 'synthetic', 'cutoff_at': '2026-11-18T00:02:00Z', 'total_quote': q},
                'total': {'reference_direction': 'under'}}

    def test_quarter_line_strict_hits_differ_from_half_win_weighted_objective(self):
        p = self.prediction(); before = deepcopy(p)
        d = decisions(p)
        self.assertEqual(d['choices'], {'settlement_balance': 'under', 'strict_full_win': 'over',
                                       'positive_settlement': 'under', 'expected_return': 'under'})
        self.assertAlmostEqual(d['objectives']['strict_full_win']['over'], .4)
        self.assertAlmostEqual(d['objectives']['expected_return']['under'], .005)
        self.assertEqual(p, before)

    def test_integer_line_push_is_excluded_from_strict_denominator(self):
        d = decisions(self.prediction(2))
        self.assertAlmostEqual(d['objectives']['strict_full_win']['over'], .4 / .7)
        self.assertAlmostEqual(d['probabilities']['over']['push'], .3)

    def test_return_uses_actual_price_while_hit_probability_does_not(self):
        p = self.prediction(); p['input']['total_quote']['over_odds'] = 3.
        d = decisions(p)
        self.assertEqual(d['choices']['expected_return'], 'over')
        self.assertEqual(d['choices']['settlement_balance'], 'under')
        self.assertAlmostEqual(d['objectives']['expected_return']['over'], .35)

    def test_missing_stale_and_unverified_quotes_do_not_invent_a_line(self):
        for change in ({'quoted_at': '2026-11-17T00:00:00Z'}, {'verified': False}, None):
            p = self.prediction()
            if change is None:
                del p['input']['total_quote']
            else:
                p['input']['total_quote'].update(change)
            self.assertEqual(decisions(p)['choices'], {})


if __name__ == '__main__':
    unittest.main()
