"""Unit tests for sharp Power-devig consensus and structural score kernel."""

import hashlib
import unittest

import numpy as np

from research_program.sharp_consensus_kernel import (
    VERSION,
    pool_book_probabilities,
    power_devig,
    predict,
    sharp_consensus_matrices,
)
from v2.model import probabilities


def _fixture(line=2.75, over_odds=1.95, under_odds=1.91, extra_book=True):
    sha = hashlib.sha256(b'verified-prematch-market-capture').hexdigest()
    snapshots = [
        {
            'match_id': 'espn:900001',
            'provider_match_id': 'evt-900001',
            'bookmaker': 'Bet365',
            'market': '1x2',
            'period': 'regulation_90',
            'line': None,
            'decimal_odds': {'home': 1.62, 'draw': 4.00, 'away': 5.25},
            'quoted_at': '2026-11-29T13:20:00Z',
            'query_time': '2026-11-29T13:30:00Z',
            'source_url': 'https://api.the-odds-api.com/v4/sports/soccer_epl/odds',
            'source_sha256': sha,
        },
        {
            'match_id': 'espn:900001',
            'provider_match_id': 'evt-900001',
            'bookmaker': 'Bet365',
            'market': 'totals',
            'period': 'regulation_90',
            'line': line,
            'decimal_odds': {'over': over_odds, 'under': under_odds},
            'quoted_at': '2026-11-29T13:20:00Z',
            'query_time': '2026-11-29T13:30:00Z',
            'source_url': 'https://api.the-odds-api.com/v4/sports/soccer_epl/odds',
            'source_sha256': sha,
        },
    ]
    if extra_book:
        snapshots.extend([
            {
                'match_id': 'espn:900001',
                'provider_match_id': 'evt-900001',
                'bookmaker': 'Pinnacle',
                'market': '1x2',
                'period': 'regulation_90',
                'line': None,
                'decimal_odds': {'home': 1.66, 'draw': 4.15, 'away': 5.40},
                'quoted_at': '2026-11-29T13:25:00Z',
                'query_time': '2026-11-29T13:30:00Z',
                'source_url': 'https://api.the-odds-api.com/v4/sports/soccer_epl/odds',
                'source_sha256': sha,
            },
            {
                'match_id': 'espn:900001',
                'provider_match_id': 'evt-900001',
                'bookmaker': 'Pinnacle',
                'market': 'totals',
                'period': 'regulation_90',
                'line': line,
                'decimal_odds': {'over': 1.98, 'under': 1.92},
                'quoted_at': '2026-11-29T13:25:00Z',
                'query_time': '2026-11-29T13:30:00Z',
                'source_url': 'https://api.the-odds-api.com/v4/sports/soccer_epl/odds',
                'source_sha256': sha,
            },
        ])
    return {
        'match_id': 'espn:900001',
        'cohort': 'eng.1',
        'stage': 'regular',
        'score_basis': 'regulation_90',
        'cutoff_at': '2026-11-29T13:45:00Z',
        'kickoff_at': '2026-11-29T15:00:00Z',
        'total_quote': {
            'verified': True,
            'match_id': 'espn:900001',
            'provider_match_id': 'evt-900001',
            'bookmaker': 'Bet365',
            'market': 'asian_total',
            'line': line,
            'over_odds': over_odds,
            'under_odds': under_odds,
            'quoted_at': '2026-11-29T13:20:00Z',
            'query_time': '2026-11-29T13:30:00Z',
            'source_url': 'https://api.the-odds-api.com/v4/sports/soccer_epl/odds',
            'source_sha256': sha,
        },
        'market_snapshots': snapshots,
    }


class SharpConsensusKernelTests(unittest.TestCase):
    def test_power_devig_removes_favorite_longshot_bias(self):
        prices = {'home': 1.45, 'draw': 4.60, 'away': 7.50}
        inv = np.array([1 / prices['home'], 1 / prices['draw'], 1 / prices['away']])
        prop = inv / inv.sum()
        pwr = power_devig(prices, ('home', 'draw', 'away'))
        self.assertAlmostEqual(float(pwr.sum()), 1.0, places=12)
        self.assertGreater(pwr[0], prop[0])
        self.assertLess(pwr[2], prop[2])

    def test_projected_kernel_preserves_exact_wdl_and_decoupled_top3(self):
        h2h = {
            'Pinnacle': {'home': 1.85, 'draw': 3.75, 'away': 4.35},
            'Bet365': {'home': 1.80, 'draw': 3.60, 'away': 4.33},
        }
        tot = {
            'Pinnacle': {'over': 1.93, 'under': 1.97},
            'Bet365': {'over': 1.90, 'under': 1.95},
        }
        target_wdl = pool_book_probabilities(h2h, ('home', 'draw', 'away'))
        res = sharp_consensus_matrices(h2h, tot, 2.5)
        for key in ('consensus_matrix', 'kernel_matrix'):
            mat = res[key]
            self.assertAlmostEqual(float(mat.sum()), 1.0, places=10)
            np.testing.assert_allclose(probabilities(mat), target_wdl, atol=1e-7)
        self.assertEqual(len(res['decoupled_top3']), 3)
        self.assertEqual(len(set(res['decoupled_top3'])), 3)
        self.assertEqual(res['decoupled_top3'][0], res['primary'])
        # Kernel boosts 2-1 relative to independent Poisson under identical WDL/Total
        self.assertGreater(res['kernel_matrix'][2, 1], res['consensus_matrix'][2, 1])

    def test_quarter_line_detects_asymmetry_between_net_and_full_win(self):
        # At line 2.75 with slight Over lean (over=1.86, under=2.04),
        # net settlement favors Over (3 goals is half-win), whereas strict full-win
        # (requiring >=4 goals for Over vs <=2 goals for Under) favors Under!
        fx = _fixture(line=2.75, over_odds=1.86, under_odds=2.04, extra_book=False)
        out = predict(fx)
        self.assertEqual(out['status'], 'AVAILABLE')
        self.assertEqual(out['version'], VERSION)
        self.assertEqual(out['full_win_total']['legacy_net_direction'], 'over')
        self.assertEqual(out['full_win_total']['strict_full_win_direction'], 'under')
        self.assertTrue(out['full_win_total']['quarter_line_asymmetry_flip'])

    def test_pools_sharp_books_and_ignores_stale_secondary_book(self):
        fx = _fixture(line=2.5, over_odds=1.91, under_odds=1.95, extra_book=True)
        # Add a stale Betfair snapshot (> 1h before cutoff) which must be excluded
        stale = dict(
            fx['market_snapshots'][0],
            bookmaker='Betfair Exchange',
            quoted_at='2026-11-29T11:00:00Z',
        )
        fx['market_snapshots'].append(stale)
        out = predict(fx)
        self.assertEqual(out['status'], 'AVAILABLE')
        self.assertEqual(out['pooled_h2h_books'], ['bet365', 'pinnacle'])
        self.assertEqual(out['pooled_total_books'], ['bet365', 'pinnacle'])


if __name__ == '__main__':
    unittest.main()
