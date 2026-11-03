import copy,hashlib,tempfile,unittest
from pathlib import Path
from research_program.replay import snapshot,save_snapshot,eligible_labels
from datetime import datetime,timedelta,timezone
class ReplayTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  (self.root/'raw.json').write_bytes(b'{"synthetic":true}')
  self.now=datetime(2026,11,3,tzinfo=timezone.utc)
  self.target={'match_id':'target','cohort':'eng.1','stage':'regular','home_id':'A','away_id':'B','cutoff_at':(self.now-timedelta(days=10)).isoformat(),'kickoff_at':(self.now-timedelta(days=9)).isoformat()}
  captured=self.now-timedelta(days=11)
  self.row={'provider':'SYNTHETIC','cohort':'eng.1','stage':'regular','match_id':'past','entity_id':'A','entity_side':'home','field':'xg','value':1.2,'period':'regulation_90','event_status':'FT','event_kickoff_at':(self.now-timedelta(days=12)).isoformat(),'observed_at':captured.isoformat(),'first_known_at':None,'evidence':{'url':'https://example.test/data','file':'raw.json','sha256':hashlib.sha256((self.root/'raw.json').read_bytes()).hexdigest(),'query_time':captured.isoformat(),'identity_verified':True}}
 def build(self,rows):return snapshot(rows,self.target,self.root,provider='SYNTHETIC',now=self.now)
 def test_replay_uses_actual_old_capture_and_missing_not_zero(self):
  r=self.build([self.row]);self.assertEqual(r['features']['A']['xg']['mean'],1.2);self.assertIsNone(r['features']['B']['xg']['mean']);self.assertFalse(r['claims']['source_first_known_inferred'])
  self.assertEqual(save_snapshot(r,self.root/'snapshots'),save_snapshot(r,self.root/'snapshots'))
 def test_backfill_does_not_enter_old_snapshot(self):
  row=copy.deepcopy(self.row);row['observed_at']=row['evidence']['query_time']=self.now.isoformat();row['first_known_at']=self.row['observed_at']
  self.assertEqual(self.build([row])['excluded']['captured_after_cutoff'],1)
 def test_phase_unknown_target_and_future_events_excluded(self):
  for field,value in [('stage',None),('stage','playoffs'),('match_id','target')]:
   row=dict(self.row,**{field:value});self.assertEqual(self.build([row])['features']['A']['xg']['n'],0)
 def test_label_capture_after_cutoff_does_not_train(self):
  row={'match_id':'past','cohort':'eng.1','stage':'regular','score_basis':'regulation_90','kickoff_at':self.row['event_kickoff_at'],'observed_at':self.now.isoformat(),'home_goals':1,'away_goals':0}
  self.assertFalse(eligible_labels([row],self.target)[0])
if __name__=='__main__':unittest.main()
