import copy,hashlib,tempfile,unittest
from pathlib import Path
from datetime import datetime,timedelta,timezone
from research_program.market_expert import baseline,paired_wdl
class MarketTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);(self.root/'raw').write_bytes(b'SYNTHETIC')
  self.now=datetime.now(timezone.utc);self.target={'match_id':'target','cohort':'eng.1','stage':'regular','cutoff_at':self.now.isoformat(),'kickoff_at':(self.now+timedelta(hours=1)).isoformat()}
 def row(self,market='h2h',line=None,seconds=10):
  t=(self.now-timedelta(seconds=seconds)).isoformat()
  return {'provider':'SYNTHETIC','cohort':'eng.1','stage':'regular','match_id':'target','entity_id':'A','field':'market_quote','period':'regulation_90','event_kickoff_at':self.target['kickoff_at'],'observed_at':t,'evidence':{'url':'https://example.test/odds','file':'raw','sha256':hashlib.sha256((self.root/'raw').read_bytes()).hexdigest(),'query_time':t,'identity_verified':True},'value':{'quoted_at':t,'bookmaker':'BOOK','market':market,'line':line,'odds':{'home':2.,'draw':3.,'away':4.} if market=='h2h' else {'over':1.9,'under':2.}}}
 def build(self,rows):return baseline(rows,self.target,self.root,bookmaker='BOOK',now=self.now)
 def test_devig_and_cross_line_quotes_not_combined(self):
  r=self.build([self.row(),self.row('totals',2.5),self.row('totals',2.75)])
  self.assertAlmostEqual(sum(r['wdl']['probabilities']),1.);self.assertEqual(len(r['comparisons']),3);self.assertIsNone(r['score_matrix']);self.assertFalse(r['flow_or_volume_claimed'])
 def test_stale_future_and_conflicting_quotes_rejected(self):
  self.assertIsNone(self.build([self.row(seconds=120)])['wdl'])
  late=self.row(seconds=-1)
  with self.assertRaises(ValueError):self.build([late])
  a=self.row();b=copy.deepcopy(a);b['value']['odds']['home']=2.1
  with self.assertRaises(ValueError):self.build([a,b])
 def test_comparison_preserves_full_denominator(self):
  r=paired_wdl([{'match_id':'a','market_wdl':[.7,.2,.1],'current_wdl':[.2,.5,.3],'actual_wdl':0},{'match_id':'b'}])
  self.assertEqual(r['population'],2);self.assertEqual(r['paired_n'],1);self.assertEqual(r['coverage'],.5);self.assertEqual(r['net_wdl_hits_market_vs_current'],1)
if __name__=='__main__':unittest.main()
