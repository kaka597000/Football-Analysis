import io
import json
import tarfile
import unittest

from archive import Archives, historical_line, resolve_club, sha
from metrics import outcome, summarize
from replay import fit_input


def raw_archive(obj, name):
    file=io.BytesIO()
    with tarfile.open(fileobj=file,mode='w') as t:
        b=json.dumps(obj).encode()
        info=tarfile.TarInfo(name)
        info.size=len(b)
        t.addfile(info,io.BytesIO(b))
    file.seek(0)
    return tarfile.open(fileobj=file)


class EvidenceTests(unittest.TestCase):
    def test_name_matching_neither_fuzzy_nor_ambiguous(self):
        self.assertEqual(resolve_club(['Démo FC'],['Demo','Demo City']),'Demo')
        self.assertIsNone(resolve_club(['Demo'],['Demo FC','FC Demo']))
        self.assertIsNone(resolve_club(['Sporting'],['Sporting United']))

    def test_actual_line_requires_both_named_book_prices(self):
        self.assertIsNone(historical_line({'B365>2.5':'2'}))
        self.assertIsNone(historical_line({'B365>2.5':'nan','B365<2.5':'2'}))
        self.assertEqual(historical_line({'B365>2.5':'1.90','B365<2.5':'2'}),2.5)

    def test_target_fields_not_accessed_during_fitting(self):
        from datetime import date
        self.assertEqual(fit_input([{'day':date(2021,11,20)}],2021)[1],{})

    def test_verified_raw_prices_time_and_freeze_binding(self):
        source='https://sample.example/odds'
        at='2026-11-21T10:00:00Z'
        kick='2026-11-21T12:00:00Z'
        market={'key':'totals','last_update':at,'outcomes':[{'name':'Over','point':2.75,'price':1.9},
                                                            {'name':'Under','point':2.75,'price':2.0}]}
        payload=[{'id':'native','commence_time':kick,'bookmakers':[{'key':'book','markets':[market]}]}]
        h=sha(json.dumps(payload).encode())
        q=dict(source_sha256=h,source_url=source,quoted_at=at,query_time=at,bookmaker='book',
               line=2.75,match_id='e',market='asian_total',over_odds=1.9,under_odds=2.0,verified=True)
        snap=dict(q, market='totals', provider_match_id='native',decimal_odds={'over':1.9,'under':2.})
        f=dict(match_id='e',cutoff_at=at,kickoff_at=kick,total_quote=q,market_snapshots=[snap])
        a=Archives.__new__(Archives)
        with raw_archive(payload,f'raw/open-pregame/{h}.json') as a.assets:
            self.assertEqual(a.quote_line(f),2.75)
            self.assertIsNone(a.quote_line(dict(f,cutoff_at='2026-11-21T09:00:00Z')))
            self.assertIsNone(a.quote_line(dict(f,market_snapshots=[dict(snap,provider_match_id='wrong')])))
            self.assertIsNone(a.quote_line(dict(f,market_snapshots=[])))
            self.assertIsNone(a.quote_line(dict(f,total_quote=dict(q,under_odds=1.4))))

    def test_push_counts_separately_and_matrix_top3_can_hit_without_first(self):
        pick={'rates':[1.,1.], 'first':'0-0', 'top3':['0-0','1-0','1-1'],'wdl':'draw','total':'over'}
        candidate=dict(pick,first='1-1',top3=['1-1','0-0','1-0'])
        record=dict(id='e',day='2026-01-04',line=2.,baseline=pick,candidate=candidate,status='paired')
        graded=outcome(record,(1,1))
        m=summarize([record],{'e':graded})['metrics']
        self.assertEqual((m['first']['gained'],m['top3']['gained']),(1,0))
        self.assertEqual(m['total']['settlements']['candidate'],{'PUSH':1})
        self.assertEqual(m['total']['candidate_hits'],0)
