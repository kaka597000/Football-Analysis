import unittest
from datetime import datetime,timedelta
import numpy as np
import lightgbm as lgb
import ml_model as m


class Tests(unittest.TestCase):
    def rows(self):
        return [{'match_id':str(i),'date':(datetime(2020,1,1)+timedelta(days=i)).date().isoformat(),
                 'home':'A','away':'B','home_goals':1,'away_goals':0,'neutral':False}
                for i in range(90)]

    def test_feature_builder_excludes_target_same_day_and_future_results(self):
        rows=self.rows();target=dict(rows[75]);rates=(1.4,1.)
        before=m.feature_matrix(rows,target,rates)
        for r in rows[75:]:r.update(home_goals=29,away_goals=29)
        np.testing.assert_equal(before,m.feature_matrix(rows,target,rates))
        self.assertTrue(np.isnan(before[:,m.FEATURES.index('own_recent_xg')]).all())

    def test_target_xg_cannot_enter_features_and_unproven_past_xg_not_used(self):
        rows=self.rows();target=rows[75]
        before=m.feature_matrix(rows,target,(1.4,1.))
        target.update(home_xg=20.,away_xg=20.,xg_evidence={'source_url':'https://example.test','query_time':'2020-01-01T00:00:00Z'})
        np.testing.assert_equal(before,m.feature_matrix(rows,target,(1.4,1.)))
        rows[74].update(home_xg=2.,away_xg=1.)
        np.testing.assert_equal(before,m.feature_matrix(rows,target,(1.4,1.)))

    def test_verified_past_xg_has_coverage_and_invalid_xg_rejected(self):
        rows=self.rows();r=rows[74]
        r.update(home_xg=2.,away_xg=1.,observed_at='2021-01-01T00:00:00Z',
                 xg_evidence={'source_url':'https://example.test','query_time':'2021-01-01T00:00:00Z'})
        f=m.feature_matrix(rows,rows[75],(1.4,1.))
        self.assertEqual(f[0,m.FEATURES.index('own_recent_xg')],2.)
        self.assertAlmostEqual(f[0,m.FEATURES.index('own_xg_coverage')],1/6)
        r['home_xg']=-1.
        with self.assertRaises(ValueError):m.feature_matrix(rows,rows[75],(1.4,1.))

    def test_real_lightgbm_residual_serialization_and_probability_consistency(self):
        # Synthetic plumbing fixture, excluded from football performance results.
        items=[]
        for i in range(40):
            f=np.zeros((2,len(m.FEATURES)));f[:,0]=np.log([1.4,1.]);f[:,2]=[1,0]
            items.append({'features':f,'rates':(1.4,1.),'row':{'home_goals':2,'away_goals':0}})
        booster=m.fit_booster(items); restored=lgb.Booster(model_str=booster.model_to_string())
        score,delta=m.ml_matrix(restored,items[0]['features'],(1.4,1.),-.04)
        self.assertAlmostEqual(score.sum(),1.,12)
        self.assertTrue((score>=0).all());self.assertLessEqual(max(abs(delta)),m.CONFIG['residual_cap'])
        self.assertAlmostEqual(m.legacy.probabilities(score).sum(),1.,12)
        self.assertAlmostEqual(sum(m.legacy.total_settlement(score,2.75,'over').values()),1.,12)

    def test_historical_process_is_used_but_target_process_is_excluded(self):
        rows=self.rows();target=rows[75]
        p={'home':{'shots_on_target':5,'shots_off_target':7,'possession':60},
           'away':{'shots_on_target':2,'shots_off_target':3,'possession':40}}
        for r in (rows[74],target):
            r.update(process=p,observed_at='2021-01-01T00:00:00Z',
                     process_evidence={'source_url':'https://example.test','query_time':'2021-01-01T00:00:00Z'})
        f=m.feature_matrix(rows,target,(1.4,1.))
        self.assertEqual(f[0,m.FEATURES.index('own_recent_shots_on_target')],5)
        self.assertAlmostEqual(f[0,m.FEATURES.index('own_process_coverage')],1/6)
        target['process']={'home':{'shots_on_target':90,'shots_off_target':90,'possession':90}}
        np.testing.assert_equal(f,m.feature_matrix(rows,target,(1.4,1.)))


if __name__=='__main__':unittest.main()
