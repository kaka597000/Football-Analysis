import unittest,copy
import numpy as np
import features as ft
class FeaturesTest(unittest.TestCase):
 def rows(self):
  return [{'match_id':str(i),'cohort':'eng.1','date':d,'home':'A','away':'B','home_goals':h,'away_goals':0,'score_basis':'regulation_90','odds':[1.5,4,7],'season':'2020','process':{'home_shots':s,'away_shots':4,'home_sot':2,'away_sot':1}} for i,(d,h,s) in enumerate([('2020-08-01',1,10),('2020-08-04',2,20),('2020-08-08',3,30)])]
 def test_target_score_and_process_excluded(self):
  a=self.rows();b=copy.deepcopy(a);b[-1]['home_goals']=20;b[-1]['process']['home_shots']=99
  np.testing.assert_equal(ft.items(a)[-1]['features'],ft.items(b)[-1]['features'])
 def test_same_day_excluded(self):
  a=self.rows();a[-1]['date']=a[-2]['date'];b=copy.deepcopy(a);b[-2]['process']['home_shots']=99;b[-2]['home_goals']=20
  np.testing.assert_equal(ft.items(a)[-1]['features'],ft.items(b)[-1]['features'])
 def test_lagged_shots_and_missing(self):
  x=ft.items(self.rows());j=ft.FEATURES.index('favorite_shots_for');cov=ft.FEATURES.index('favorite_shots_coverage')
  self.assertTrue(np.isnan(x[0]['features'][j]));self.assertEqual(x[0]['features'][cov],0);self.assertEqual(x[-1]['features'][j],15)
 def test_prior_venue_and_counts(self):
  x=ft.items(self.rows())[-1]['features'];self.assertEqual(x[ft.FEATURES.index('favorite_venue_n')],2);self.assertEqual(x[ft.FEATURES.index('favorite_league_games_7d')],2)
 def test_mixed_cohort_rejected(self):
  r=self.rows();r[-1]['cohort']='esp.1'
  with self.assertRaises(ValueError):ft.items(r)
 def test_research_xg_does_not_fill_provider_xg(self):
  rows=self.rows();rows[0]['process'].update(home_research_xg=1.4,away_research_xg=.6)
  x=ft.items(rows)[1]['features'];self.assertEqual(x[ft.FEATURES.index('favorite_research_xg_for')],1.4);self.assertTrue(np.isnan(x[ft.FEATURES.index('favorite_xg_for')]))
if __name__=='__main__':unittest.main()
