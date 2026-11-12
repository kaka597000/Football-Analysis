import copy
import unittest
from datetime import date,timedelta
import numpy as np
from research_program import elo_kelly_ablation as m


class AblationTests(unittest.TestCase):
    def rows(self):
        return [{'id':str(i),'league':'E0','season':'1920','date':str(date(2020,1,1)+timedelta(days=i)),
            'home':'A','away':'B','hg':i%4,'ag':i%3,'market':m.market(self.quote())} for i in range(30)]

    def quote(self):
        d={}
        for columns,odds in zip(m.PROTOCOL['book_columns'],[(2.,3.5,4.),(2.2,3.3,3.8),(2.1,3.6,3.7)]):d.update(zip(columns,map(str,odds)))
        d.update({'PC>2.5':'1.9','PC<2.5':'2.'});return d

    def test_own_and_future_labels_do_not_change_features(self):
        rows=self.rows();a=m.feature_rows(rows);changed=copy.deepcopy(rows)
        for r in changed[20:]:r['hg']=9;r['ag']=8
        b=m.feature_rows(changed)
        self.assertEqual(a[17]['elo_gap'],b[17]['elo_gap'])
        self.assertEqual(a[17]['goal_features'],b[17]['goal_features'])
        self.assertNotEqual(a[-1]['elo_gap'],b[-1]['elo_gap'])

    def test_market_requires_all_closing_books(self):
        d=self.quote();del d['WHCH'];d['WHH']='2.'
        self.assertIsNone(m.market(d))

    def test_kelly_uses_external_panel_not_own_probability(self):
        q=m.market(self.quote());ref=np.mean(q['books'][1:],axis=0)
        np.testing.assert_allclose(q['kelly_relative'],np.array([2,3.5,4])*ref/q['payouts'][0]-1)

    def test_role_swap_reverses_elo_and_market_directions(self):
        r=m.feature_rows(self.rows())[-1];x=m.design(r,'goals_market_kelly_elo')
        self.assertEqual(x[0,-1],-x[1,-1]);self.assertEqual(x[0,4],-x[1,4])

    def test_fitted_model_roundtrip_and_target_label_invariance(self):
        rs=m.feature_rows(self.rows());m1=m.fit(rs[:-1],'goals_market_kelly_elo');r=rs[-1]
        changed=dict(r,hg=99,ag=99);np.testing.assert_array_equal(m.predict(m1,r),m.predict(m1,changed))
        self.assertLess(m1['train_end'],r['date']);self.assertTrue(np.isfinite(m.predict(m1,r)).all())

    def test_extreme_target_uses_training_bounds_not_refitted_bounds(self):
        rs=m.feature_rows(self.rows());model=m.fit(rs[:-1],'goals_elo');r=copy.deepcopy(rs[-1]);r['elo_gap']=1e100
        before=copy.deepcopy(model);self.assertTrue(np.isfinite(m.predict(model,r)).all());self.assertEqual(model,before)

    def test_missing_market_can_still_supply_elo_only_features(self):
        r=m.feature_rows(self.rows())[-1];r['market']=None
        self.assertEqual(m.design(r,'goals_elo').shape,(2,5))

    def test_same_team_same_day_rejected(self):
        rows=self.rows();rows[1]['date']=rows[0]['date']
        with self.assertRaises(ValueError):m.feature_rows(rows)


if __name__=='__main__':unittest.main()
