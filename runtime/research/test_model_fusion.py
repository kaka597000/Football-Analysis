import copy,unittest
from datetime import datetime,timedelta,timezone
from fuse_models import fuse,object_sha256

class FusionTests(unittest.TestCase):
 def pair(self):
  now=datetime.now(timezone.utc)
  f={'match_id':'SYNTHETIC_ONLY','cohort':'eng.1','identity_verified':True,'phase':'PreMatch',
     'cutoff_at':(now-timedelta(seconds=10)).isoformat(),'kickoff_at':(now+timedelta(days=1)).isoformat()}
  def model(m):return {'input':copy.deepcopy(f),'matrix':m,'matrix_hash':object_sha256(m),
    'generated_at':(now-timedelta(seconds=1)).isoformat(),'version':'TEST_ONLY','primary':'0-0'}
  return {'match_id':f['match_id'],'current_model':model([[.6,.4]]),'test_model':model([[.2],[.8]]),
   'execution_audit':{k:{'artifact_sha256':c*64,'engine':'SYNTHETIC_ONLY'} for k,c in [('current','a'),('test','b')]}}
 def test_zero_padding_arithmetic_and_original_preservation(self):
  p=self.pair();old=copy.deepcopy(p);r=fuse(p)
  self.assertEqual(r['matrix'],[[.4,.2],[.4,0.]])
  self.assertAlmostEqual(sum(r['wdl'].values()),1.)
  self.assertEqual(p,old);self.assertFalse(r['trained_fusion']);self.assertIsNone(r['total'])
 def test_wrong_cutoff_identity_or_matrix_tampering_rejected(self):
  for mode in ('input','hash','artifact','late','identity'):
   p=self.pair()
   if mode=='input':p['test_model']['input']['cohort']='other'
   elif mode=='hash':p['test_model']['matrix'][0][0]=.3
   elif mode=='artifact':p['execution_audit']['test']['artifact_sha256']='a'*64
   elif mode=='identity':p['current_model']['input']['identity_verified']=False;p['test_model']['input']['identity_verified']=False
   else:
    t=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()
    for k in ('current_model','test_model'):p[k]['input']['kickoff_at']=t
   with self.subTest(mode=mode),self.assertRaises(ValueError):fuse(p)
 def test_missing_quote_time_not_silently_used(self):
  p=self.pair()
  for k in ('current_model','test_model'):p[k]['input']['total_quote']={'line':2.5}
  self.assertIsNone(fuse(p)['total'])
 def test_refused_expert_cannot_be_revived_by_residual_matrix(self):
  for field in ('status','recommendation_status'):
   for status in ('ABSTAIN','ABSTAIN_UNSUPPORTED','REJECTED','BLOCKED','ERROR','UNAVAILABLE','NOT_EXECUTED'):
    p=self.pair();p['test_model'][field]=status
    with self.subTest(field=field,status=status),self.assertRaisesRegex(ValueError,'refused'):
     fuse(p)
  p=self.pair();p['test_model']['recommendation_status']='OBSERVE'
  self.assertEqual(fuse(p)['status'],'EXPERIMENTAL_NOT_PROMOTED')
 def test_overflow_and_wrong_score_semantics_rejected(self):
  for overflow in (.01,-.01,float('nan'),float('inf'),True,'0'):
   p=self.pair();p['test_model']['overflow']=overflow
   with self.subTest(overflow=overflow),self.assertRaisesRegex(ValueError,'overflow'):fuse(p)
  for field,value in [('score_basis','including_extra_time'),('matrix_axes',['away_goals','home_goals'])]:
   p=self.pair();p['test_model'][field]=value
   with self.subTest(field=field),self.assertRaises(ValueError):fuse(p)
  p=self.pair()
  for key in ('current_model','test_model'):p[key]['input']['score_basis']='penalty_shootout'
  with self.assertRaisesRegex(ValueError,'regulation-90'):fuse(p)
 def test_later_expert_artifact_rejected_even_with_old_generation_claim(self):
  for field in ('model_observed_at','model_built_at'):
   p=self.pair();p['test_model'][field]=p['test_model']['generated_at']
   with self.subTest(field=field),self.assertRaisesRegex(ValueError,'after cutoff'):fuse(p)
   p['test_model'][field]=p['test_model']['input']['cutoff_at']
   self.assertIsNotNone(fuse(p)['matrix'])
 def test_rectangular_support_settlement_and_probability_meaning(self):
  p=self.pair();q={'line':.75,'match_id':p['match_id'],'verified':True,
      'bookmaker':'SYNTHETIC_ONLY','market':'standard_total','source_url':'https://example.org/odds',
      'quoted_at':p['current_model']['input']['cutoff_at'],'query_time':p['current_model']['input']['cutoff_at']}
  for k in ('current_model','test_model'):
   p[k]['matrix']=[[.2,.8]];p[k]['matrix_hash']=object_sha256(p[k]['matrix']);p[k]['input']['total_quote']=copy.deepcopy(q)
  original=copy.deepcopy(p);r=fuse(p)
  self.assertEqual(r['matrix'],[[.2,.8]])
  self.assertAlmostEqual(r['total']['over']['half_win'],.8)
  self.assertAlmostEqual(r['total']['over']['loss'],.2)
  self.assertAlmostEqual(r['total']['under']['half_loss'],.8)
  self.assertAlmostEqual(r['total']['under']['win'],.2)
  self.assertEqual(r['wdl'],{'主胜':0.,'平局':.2,'客胜':.8})
  self.assertEqual(p,original)
 def test_similarity_uses_matrix_modes_not_stale_labels(self):
  p=self.pair() # Both labels say 0-0 but matrix modes differ.
  self.assertFalse(fuse(p)['expert_similarity']['same_statistical_primary'])
if __name__=='__main__':unittest.main()
