import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from nations_league import repair, repair_forward
from research.analysis_freeze import digest


class ForwardTests(unittest.TestCase):
    def prediction(self,line=3.25):
        now=datetime.now(timezone.utc)
        quote={'match_id':'espn:1','bookmaker':'synthetic-book','market':'asian_total',
               'line':line,'over_odds':1.9,'under_odds':1.9,'verified':True,
               'quoted_at':(now-timedelta(seconds=10)).isoformat(),
               'query_time':(now-timedelta(seconds=5)).isoformat(),
               'source_url':'https://example.org/synthetic-test-quote'}
        m=np.zeros((51,51));m[2,1]=1.
        return {'matrix':m.tolist(),'matrix_hash':digest(m.tolist()),'primary':'2-1',
                'input':{'match_id':'espn:1','cutoff_at':now.isoformat(),'total_quote':quote}}

    def test_actual_quarter_line_keeps_half_settlements(self):
        p=repair_forward.attach_total(self.prediction())
        self.assertEqual(p['total']['over']['half_loss'],1.)
        self.assertEqual(p['total']['under']['half_win'],1.)
        self.assertEqual(p['total']['reference_direction'],'under')
        self.assertEqual(p['total']['quote']['line'],3.25)

    def test_missing_or_stale_quote_never_becomes_default_two_point_five(self):
        p=self.prediction();p['input'].pop('total_quote')
        self.assertIsNone(repair_forward.attach_total(p)['total'])
        p=self.prediction();p['input']['total_quote']['quoted_at']='2000-01-01T00:00:00Z'
        self.assertIsNone(repair_forward.attach_total(p)['total'])
        self.assertIn('QUOTE_TOO_OLD_FOR_LOCKED_3600_SECOND_WINDOW',p['total_quote_audit']['reasons'])

    def test_quote_identity_and_probability_integrity_are_required(self):
        p=self.prediction();p['input']['total_quote']['match_id']='espn:other'
        self.assertIsNone(repair_forward.attach_total(p)['total'])
        p=self.prediction();p['matrix_hash']='0'*64
        with self.assertRaises(ValueError):repair_forward.attach_total(p)

    def test_pending_and_missing_captures_stay_in_population(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'freezes').mkdir()
            now=datetime.now(timezone.utc)
            fixtures=[{'match_id':'espn:'+str(i),'cohort':'uefa.nations:A:league-phase',
                'stage':'league-phase','league_level':'A','home':'espn:11','away':'espn:12',
                'kickoff_at':(now+timedelta(days=i)).isoformat(),'cutoff_at':now.isoformat()} for i in (1,2)]
            record={'payload':{'schema':'WCPP-NATIONS-REPAIR-FORWARD-1','tracks':list(repair_forward.TRACKS),
                'fixtures_identity':fixtures,'eligible_match_ids':[f['match_id'] for f in fixtures]}}
            record['record_sha256']=digest(record)
            (root/'plan.json').write_text(json.dumps(record))
            result=repair_forward.review(root,[])
            self.assertEqual(result['population'],2);self.assertEqual(result['settled'],0)
            self.assertEqual(len(result['pending']),2);self.assertEqual(len(result['missing_capture_ids']),2)
            for comparison in result['comparisons'].values():
                self.assertEqual(comparison['scope'],2);self.assertEqual(comparison['finished'],0)
                self.assertEqual(comparison['models']['当前模型']['selected_population']['primary'],0)


if __name__=='__main__':unittest.main()
