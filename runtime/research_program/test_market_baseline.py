import copy
import unittest

import numpy as np

from research_program import market_baseline
from research_program.elo_kelly_shadow import devig
from research_program.three_arm_model import score_picks
from v2_test.data_contract import digest


class MarketBaselineTests(unittest.TestCase):
    def fixture(self):
        url = 'https://example.org/odds?markets=1x2%2Ctotals'
        capture = dict(match_id='espn:42', provider_match_id='event1', bookmaker='pinnacle',
                       source_url=url, source_sha256='a' * 64,
                       quoted_at='2026-11-23T10:00:00Z', query_time='2026-11-23T10:01:00Z')
        return {
            'match_id': 'espn:42',
            'score_basis': 'regulation_90',
            'kickoff_at': '2026-11-23T12:00:00Z',
            'cutoff_at': '2026-11-23T10:30:00Z',
            'total_quote': dict(capture, market='standard_total', line=2.75, verified=True,
                                over_odds=1.98, under_odds=1.92),
            'market_snapshots': [
                dict(capture, market='1x2', decimal_odds={'home': 1.67, 'draw': 4.1, 'away': 5.0}),
                dict(capture, market='totals', line=2.75, decimal_odds={'over': 1.98, 'under': 1.92}),
            ],
        }

    def test_audited_scores_are_independent_and_reproducible(self):
        match = self.fixture()
        before = copy.deepcopy(match)
        result = market_baseline.predict(match)
        self.assertEqual(result['status'], 'AVAILABLE')
        self.assertEqual(match, before)
        self.assertEqual(result['primary'], result['top3'][0])
        self.assertEqual(len(set(result['top3'])), 3)
        self.assertEqual(result['lowest_price_1x2'], 'home')
        self.assertEqual(result['lowest_price_total'], 'under')
        snapshot = result['quote_snapshot']
        self.assertEqual(result['quote_snapshot_sha256'], digest(snapshot))
        self.assertEqual(snapshot['provider_match_id'], 'event1')
        self.assertFalse(result['active_model_changed'])
        expected, _ = devig(snapshot['h2h']['prices'])
        for side in expected:
            self.assertAlmostEqual(result['wdl_probabilities'][side], expected[side], places=10)
        quote = snapshot['total']['prices']
        implied_over = (1 / quote['over']) / sum(1 / value for value in quote.values())
        over = result['full_win_total']['settlements']['over']
        won = over['win'] + .5 * over['half_win']
        lost = over['loss'] + .5 * over['half_loss']
        self.assertAlmostEqual(won / (won + lost), implied_over, places=10)

    def test_scores_provided_for_tied_odds_without_arbitrary_direction(self):
        match = self.fixture()
        match['market_snapshots'][0]['decimal_odds'] = {'home': 2.5, 'draw': 3.3, 'away': 2.5}
        match['market_snapshots'][1]['decimal_odds'] = {'over': 1.95, 'under': 1.95}
        match['total_quote'].update(over_odds=1.95, under_odds=1.95)
        result = market_baseline.predict(match)
        self.assertEqual(result['status'], 'AVAILABLE')
        self.assertIsNone(result['lowest_price_1x2'])
        self.assertIsNone(result['lowest_price_total'])
        self.assertEqual(len(set(result['top3'])), 3)

    def test_conflicting_duplicate_does_not_depend_on_order(self):
        match = self.fixture()
        copied = copy.deepcopy(match['market_snapshots'][0])
        match['market_snapshots'].append(copied)
        self.assertEqual(market_baseline.predict(match), market_baseline.predict(self.fixture()))
        copied['decimal_odds']['home'] = 1.99
        for order in (match['market_snapshots'], match['market_snapshots'][::-1]):
            result = market_baseline.predict(dict(match, market_snapshots=order))
            self.assertEqual(result['reasons'], ['CONFLICTING_CAPTURE_QUOTES'])

    def test_capture_must_match_identity_source_and_precise_time(self):
        for key, bad in (('provider_match_id', 'event2'), ('match_id', 'espn:7'),
                         ('source_sha256', 'b' * 64), ('query_time', '2026-11-23T10:05:00Z'),
                         ('quoted_at', '2026-11-23T10:01:01Z'), ('period', 'first_half'),
                         ('source_url', 'https://example.org?api_key=private')):
            with self.subTest(key=key):
                match = self.fixture()
                match['market_snapshots'][0][key] = bad
                result = market_baseline.predict(match)
                self.assertEqual(result['status'], 'ABSTAIN')
                self.assertNotIn('top3', result)

    def test_stale_missing_or_mismatched_total_lines_abstain(self):
        tests = [
            ('cutoff_at', '2026-11-23T11:30:00Z', 'QUOTE_TOO_OLD_FOR_LOCKED_3600_SECOND_WINDOW'),
            ('kickoff_at', '2026-11-23T09:58:00Z', 'CAPTURE_OR_KICKOFF_CONFLICT'),
            ('total_quote', None, 'NO_REAL_TOTAL_QUOTE'),
            ('score_basis', 'incl_extra_time', 'REGULATION_SCOPE_REQUIRED'),
        ]
        for key, bad, reason in tests:
            with self.subTest(key=key):
                self.assertIn(reason, market_baseline.predict(dict(self.fixture(), **{key: bad}))['reasons'])
        match = self.fixture()
        match['total_quote']['under_odds'] = 1.88
        self.assertIn('PRIMARY_TOTAL_QUOTE_DISAGREES_WITH_CAPTURE',
                      market_baseline.predict(match)['reasons'])

    def test_numerical_helper_has_finite_normalized_matrices(self):
        h2h = {'home': 1.65, 'draw': 4.1, 'away': 5.0}
        total = {'line': 3.25, 'over': 1.99, 'under': 1.89}
        plain, projected = market_baseline.quote_only_matrices(h2h, total)
        for matrix in (plain, projected):
            self.assertEqual(matrix.shape, (51, 51))
            self.assertTrue(np.isfinite(matrix).all() and (matrix >= 0).all())
            self.assertAlmostEqual(matrix.sum(), 1.)
        self.assertAlmostEqual(score_picks(projected)['wdl_probabilities']['draw'], devig(h2h)[0]['draw'])
        self.assertFalse(np.array_equal(plain, projected))


if __name__ == '__main__':
    unittest.main()
