import unittest,copy,json,sys,tempfile
from pathlib import Path
from datetime import datetime,timedelta,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from upset_v2 import predict as m
class PredictionTest(unittest.TestCase):
 def fixture(self):
  now=datetime.now(timezone.utc);cut=now.isoformat()
  return {'match_id':'synthetic-software-test-only','cohort':'esp.1','home':'A','away':'B','cutoff_at':cut,'kickoff_at':(now+timedelta(days=1)).isoformat(),'neutral':False,'wdl_quote':{'match_id':'synthetic-software-test-only','market':'1x2','verified':True,'bookmaker':'synthetic-only','source_url':'https://example.test/software-test','query_time':cut,'home_odds':1.5,'draw_odds':4.,'away_odds':7.}}
 def test_local_artifact_inference_and_input_isolation(self):
  f=self.fixture();before=copy.deepcopy(f);r=m.run(f);self.assertEqual(f,before);self.assertEqual(r['status'],'CANDIDATE_SHADOW_ONLY');self.assertAlmostEqual(sum(r['probabilities'].values()),1);self.assertFalse(r['main_score_reordered']);self.assertFalse(r['acceptance_gate_passed']);self.assertEqual(r['risk_label'],'UNVALIDATED_NO_ACTION_ALERT')
 def test_missing_and_untrained(self):
  f=self.fixture();f.pop('wdl_quote');self.assertEqual(m.run(f)['status'],'ABSTAIN_MISSING_1X2_QUOTE');f['cohort']='afc.champions';self.assertEqual(m.run(f)['status'],'ABSTAIN_NO_TRAINED_COHORT')
 def test_future_quote_or_bad_hash_rejected(self):
  f=self.fixture();f['wdl_quote']['query_time']=(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat()
  with self.assertRaises(ValueError):m.run(f)
  f=self.fixture();reg=json.loads(Path('/workspace/WCPP/runtime/upset-candidates.json').read_text());reg['models']['esp.1']['artifact_sha256']='invalid'
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'reg.json';p.write_text(json.dumps(reg))
   with self.assertRaises(ValueError):m.run(f,str(p))
if __name__=='__main__':unittest.main()
