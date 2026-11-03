"""Synthetic software packages; none are football performance evidence."""
import copy,unittest
from datetime import datetime,timedelta,timezone
from research.analysis_freeze import digest
from research_program.promotion_review import assess

def seal(record):
 record.pop('record_sha256',None);record['record_sha256']=digest(record);return record

class GateTests(unittest.TestCase):
 def plan(self):
  p={'cohort':'eng.1','stage':'regular','eligible_match_ids':['future-a'],'minimum_sample':30,'alpha':.05,
     'current_artifact_sha256':'a'*64,'candidate_artifact_sha256':'b'*64,'minimum_primary_coverage':1.,
     'quality_tolerances':{k:0. for k in ('score_log_loss','score_brier','wdl_brier','wdl_rps')},'tail_brier_tolerance':0.}
  return seal({'track':'research-preregistration','recorded_at':datetime.now(timezone.utc).isoformat(),'payload':p})
 def package(self):
  now=datetime.now(timezone.utc);kickoff=now-timedelta(days=2);cutoff=kickoff-timedelta(hours=1)
  plan=self.plan();plan['recorded_at']=(now-timedelta(days=4)).isoformat()
  plan['payload'].update(eligible_match_ids=[str(i) for i in range(30)],current_version='BASE',candidate_version='CAND',ece_tolerance=0.)
  seal(plan);rows=[]
  for i in range(30):
   f={'match_id':str(i),'cohort':'eng.1','stage':'regular','identity_verified':True,'gender':'men','age':'senior','home':'A'+str(i),'away':'B'+str(i),'cutoff_at':cutoff.isoformat(),'kickoff_at':kickoff.isoformat()}
   def record(role,matrix,primary):
    p={'input':copy.deepcopy(f),'input_sha256':digest(f),'version':'BASE' if role=='current' else 'CAND','generated_at':(cutoff+timedelta(seconds=10)).isoformat(),'matrix':matrix,'matrix_hash':digest(matrix),'primary':primary,'artifact_sha256':'b'*64}
    if role=='current':p={'current_model':p,'execution_audit':{'current':{'artifact_sha256':'a'*64},'fixture_sha256':digest(f)}}
    return seal({'track':'current-test-pair' if role=='current' else 'challenger-independent','fixture':copy.deepcopy(f),'stage':'prematch','evaluation_slot':'FIRST_PREMATCH','recorded_at':(cutoff+timedelta(seconds=20)).isoformat(),'payload':p})
   rows.append({'match_id':str(i),'current_record':record('current',[[1.,0.],[0.,0.]],'0-0'),'candidate_record':record('candidate',[[0.,0.],[1.,0.]],'1-0'),'result':{'status':'completed','match_id':str(i),'home_goals':1,'away_goals':0,'score_basis':'regulation_90','source_url':'https://example.test/synthetic','observed_at':now.isoformat()}})
  receipts=[seal({'track':track,'stage':'review','recorded_at':now.isoformat(),'payload':{'plan_record_sha256':plan['record_sha256'],'validation_ids_sha256':digest(sorted(plan['payload']['eligible_match_ids'])),'decision':'PASSED','cohort':'eng.1','stage':'regular'}}) for track in ('actual-line-validation-audit','asof-source-validation-audit')]
  return plan,rows,receipts
 def test_no_registration_or_missing_population_cannot_promote(self):
  self.assertEqual(assess(None,[])['status'],'BLOCKED')
  r=assess(self.plan(),[]);self.assertFalse(r['automatic_promotion']);self.assertIn('INCOMPLETE_PREDECLARED_POPULATION',r['reasons'])
 def test_valid_package_only_reaches_review_with_separate_post_audits(self):
  plan,rows,receipts=self.package();before=copy.deepcopy(plan)
  self.assertEqual(assess(plan,rows)['status'],'BLOCKED')
  report=assess(plan,rows,receipts);self.assertEqual(report['status'],'READY_FOR_INDEPENDENT_REVIEW');self.assertFalse(report['automatic_promotion']);self.assertEqual(plan,before)
  plan['recorded_at']=datetime.now(timezone.utc).isoformat();seal(plan)
  with self.assertRaises(ValueError):assess(plan,rows,receipts)
 def test_actual_inputs_and_provided_hashes_are_verified(self):
  for key,value in [('input',{'match_id':'different'}),('input_sha256','e'*64)]:
   plan,rows,receipts=self.package();rows[0]['candidate_record']['payload'][key]=value;seal(rows[0]['candidate_record'])
   with self.assertRaises(ValueError):assess(plan,rows,receipts)
 def test_timezone_aliases_do_not_inflate_independent_samples(self):
  plan,rows,receipts=self.package();other=copy.deepcopy(rows[0]);other['match_id']='1';other['result']['match_id']='1'
  for role in ('current','candidate'):
   rec=other[role+'_record'];f=rec['fixture'];f['match_id']='1';f['kickoff_at']=datetime.fromisoformat(f['kickoff_at']).astimezone(timezone(timedelta(hours=8))).isoformat()
   p=rec['payload']['current_model'] if role=='current' else rec['payload'];p['input']=copy.deepcopy(f);p['input_sha256']=digest(f)
   if role=='current':rec['payload']['execution_audit']['fixture_sha256']=digest(f)
   seal(rec)
  rows[1]=other
  with self.assertRaises(ValueError):assess(plan,rows,receipts)
 def test_actual_primary_calibration_is_not_matrix_mode_calibration(self):
  plan,rows,receipts=self.package();matrix=[[.5,0.],[.4,.1]]
  for row in rows:
   for role in ('current','candidate'):
    rec=row[role+'_record'];p=rec['payload']['current_model'] if role=='current' else rec['payload'];p['matrix']=matrix;p['matrix_hash']=digest(matrix);seal(rec)
  report=assess(plan,rows,receipts)
  self.assertIn('PRIMARY_CONFIDENCE_CALIBRATION_REGRESSION',report['reasons'])
  self.assertAlmostEqual(report['actual_primary_calibration']['candidate']['ece'],.6)
 def test_duplicate_universe_cannot_approve(self):
  p=self.plan();p['payload']['eligible_match_ids']*=2;seal(p)
  with self.assertRaises(ValueError):assess(p,[])
if __name__=='__main__':unittest.main()
