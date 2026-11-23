from datetime import datetime, timezone
import unittest

from research_program.club_market_capture import choose_total, fresh_rows


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
