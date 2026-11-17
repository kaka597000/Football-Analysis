import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from scipy.stats import poisson

from nations_league import repair


class RepairTests(unittest.TestCase):
    def test_original_poisson_recipe_is_numerically_preserved(self):
        m=repair.probability_matrix(1.65,1.32)
        expected=np.outer(poisson.pmf(np.arange(51),1.65),poisson.pmf(np.arange(51),1.32))
        np.testing.assert_allclose(m,expected,rtol=1e-12,atol=1e-15)

    def test_shared_pace_preserves_means_and_has_expected_covariance(self):
        m=repair.probability_matrix(2.44,.86,.25)
        h,a=np.indices(m.shape)
        self.assertAlmostEqual(float((h*m).sum()),2.44,places=8)
        self.assertAlmostEqual(float((a*m).sum()),.86,places=8)
        self.assertAlmostEqual(float((h*a*m).sum())-2.44*.86,.25*2.44*.86,places=8)
        self.assertAlmostEqual(float((h*h*m).sum())-2.44**2,2.44+.25*2.44**2,places=8)
        p=repair.probability_matrix(2.44,.86)
        ph,pa=np.indices(p.shape)
        self.assertGreater(float(m[h+a>=9].sum()),float(p[ph+pa>=9].sum()))

    def test_high_supported_rates_do_not_silently_drop_tail(self):
        m=repair.probability_matrix(12,12,.25,1.1)
        self.assertAlmostEqual(float(m.sum()),1.,places=12)
        self.assertLess(float(m[-1,:].sum()+m[:,-1].sum()),1e-10)
        self.assertGreater(m.shape[0],51)
        for args in [(0,1),(13,1),(1,float('nan')),(1,1,-1),(1,1,.3)]:
            with self.subTest(args=args),self.assertRaises(ValueError):repair.probability_matrix(*args)

    def test_point_mode_and_wdl_are_distinct_objectives(self):
        m=repair.probability_matrix(1.65,1.32)
        self.assertEqual(repair.select_score(m),[1,1])
        h,a=repair.select_score(m,'wdl_conditioned_map')
        self.assertGreater(h,a)
        total=repair.select_score(m,'total_mode_conditioned_map')
        x,y=np.indices(m.shape)
        self.assertEqual(sum(total),int(np.bincount((x+y).ravel(),weights=m.ravel()).argmax()))
        np.testing.assert_allclose(m,repair.probability_matrix(1.65,1.32))
        with self.assertRaises(ValueError):repair.select_score(m,'force_more_goals')

    def test_calendar_partition_excludes_target_and_later_labels(self):
        rows=[{'date':d} for d in ('2022-01-01','2024-01-01','2024-12-31','2025-01-01')]
        train,targets=repair.split(rows,['2024-01-01','2025-01-01'])
        self.assertEqual([r['date'] for r in train],['2022-01-01'])
        self.assertEqual(len(targets),2)
        with self.assertRaises(ValueError):repair.split(rows,['2025-01-01','2024-01-01'])

    def test_net_hits_requires_same_fixtures_and_preserves_losses(self):
        before=[{'match_id':str(i),'date':'2024-09-05','primary_hit':i==0,
                 'metrics':{'wdl_accuracy':1.}} for i in range(3)]
        after=copy.deepcopy(before)
        after[0]['primary_hit']=False;after[1]['primary_hit']=True
        out=repair.paired(before,after)
        self.assertEqual(out['primary_net_hits'],0)
        self.assertEqual(out['gained_match_ids'],['1']);self.assertEqual(out['lost_match_ids'],['0'])
        after[0]['match_id']='different'
        with self.assertRaises(ValueError):repair.paired(before,after)

    def test_probability_improvement_cannot_replace_primary_gain(self):
        base={'n':16,'primary_hits':3,'wdl_hits':8,**dict.fromkeys(
            ['score_log_loss','score_brier','wdl_brier','wdl_rps','zero_brier'],1.)}
        c={**base,'score_log_loss':.9}
        reasons=repair.quality_reasons(c,base,{'primary_net_rate_95':[-.1,.1]})
        self.assertIn('NO_STRICT_TOP1_GAIN',reasons)
        self.assertIn('TOP1_NET_INTERVAL_INCLUDES_ZERO',reasons)

    def test_gate_is_not_a_licence_to_relabel_unsupported_scores(self):
        r={'match_id':'e','date':'2024-01-01','cohort':'uefa.nations:A:league-phase',
           'home_goals':7,'away_goals':2}
        p=repair.record_prediction(repair.probability_matrix(2.44,.86),r,'joint_map')
        self.assertFalse(p['primary_hit']);self.assertEqual(p['actual'],[7,2])
        summary=repair.summarize([p])
        self.assertEqual(summary['actual_total4plus_n'],1)
        self.assertEqual(summary['actual_line_n'],0)
        self.assertEqual(summary['coverage'],1.)


if __name__=='__main__':unittest.main()
