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

 def historical_bundle(self):
  now=datetime.now(timezone.utc);kickoff=now-timedelta(hours=3);recorded=kickoff-timedelta(minutes=30)
  b=self.bundle();fixture=b['current_model']['input']
  fixture.update(kickoff_at=kickoff.isoformat(),cutoff_at=(recorded-timedelta(minutes=1)).isoformat())
  for key in ('current_model','test_model'):
   b[key].update(input=copy.deepcopy(fixture),input_hash=p.digest(fixture),generated_at=(recorded-timedelta(seconds=1)).isoformat())
  return b,recorded,{'match_id':b['match_id'],'event_status':'FT','score_basis':'regulation_90',
                    'home_goals':1,'away_goals':0,'source_url':'https://example.test/result','observed_at':now.isoformat()}

 def test_interrupted_write_leaves_no_record_and_allows_retry(self):
  import publication
  b=self.bundle()
  with tempfile.TemporaryDirectory() as d:
   with patch.object(publication.os,'fsync',side_effect=OSError('synthetic interruption')):
    with self.assertRaises(OSError):p.save(b,d)
   self.assertEqual(list(Path(d).glob('*.json')),[])
   self.assertEqual(list(Path(d).glob('.delivery-*')),[])
   saved=Path(p.save(b,d));self.assertEqual(json.loads(saved.read_text())['bundle'],b)

 def test_commit_past_kickoff_cannot_publish(self):
  b=self.bundle();kickoff=datetime.fromisoformat(b['current_model']['input']['kickoff_at'])
  early,late=kickoff-timedelta(seconds=1),kickoff+timedelta(seconds=1)
  for times in ([early,late],[early,early,late]):
   with self.subTest(times=times),tempfile.TemporaryDirectory() as d:
    with patch.object(p,'datetime') as clock:
     clock.now.side_effect=times
     with self.assertRaisesRegex(ValueError,'before kickoff'):p.save(b,d)
    self.assertEqual(list(Path(d).glob('*.json')),[])
    self.assertEqual(list(Path(d).glob('.delivery-*')),[])

 def test_concurrent_same_match_keeps_one_complete_original(self):
  from concurrent.futures import ThreadPoolExecutor
  b=self.bundle()
  with tempfile.TemporaryDirectory() as d:
   with ThreadPoolExecutor(max_workers=2) as pool:
    futures=[pool.submit(p.save,copy.deepcopy(b),d) for _ in range(2)]
    results=[]
    for future in futures:
     try:results.append(future.result(timeout=10))
     except FileExistsError:results.append(None)
   self.assertEqual(sum(value is not None for value in results),1)
   files=list(Path(d).glob('*.json'));self.assertEqual(len(files),1)
   self.assertEqual(json.loads(files[0].read_text())['bundle'],b)
   self.assertEqual(list(Path(d).glob('.delivery-*')),[])

 def test_live_or_interrupted_result_remains_pending(self):
  b,recorded,result=self.historical_bundle()
  with tempfile.TemporaryDirectory() as d:
   with patch.object(p,'datetime') as clock:
    clock.now.return_value=recorded;p.save(b,d)
   for status in ('LIVE','HT','SUSPENDED','POSTPONED'):
    with self.subTest(status=status):
     report=p.review(d,[dict(result,event_status=status)])
     self.assertEqual(report['paired_settled'],0)
     self.assertEqual(report['pending'],[b['match_id']])
     self.assertEqual(report['current_top1_hits'],0)
   self.assertEqual(p.review(d,[result])['paired_settled'],1)

 def test_missing_or_conflicting_result_status_is_rejected(self):
  b,recorded,result=self.historical_bundle()
  with tempfile.TemporaryDirectory() as d:
   with patch.object(p,'datetime') as clock:
    clock.now.return_value=recorded;p.save(b,d)
   for value in ({k:v for k,v in result.items() if k!='event_status'},
                 dict(result,status='LIVE'),dict(result,event_status='LIVE',status='completed')):
    with self.subTest(value=value),self.assertRaisesRegex(ValueError,'result status'):p.review(d,[value])
if __name__=='__main__':unittest.main()
