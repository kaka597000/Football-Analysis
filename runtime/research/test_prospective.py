import copy,json,tempfile,unittest
from pathlib import Path
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
import prospective as p
class LedgerTest(unittest.TestCase):
 def bundle(self):
  now=datetime.now(timezone.utc);fixture={'match_id':'synthetic-software-test','cohort':'eng.1','cutoff_at':(now-timedelta(seconds=2)).isoformat(),'kickoff_at':(now+timedelta(hours=1)).isoformat()}
  row={'version':'synthetic-only','input':fixture,'input_hash':p.digest(fixture),'generated_at':(now-timedelta(seconds=1)).isoformat(),'primary':'1-0','matrix':[[.1,.2],[.5,.2]],'matrix_hash':p.digest([[.1,.2],[.5,.2]])}
  return {'match_id':fixture['match_id'],'current_model':row,'test_model':copy.deepcopy(row),'upset_candidate':{'status':'ABSTAIN_MISSING_1X2_QUOTE'}}
 def test_no_overwrite_and_detect_tamper(self):
  b=self.bundle()
  with tempfile.TemporaryDirectory() as d:
   path=Path(p.save(b,d))
   with self.assertRaises(FileExistsError):p.save(b,d)
   r=json.loads(path.read_text());r['bundle']['current_model']['primary']='9-9';path.write_text(json.dumps(r))
   with self.assertRaises(ValueError):p.review(d,[])
 def test_late_record_rejected(self):
  b=self.bundle();b['current_model']['input']['kickoff_at']=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat();b['test_model']['input']=copy.deepcopy(b['current_model']['input'])
  with tempfile.TemporaryDirectory() as d:
   with self.assertRaises(ValueError):p.save(b,d)
 def test_pair_mismatch_rejected(self):
  b=self.bundle();b['test_model']['input']['match_id']='other'
  with tempfile.TemporaryDirectory() as d:
   with self.assertRaises(ValueError):p.save(b,d)
 def test_matrix_or_input_hash_rejected(self):
  for k in ('matrix_hash','input_hash'):
   b=self.bundle();b['test_model'][k]='invalid'
   with tempfile.TemporaryDirectory() as d:
    with self.assertRaises(ValueError):p.save(b,d)
 def test_pending_denominator_and_no_formal_freeze(self):
  b=self.bundle()
  with tempfile.TemporaryDirectory() as d:
   p.save(b,d);r=p.review(d,[])
  self.assertEqual(r['paired_settled'],0);self.assertEqual(r['pending'],['synthetic-software-test']);self.assertIsNone(r['current_top1_rate']);self.assertEqual(r['formal_kpi_added'],0)
 def test_main12_missing_optional_input_hash_is_computed(self):
  b=self.bundle();b['current_model'].pop('input_hash');b['test_model'].pop('input_hash')
  with tempfile.TemporaryDirectory() as d:
   path=Path(p.save(b,d));r=json.loads(path.read_text());self.assertEqual(r['input_sha256'],p.digest(b['current_model']['input']));self.assertEqual(p.review(d,[])['paired_settled'],0)
if __name__=='__main__':unittest.main()
