import hashlib
import json
import unittest

from early_boards import project_quote
from research.analysis_freeze import verify


class CleanNativeTests(unittest.TestCase):
    def test_project_quote_keeps_current_when_opening_is_missing(self):
        quote=project_quote([{
            'provider':{'name':'DraftKings'},
            'moneyline':{k:{'close':{'odds':v}} for k,v in [('home','+100'),('draw','-105'),('away','+125')]},
            'total':{
                'over':{'close':{'line':'o2.5','odds':'+100'}},
                'under':{'close':{'line':'u2.5','odds':'-100'}}
            }
        }])
        self.assertIsNone(quote['opening_total'])
        self.assertEqual(quote['current_total']['line'],2.5)
        self.assertEqual(quote['current_total']['over'],2.0)
        self.assertEqual(quote['current_total']['under'],2.0)
        self.assertAlmostEqual(quote['latest_h2h']['draw'],1+100/105)
        self.assertNotEqual(quote['latest_h2h']['draw'],round(1+100/105,6))

    def test_freeze_verify_checks_canonical_record_and_detects_change(self):
        body={'revision':1,'evaluation_slot':'FIRST_PREMATCH','fixture':{'kickoff_at':'2026-11-08T12:00:00Z'}}
        record={**body,'record_sha256':hashlib.sha256(json.dumps(body,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()}
        self.assertIs(verify(record),record)
        record['revision']=2
        with self.assertRaises(ValueError): verify(record)

if __name__=='__main__':
    unittest.main()
