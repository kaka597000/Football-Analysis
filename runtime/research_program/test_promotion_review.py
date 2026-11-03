import unittest
from datetime import datetime,timezone
from research.analysis_freeze import digest
from research_program.promotion_review import assess
class GateTests(unittest.TestCase):
 def plan(self):
  p={'cohort':'eng.1','stage':'regular','eligible_match_ids':['future-a'],'minimum_sample':30,'alpha':.05,'current_artifact_sha256':'a'*64,'candidate_artifact_sha256':'b'*64,
     'minimum_primary_coverage':1.,'quality_tolerances':{k:0. for k in ('score_log_loss','score_brier','wdl_brier','wdl_rps')},'tail_brier_tolerance':0.}
  r={'track':'research-preregistration','recorded_at':datetime.now(timezone.utc).isoformat(),'payload':p};r['record_sha256']=digest(r);return r
 def test_no_registration_or_missing_population_cannot_promote(self):
  self.assertEqual(assess(None,[])['status'],'BLOCKED')
  r=assess(self.plan(),[]);self.assertFalse(r['automatic_promotion']);self.assertIn('INCOMPLETE_PREDECLARED_POPULATION',r['reasons']);self.assertIn('INDEPENDENT_SOURCE_ASOF_AUDIT_REQUIRED',r['reasons'])
 def test_duplicate_universe_cannot_approve(self):
  p=self.plan();p['payload']['eligible_match_ids']*=2;p['record_sha256']=digest({k:v for k,v in p.items() if k!='record_sha256'})
  with self.assertRaises(ValueError):assess(p,[])
if __name__=='__main__':unittest.main()
