import copy
import json
import math
import unittest
import numpy as np
from score_concentration import diagnose, probability_view, binary_summary


class ConcentrationTests(unittest.TestCase):
    def prediction(self):
        p=np.array([math.exp(-1.5)*1.5**i/math.factorial(i) for i in range(20)])
        p/=p.sum()
        return {'version':'synthetic-v1','primary':'1-1','matrix':np.outer(p,p).tolist()}

    def row(self,mid='one',result=(1,1)):
        return {'match_id':mid,'track':'current','cohort':'synthetic','prediction':self.prediction(),
                'result':{'home_goals':result[0],'away_goals':result[1]} if result else None}

    def test_mode_concentration_is_not_probability_concentration(self):
        pred=self.prediction(); before=copy.deepcopy(pred); v=probability_view(pred)
        self.assertEqual(v['joint_mode']['score'],'1-1')
        self.assertAlmostEqual(v['total_mean'],3.)
        self.assertLess(v['events']['score_1_1'],.12)
        self.assertGreater(v['events']['total_ge_3'],.5)
        self.assertEqual(pred,before)
        self.assertAlmostEqual(v['top_two_gap'],v['joint_mode']['probability']-v['runner_up']['probability'])

    def test_abstentions_missing_and_pending_keep_denominators(self):
        rows=[self.row(),self.row('two',(3,0)),self.row('pending',None)]
        rows[1]['prediction']={'status':'ABSTAIN','version':'synthetic-v1'}
        g=diagnose(rows)['groups'][0]
        self.assertEqual(g['population'],3); self.assertEqual(g['settled'],2)
        self.assertEqual(g['primary']['hits'],1); self.assertEqual(g['primary']['hits_per_all_settled'],.5)
        self.assertEqual(g['primary']['accuracy_among_issued'],1.)
        self.assertEqual(g['matrix_settled'],1); self.assertEqual(g['pending'],['pending'])

    def test_calibration_zeros_ones_draw_and_outside_support(self):
        r=self.row(result=(10,1)); r['prediction']['matrix']=[[.1,.2],[.3,.4]]
        g=diagnose([r])['groups'][0]
        self.assertAlmostEqual(g['calibration']['home_zero']['mean_probability'],.3)
        self.assertEqual(g['calibration']['away_one']['observed_events'],1)
        self.assertEqual(g['proper_scores']['actual_outside_matrix_count'],1)
        self.assertEqual(g['proper_scores']['zero_probability_actual_count'],1)
        self.assertTrue(math.isfinite(g['proper_scores']['score_log_loss_clipped_1e_15']))
        json.dumps(g,allow_nan=False)

    def test_versions_and_cohorts_never_silently_pool(self):
        rows=[self.row(),self.row('two')]; rows[1]['prediction']['version']='synthetic-v2'
        self.assertEqual(len(diagnose(rows)['groups']),2)
        rows[1]['match_id']='one'
        with self.assertRaisesRegex(ValueError,'duplicate'): diagnose(rows)

    def test_invalid_matrix_visible_not_normalized(self):
        r=self.row(); r['prediction']['matrix']=[[.9]]
        g=diagnose([r])['groups'][0]
        self.assertEqual(g['invalid_matrix_match_ids'],['one'])
        self.assertEqual(g['primary']['hits'],1); self.assertEqual(g['matrix_settled'],0)

    def test_rectangular_grid_and_ties(self):
        v=probability_view({'matrix':[[.25,.25,0],[0,.25,.25]],'primary':[1,1]})
        self.assertEqual(v['mode_ties'],4); self.assertEqual(v['joint_mode']['score'],'0-0')
        self.assertEqual(v['top_two_gap'],0.); self.assertFalse(v['primary_equals_mode'])

    def test_residual_abstention_score_not_counted(self):
        for status in ('ABSTAIN','ABSTAIN_UNVERIFIED','NOT_EXECUTED','FAILED'):
            r=self.row(); r['prediction']['status']=status
            g=diagnose([r])['groups'][0]
            self.assertEqual(g['primary']['issued'],0); self.assertEqual(g['primary']['hits'],0)

    def test_fixed_bins_include_one(self):
        b=binary_summary([(0.,0),(1.,1),(.1,0)])
        self.assertEqual(b['n'],3); self.assertEqual(b['bins'][9]['n'],1)
        self.assertEqual(b['bins'][1]['n'],1)


if __name__=='__main__': unittest.main()
