import copy,unittest
import test_ensemble
from ensemble_guarded import optimize

class GuardTests(unittest.TestCase):
 def test_end_labels_do_not_change_selected_policy(self):
  rows=test_ensemble.Tests().rows();a=optimize(rows);changed=copy.deepcopy(rows)
  for r in changed:
   if r['date']>=a['split']['temperature_fit_before']:r['home_goals']=1-r['home_goals']
  b=optimize(changed)
  self.assertEqual(a['selected'],b['selected']);self.assertEqual(a['static_weights'],b['static_weights'])
 def test_baseline_fallback_and_normalized_weights(self):
  a=optimize(test_ensemble.Tests().rows())
  self.assertTrue(any(c['kind']=='baseline' and c['eligible'] for c in a['calibration_candidates']))
  self.assertAlmostEqual(sum(a['static_weights']),1.)
  self.assertEqual(a['promotion'],'DO_NOT_PROMOTE')
if __name__=='__main__':unittest.main()
