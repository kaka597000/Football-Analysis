from datetime import date
import unittest
from unittest.mock import Mock, patch

from audit_open_quotes import archived_quote, markets
from clocked_evidence import retained_h2h
from history import book, projected_games, scores


def fixture(day, half=(0,0), full=(1,0)):
    return dict(id=day.isoformat(),day=day,home='A',away='B',
                raw={'HTHG':str(half[0]),'HTAG':str(half[1]),'FTHG':str(full[0]),'FTAG':str(full[1]),
                     'B365H':'1.8','B365D':'3.5','B365A':'4.2','B365>2.5':'1.9','B365<2.5':'1.9'})


class TemporalProjectionTests(unittest.TestCase):
    def test_target_own_half_or_full_never_enters_its_projection(self):
        def project(h,f):
            priors=[fixture(date(2023,1,i)) for i in range(20,26)]
            target=fixture(date(2023,2,1),h,f)
            with patch('history.seasons',return_value=[('2223','eng.1',priors+[target])]):
                return list(projected_games('unused',training=False))[0]
        a,b=project((0,0),(1,0)),project((4,3),(5,5))
        self.assertEqual(a,b)
        self.assertNotIn('label',a)
        self.assertEqual(a['signal'],-1.)

    def test_archive_book_requires_all_five_named_legs(self):
        r=fixture(date(2023,2,2))['raw']
        self.assertEqual(book(r)['total']['line'],2.5)
        for key in ('B365A','B365<2.5'):
            self.assertIsNone(book({k:v for k,v in r.items() if k!=key}))
        self.assertIsNone(book(dict(r,B365A='nan')))
        self.assertIsNone(scores({'HTHG':'0','HTAG':''},'HT'))

    def test_too_short_history_keeps_same_fixture_and_market(self):
        game=fixture(date(2023,2,1))
        with patch('history.seasons',return_value=[('2223','eng.1',[game])]):
            row,=projected_games('unused',training=False)
        self.assertEqual(row['feature_status'],'less_than_six')
        self.assertEqual(row['signal'],0.)
        self.assertIsNotNone(row['market'])

    def test_native_1x2_binds_event_book_kickoff_source_time_and_both_teams(self):
        kickoff = '2026-11-20T20:00:00Z'
        stamp = '2026-11-20T19:30:00Z'
        prices = {'home': 1.55, 'draw': 3.65, 'away': 5.5}
        snap = {'provider_match_id': 'game-1', 'bookmaker': 'one',
                'h2h': {'quoted_at': stamp, 'prices': prices}}
        market = {'key': 'h2h', 'last_update': stamp, 'outcomes': [
            {'name': 'Team A', 'price': 1.55}, {'name': 'Team B', 'price': 5.5},
            {'name': 'Draw', 'price': 3.65}]}
        event = {'id': 'game-1', 'commence_time': kickoff, 'home_team': 'Team A',
                 'away_team': 'Team B', 'bookmakers': [{'key': 'one', 'markets': [market]}]}
        self.assertEqual(retained_h2h([event], snap, kickoff), prices)
        for change in ({'id': 'game-2'}, {'commence_time': stamp},
                       {'home_team': 'Team B', 'away_team': 'Team A'},
                       {'bookmakers': [{'key': 'two', 'markets': [market]}]},
                       {'bookmakers': [{'key': 'one', 'markets': [dict(market, last_update=kickoff)]}]}):
            self.assertIsNone(retained_h2h([dict(event, **change)], snap, kickoff))
        self.assertIsNone(retained_h2h([event, event], snap, kickoff))


class OpeningFieldsTests(unittest.TestCase):
    def test_open_and_observed_prices_compare_by_side_and_economic_value(self):
        prices = {'open': {'odds': '+100'}, 'close': {'odds': '-100'}}
        quote = {'moneyline': {side: prices for side in ('home', 'draw', 'away')},
                 'total': {side: {'open': {'line': prefix + '2.50', 'odds': '-110'},
                                  'close': {'line': prefix + '2.5', 'odds': '-110'}}
                           for side, prefix in (('over', 'o'), ('under', 'u'))}}
        self.assertEqual(markets(quote, 'open'), markets(quote, 'close'))
        quote['total']['under']['close']['line'] = 'o2.5'
        with self.assertRaisesRegex(ValueError, 'total side'):
            markets(quote, 'close')

    def test_unbound_or_postcut_snapshot_is_excluded_before_reading_body(self):
        report = Mock()
        cutoff = '2026-11-07T12:08:15Z'
        for event, stamp in [('2', '2026-11-07T11:55:00Z'), ('1', cutoff),
                             ('1', '2026-11-07T12:10:07Z')]:
            source = {'source_url': f'https://site.api.espn.com/summary?event={event}',
                      'query_time': stamp}
            status, quote = archived_quote(report, {'cutoff_at': cutoff, 'sources': [source]}, '1')
            self.assertEqual((status, quote), ('missing_bound_source', None))
        report.glob.assert_not_called()
