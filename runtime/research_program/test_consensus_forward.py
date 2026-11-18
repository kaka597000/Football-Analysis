import unittest

from research_program.consensus_forward import build_groups


class NamedBookPanelTest(unittest.TestCase):
    def test_region_deduplication_and_exchange_exclusion(self):
        def quote(key, price, time='04:00:00'):
            return {'value': {'bookmaker': key, 'market': 'h2h', 'quoted_at': '2026-11-18T' + time + 'Z',
                              'odds': {'home': price, 'draw': 3.5, 'away': 4.0}}}

        snapshot = {'observations': [quote('unibet_fr', 2.1), quote('unibet_nl', 2.2, '04:03:00'),
                                     quote('pinnacle', 2.4), quote('betfair_ex_eu', 2.3), quote('matchbook', 2.6),
                                     quote('winamax_de', 1.9), quote('winamax_fr', 2.4)]}
        groups = build_groups(snapshot)
        self.assertEqual(set(groups), {'unibet', 'pinnacle'})
        self.assertEqual(groups['unibet']['decimal'], [2.2, 3.5, 4.0])
        self.assertEqual(groups['unibet']['keys'], ['unibet_nl'])
