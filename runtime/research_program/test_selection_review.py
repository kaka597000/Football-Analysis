import copy,unittest
from datetime import datetime,timedelta,timezone
from research.analysis_freeze import digest
from research_program.selection_review import compare
class SelectionTests(unittest.TestCase):
 def data(self):
  now=datetime.now(timezone.utc);kickoff=now-timedelta(days=1);t=(kickoff-timedelta(hours=1)).isoformat()
  fixture={'match_id':'SYNTHETIC','cohort':'eng.1','cutoff_at':t,'kickoff_at':kickoff.isoformat()}
  def record(payload,track):
   r={'fixture':fixture,'match_id':fixture['match_id'],'stage':'prematch','track':track,'freeze_id':track+'-synthetic','recorded_at':t,'evaluation_slot':'FIRST_PREMATCH','payload':payload}
   r['record_sha256']=digest(r);return r
  m=[[.1,.1],[.6,.2]];prediction={'matrix':m,'matrix_hash':digest(m),'primary':'1-0','version':'SYNTHETIC'}
  pair=record({'current_model':prediction,'test_model':prediction},'current-test-pair')
  comp=record({'primary':'1-1','current_test_reference':{'freeze_id':pair['freeze_id'],'sha256':pair['record_sha256']}},'comprehensive')
  result={'match_id':'SYNTHETIC','home_goals':1,'away_goals':1,'score_basis':'regulation_90','source_url':'https://example.test/result','observed_at':now.isoformat()}
  return {'pair':pair,'comprehensive':comp},result
 def test_net_hit_and_pending_denominator(self):
  entry,result=self.data();r=compare([entry],[result]);self.assertEqual(r['by_model_version']['current_model:SYNTHETIC']['net_hits'],1)
  r=compare([entry],[]);self.assertEqual(len(r['pending']),1);self.assertEqual(r['population'],1)
 def test_reference_tampering_or_revision_selection_rejected(self):
  entry,result=self.data();entry['comprehensive']['evaluation_slot']='SEPARATE_REVISION_OR_STAGE';body={k:v for k,v in entry['comprehensive'].items() if k!='record_sha256'};entry['comprehensive']['record_sha256']=digest(body)
  with self.assertRaises(ValueError):compare([entry],[result])
 def test_duplicate_matches_do_not_double_count(self):
  entry,result=self.data()
  with self.assertRaises(ValueError):compare([entry,entry],[result])
if __name__=='__main__':unittest.main()
