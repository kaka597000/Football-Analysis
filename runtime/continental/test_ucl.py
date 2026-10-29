import copy,json,math,sys,unittest
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'continental'));import ucl_ml as ml
import train_queues as q
class Tests(unittest.TestCase):
 def rows(self):
  r=json.loads(Path('/workspace/WCPP/reports/ucl-onboarding-20261013/training-new-format-league_phase.json').read_text());return r if isinstance(r,list) else r['rows']
 def test_partial_process_preserves_real_sot_and_possession(self):
  r=self.rows();target=r[160];past=[x for x in r if x['date']<target['date']];f=ml.side_features(past,target['home'],target['date'],True)
  self.assertTrue(math.isfinite(f[-4]));self.assertTrue(math.isnan(f[-3]));self.assertTrue(math.isfinite(f[-2]));self.assertGreater(f[-1],0);self.assertLessEqual(f[-1],2/3)
 def test_target_or_same_date_process_not_used(self):
  rows=self.rows();target=rows[160];rate=(1.4,1.2);a=ml.feature_matrix(rows,target,rate);changed=copy.deepcopy(rows)
  for r in changed:
   if r['date']>=target['date']:
    r['home_goals']=20;r['process']={'home':{'shots_on_target':99,'shots_off_target':99,'possession':99},'away':{'shots_on_target':99,'shots_off_target':99,'possession':99}}
  np.testing.assert_equal(a,ml.feature_matrix(changed,target,rate))
 def test_mixed_eras_stages_rejected(self):
  rows=self.rows();rows[1]['stage']='LEGACY_GROUP:group_stage'
  with self.assertRaises(ValueError):q.validate(rows)
 def test_neutral_unknown_rejected(self):
  rows=self.rows();rows[0]['neutral']=None
  with self.assertRaises(ValueError):q.validate(rows)
 def test_invalid_observed_process_rejected(self):
  rows=self.rows();row=rows[20];row['process']['home']['possession']=101
  with self.assertRaises(ValueError):ml.side_features(rows[:21],row['home'],'2024-10-10',True)
 def test_bad_version_cannot_load_as_ucl(self):
  with self.assertRaises(ValueError):ml.predict({'version':'WCPP-ML-0.1'},{},{})
if __name__=='__main__':unittest.main()
