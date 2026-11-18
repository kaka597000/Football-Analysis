from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest

from research_program import handicap_forward as forward


class VerifiedHandicapTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 18, tzinfo=timezone.utc)
        quote = lambda key, rows: {'key': key, 'last_update': self.now.isoformat(), 'outcomes': rows}
        pair = lambda name, price, point: {'name': name, 'price': price, 'point': point}
        self.event = {
            'id': 'provider-event',
            'home_team': 'H',
            'away_team': 'A',
            'bookmakers': [
                {
                    'key': 'pinnacle',
                    'markets': [
                        quote(
                            'h2h',
                            [
                                {'name': 'A', 'price': 3.4},
                                {'name': 'H', 'price': 2.1},
                                {'name': 'Draw', 'price': 3.3},
                            ],
                        ),
                        quote('spreads', [pair('A', 1.91, 0.25), pair('H', 2.01, -0.25)]),
                        quote('totals', [pair('Under', 1.85, 2.75), pair('Over', 2.07, 2.75)]),
                    ],
                }
            ],
        }

    def test_host_line_and_two_quoted_sides_survive_outcome_order(self):
        value, issue = forward.parse_pinnacle(self.event, self.now, self.now)
        self.assertIsNone(issue)
        self.assertEqual(
            value['handicap'], {'bookmaker': 'pinnacle', 'line': -0.25, 'home': 2.01, 'away': 1.91}
        )
        self.assertEqual(value['market']['total']['line'], 2.75)
        self.assertEqual(value['quoted_at']['spreads'], self.now.isoformat())

    def test_period_mismatch_or_nonopposite_handicap_excluded(self):
        for change in ('half', 'not_opposite', 'stale', 'wrong_club'):
            event = deepcopy(self.event)
            spread = event['bookmakers'][0]['markets'][1]
            if change == 'half':
                spread['key'] = 'spreads_h1'
            if change == 'not_opposite':
                spread['outcomes'][0]['point'] = 0.75
            if change == 'stale':
                spread['last_update'] = (self.now - timedelta(minutes=61)).isoformat()
            if change == 'wrong_club':
                spread['outcomes'][1]['name'] = 'Another club'
            with self.subTest(change=change):
                self.assertIsNone(forward.parse_pinnacle(event, self.now, self.now)[0])


if __name__ == '__main__':
    unittest.main()
