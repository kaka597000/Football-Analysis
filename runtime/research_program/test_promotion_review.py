import unittest
from datetime import datetime,timedelta,timezone
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
 def test_valid_synthetic_package_only_reaches_review_and_late_plan_rejected(self):
  # Synthetic software fixture, never evidence of football performance.
  now=datetime.now(timezone.utc);kickoff=now-timedelta(days=2);cutoff=kickoff-timedelta(hours=1)
  def seal(record):record['record_sha256']=digest(record);return record
  plan=self.plan();plan.pop('record_sha256');plan['recorded_at']=(now-timedelta(days=4)).isoformat()
  plan['payload'].update(eligible_match_ids=[str(i) for i in range(30)],current_version='BASE',candidate_version='CAND',ece_tolerance=0.,actual_line_audit_receipt_sha256='c'*64,asof_source_audit_receipt_sha256='d'*64)
  plan=seal(plan);rows=[]
  for i in range(30):
   f={'match_id':str(i),'cohort':'eng.1','stage':'regular','identity_verified':True,'gender':'men','age':'senior','home':'A'+str(i),'away':'B'+str(i),'cutoff_at':cutoff.isoformat(),'kickoff_at':kickoff.isoformat()}
   def record(role,matrix,primary):
    p={'version':'BASE' if role=='current' else 'CAND','generated_at':(cutoff+timedelta(seconds=10)).isoformat(),'matrix':matrix,'matrix_hash':digest(matrix),'primary':primary,'artifact_sha256':'b'*64}
    if role=='current':p={'current_model':p,'execution_audit':{'current':{'artifact_sha256':'a'*64}}}
    return seal({'track':'current-test-pair' if role=='current' else 'challenger-independent','fixture':f,'stage':'prematch','evaluation_slot':'FIRST_PREMATCH','recorded_at':(cutoff+timedelta(seconds=20)).isoformat(),'payload':p})
   rows.append({'match_id':str(i),'current_record':record('current',[[1.,0.],[0.,0.]],'0-0'),'candidate_record':record('candidate',[[0.,0.],[1.,0.]],'1-0'),'result':{'status':'completed','match_id':str(i),'home_goals':1,'away_goals':0,'score_basis':'regulation_90','source_url':'https://example.test/synthetic','observed_at':now.isoformat()}})
  report=assess(plan,rows);self.assertEqual(report['status'],'READY_FOR_INDEPENDENT_REVIEW');self.assertFalse(report['automatic_promotion']);self.assertFalse(report['activation_modified'])
  plan.pop('record_sha256');plan['recorded_at']=now.isoformat();seal(plan)
  with self.assertRaises(ValueError):assess(plan,rows)

if __name__=='__main__':unittest.main()
