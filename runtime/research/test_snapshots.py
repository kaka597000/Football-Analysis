import unittest,tempfile
from datetime import datetime,timedelta,timezone
import snapshots as s
class Tests(unittest.TestCase):
 def value(self):
  now=datetime.now(timezone.utc);t=now.isoformat()
  return {'match_id':'synthetic-test','cohort':'eng.1','sex':'male','age_band':'senior','competition_type':'league','phase':'regular','kickoff_at':(now+timedelta(hours=2)).isoformat(),'captured_at':t,'sources':[{'url':'https://example.test/test-only','query_time':t}],'facts':{},'missing_fields':['lineup','injuries']}
 def test_capture_and_no_overwrite(self):
  v=self.value()
  with tempfile.TemporaryDirectory() as d:
   s.record(v,d)
   with self.assertRaises(FileExistsError):s.record(v,d)
 def test_future_evidence_rejected(self):
  v=self.value();v['sources'][0]['query_time']=v['kickoff_at']
  with tempfile.TemporaryDirectory() as d:
   with self.assertRaises(ValueError):s.record(v,d)
 def test_secret_fields_rejected(self):
  v=self.value();v['facts']['api_key']='synthetic-never-a-secret'
  with tempfile.TemporaryDirectory() as d:
   with self.assertRaises(ValueError):s.record(v,d)
if __name__=='__main__':unittest.main()
