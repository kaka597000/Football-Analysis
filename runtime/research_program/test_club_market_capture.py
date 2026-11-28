from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from research_program.club_market_capture import attach, choose_total, fresh_rows
from research_program.display_window_review import _market_clock_match, _quote_source


class ClubMarketTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 23, 3, 10, tzinfo=timezone.utc)
        self.quote_time = '2026-11-23T03:01:00Z'
        self.meta = {'url':'https://api.infersports.dev/v1/events/evt_A/odds',
                     'captured_at':'2026-11-23T03:08:00Z','sha256':'a'*64}

    def row(self, market, book='crown', line=None, prices=None, **fields):
        defaults = {'home':1.8,'draw':3.5,'away':4.1} if market=='1x2' else {'over':1.9,'under':1.93}
        return dict(bookmaker=book,market_type=market,period='full_time',format='decimal',status='open',
                    as_of=self.quote_time,line=line,prices=prices or defaults) | fields

    def rows(self,*rows,stale=False):
        return fresh_rows({'event_id':'evt_A','stale':stale,'odds':rows,'as_of':self.quote_time},
                          self.meta,'espn:1',self.now)

    def test_opening_window_keeps_actual_source_time_and_the_normal_window_stays_one_hour(self):
        row = self.row('1x2', as_of='2026-11-22T18:00:00Z')
        raw = {'stale': False, 'event_id': 'evt_A', 'odds': [row]}
        self.assertEqual(fresh_rows(raw, self.meta, 'espn:1', self.now), [])
        opening = fresh_rows(raw, self.meta, 'espn:1', self.now, maximum_age=timedelta(hours=24))
        self.assertEqual(opening[0]['quoted_at'], row['as_of'])
        self.assertEqual(opening[0]['query_time'], self.meta['captured_at'])
        row['as_of'] = '2026-11-22T03:09:59Z'
        self.assertEqual(fresh_rows(raw, self.meta, 'espn:1', self.now, maximum_age=timedelta(hours=24)), [])

    def test_established_source_time_and_split_lines(self):
        hda = self.row('1x2')
        split = self.row('totals',line=2.75,handicap_components=[3,2.5])
        old = self.row('totals',line=2.5,as_of='2026-11-23T02:00:00Z')
        bad = self.row('totals',line=3,handicap_components=[2.5,3.5])
        after = self.row('totals',line=3.25,as_of='2026-11-23T03:09:00Z')
        self.assertEqual(len(self.rows(hda,split,old,bad,after)),2)
        self.assertFalse(self.rows(hda,split,stale=True))
        choice = choose_total(self.rows(hda,split))
        self.assertEqual((choice['line'],choice['quoted_at']),(2.75,self.quote_time))

    def test_balanced_line_and_same_book_pair(self):
        main = self.row('totals',line=2.5)
        alt = self.row('totals',line=3.5,prices={'over':3.,'under':1.41})
        other = self.row('totals','sbobet',line=3)
        chosen = choose_total(self.rows(self.row('1x2'),alt,other,main))
        self.assertEqual((chosen['bookmaker'],chosen['line']),('crown',2.5))
        conflict = dict(main,prices={'over':2.,'under':1.9})
        self.assertEqual(choose_total(self.rows(main,conflict,other))['bookmaker'],'sbobet')
        self.assertIsNone(choose_total(self.rows(self.row('1x2'))))

    def test_j1_preparation_and_display_review_reproduce_both_timed_markets(self):
        day, start = '2026-11-23', '2026-11-23T08:00:00Z'
        home, away = 'espn:11', 'espn:22'
        fixture = dict(match_id='espn:1', cohort='jpn.1', competition='jpn.1', home=home, away=away,
                       stage='regular', score_basis='regulation_90', kickoff_at=start,
                       cutoff_at=day + 'T03:00:00Z')
        teams = {'jpn.1': {home: dict(espn_name='Home FC', provider_name='Home FC', provider_id='tm_H'),
                          away: dict(espn_name='Away FC', provider_name='Away FC', provider_id='tm_A')}}
        sides = [dict(homeAway='home', team=dict(id='11', displayName='Home FC')),
                 dict(homeAway='away', team=dict(id='22', displayName='Away FC'))]
        scheduled = dict(id='1', date=start, competitions=[dict(id='1', date=start, competitors=sides)],
                         status={'type': {'state': 'pre', 'completed': False}},
                         season={'slug': 'regular-season'})
        event = dict(id='evt_J1', sport='football', status='scheduled', scheduled_at=start,
                     league={'id': 'lg_VWS270YX', 'name': 'Jap1'},
                     home_team={'id': 'tm_H', 'name': 'Home FC'}, away_team={'id': 'tm_A', 'name': 'Away FC'})
        h2h = self.row('1x2', as_of=day + 'T03:02:00Z')
        raw = dict(event_id='evt_J1', stale=False, odds=[h2h, self.row('totals', line=2.5)])

        with TemporaryDirectory() as folder:
            root = Path(folder)

            def retain(name, obj, url):
                body = json.dumps(obj).encode()
                (root / name).write_bytes(body)
                return dict(file=name, url=url, captured_at=self.meta['captured_at'],
                            sha256=sha256(body).hexdigest())

            odds_url = 'https://api.infersports.dev/v1/events/evt_J1/odds?markets=1x2%2Ctotals'
            evidence = dict(event_id='evt_J1',
                            espn=retain('board.json', {'leagues': [{'slug': 'jpn.1'}], 'events': [scheduled]},
                                        'https://site.api.espn.com/apis/site/v2/sports/soccer/jpn.1/scoreboard'),
                            events=retain('events.json', {'data': [event]}, 'https://api.infersports.dev/v1/events'),
                            odds=retain('odds.json', raw, odds_url))
            prepared = attach(fixture, root, evidence, teams, self.now.isoformat())
            quote = prepared['total_quote']
            ok, reason, clock = _quote_source(quote, prepared, root, {}, h2h['prices'])
            self.assertTrue(ok, reason)
            self.assertEqual(_market_clock_match(quote, clock), (True, None))
            self.assertEqual(prepared['market_availability'], 'FRESH_SAME_BOOK_PAIR')

            wrong = [dict(row, quoted_at=quote['quoted_at']) if row['market'] == '1x2' else row
                     for row in prepared['market_snapshots']]
            self.assertEqual(_quote_source(quote, dict(prepared, market_snapshots=wrong), root, {},
                                           h2h['prices'])[1], 'QUOTE_RAW_1X2_PRICES_OR_CLOCK_DIFFER')
            self.assertFalse(_quote_source(quote, dict(prepared, stage='cup'), root, {}, h2h['prices'])[0])

            evidence['odds'] = retain('stale.json', dict(raw, stale=True), odds_url)
            stale = attach(fixture, root, evidence, teams, self.now.isoformat())
            self.assertNotIn('total_quote', stale)
            forged = dict(quote, source_sha256=evidence['odds']['sha256'])
            self.assertEqual(_quote_source(forged, stale, root, {}, h2h['prices'])[1], 'QUOTE_SOURCE_FEED_STALE')
