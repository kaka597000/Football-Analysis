from datetime import datetime, timedelta, timezone
import unittest

import numpy as np

from research_program import market_path_study as study


def panel(line=2.5, over=1.9, under=1.9, book='single'):
    return {
        'bookmaker': book,
        'h2h': {'home': 2.3, 'draw': 3.2, 'away': 3.1},
        'total': {'line': line, 'over': over, 'under': under},
    }


class MarketPathTests(unittest.TestCase):
    def setUp(self):
        side = np.array([0.16, 0.32, 0.28, 0.18, 0.06])
        self.base = np.outer(side, side)

    def test_equal_prices_have_no_increment(self):
        q = panel()
        groups = study.arms(self.base, q, q)
        np.testing.assert_allclose(groups['latest_only'], groups['latest_plus_movement'], atol=1e-14)
        self.assertEqual(study.move_type(q, q), 'same_line_no_total_price_move')
        with self.assertRaisesRegex(ValueError, 'one bookmaker'):
            study.arms(self.base, q, panel(book='different'))

    def test_independent_projection_uses_line_even_if_both_prices_unchanged(self):
        old, new = panel(2.5), panel(3.0)
        matrices = study.arms(self.base, old, new)
        self.assertEqual(study.move_type(old, new), 'line_change')
        self.assertEqual(study.move_type(old, panel(over=1.85)), 'same_line_price_move')
        shifted = study.forecast(matrices['latest_plus_movement'], 3.0)
        self.assertFalse(np.allclose(matrices['latest_only'], matrices['latest_plus_movement']))
        self.assertEqual(shifted['total']['line'], 3.0)
        self.assertEqual(shifted['primary'], shifted['top3'][0])
        self.assertEqual(study.outcome(shifted, {'home': 1, 'away': 2})['total_settlement'], 'PUSH')

    def test_stale_or_nonadvancing_quotes_cannot_create_live_path(self):
        start = datetime(2026, 11, 18, tzinfo=timezone.utc)
        end = start + timedelta(hours=5)

        def item(t, line=2.75):
            stamp = t.isoformat()
            return dict(
                panel(line),
                provider='source',
                provider_match_id='event',
                h2h_quoted_at=stamp,
                total_quoted_at=stamp,
                captured_at=stamp,
            )

        early, late = item(start), item(end, 3.0)

        def runs(a, b):
            return [(start, 'early', {'m': {'single': a}}), (end, 'late', {'m': {'single': b}})]

        self.assertEqual(study._match_path(runs(early, late), 'm', end)['total_movement'], 'line_change')
        self.assertIn('reason', study._match_path(runs(early, late), 'm', end + timedelta(minutes=61)))
        self.assertIn(
            'reason',
            study._match_path(runs(early, dict(late, total_quoted_at=early['total_quoted_at'])), 'm', end),
        )
        self.assertIn(
            'reason', study._match_path(runs(early, dict(late, provider_match_id='other')), 'm', end)
        )


if __name__ == '__main__':
    unittest.main()
