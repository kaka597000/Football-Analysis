import copy
import math
import unittest
import numpy as np
from research_program.xg_native_scale import fit

class NativeScaleTests(unittest.TestCase):
    def setUp(self):
        self.protocol={'window_matches':10,'max_xg_age_days':90,'ridge_alpha':.1,'max_iter':500,'tol':1e-9}
        self.names={'fresh':['is_home','own_xg','own_missing_fraction_prior_goal_window','own_observed_count','own_effective_n','own_latest_age_days','own_missing','own_residual_missing']}
        self.rows=[{'features':{'fresh':[[1.,1.+i*.1,i%2*.001,10.,9.,2.,0.,None],[0.,.8+i*.05,0.,9.,8.,3.,0.,None]]},'actual_score':[i%3,(i+1)%3]} for i in range(12)]
    def test_bounded_features_have_physical_scales(self):
        a=fit(self.rows,'fresh',self.protocol,self.names)
        self.assertEqual(a['scale'][2:],[1.,10.,10.,90.,1.,1.])
        self.assertLess(abs((.8-a['mean'][2])/a['scale'][2]),1.)
    def test_goal_role_and_continuous_xg_keep_training_scaling(self):
        a=fit(self.rows,'fresh',self.protocol,self.names)
        x=np.asarray([v[:2] for r in self.rows for v in r['features']['fresh']]);np.testing.assert_allclose(a['scale'][:2],x.std(0))
    def test_missing_columns_are_explicit_and_inputs_unchanged(self):
        old=copy.deepcopy(self.rows);a=fit(self.rows,'fresh',self.protocol,self.names)
        self.assertEqual(a['unlearned_columns'],[7]);self.assertEqual(self.rows,old);self.assertFalse(a['post_prediction_rate_clipping'])
    def test_unknown_feature_schema_rejected(self):
        with self.assertRaises(ValueError):fit(self.rows,'fresh',self.protocol,{'fresh':['wrong']})
    def test_training_is_deterministic(self):
        a=fit(self.rows,'fresh',self.protocol,self.names);b=fit(self.rows,'fresh',self.protocol,self.names)
        self.assertEqual(a,b);self.assertTrue(all(math.isfinite(x) for x in a['coef']))

if __name__=='__main__':unittest.main()
