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
if __name__=='__main__':unittest.main()
