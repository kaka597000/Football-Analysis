from datetime import date
import unittest
from unittest.mock import patch

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
