import copy,unittest
from datetime import date,timedelta
import numpy as np
from research_program.challengers import distribution,fit,rates,experiment
class ChallengerTests(unittest.TestCase):
 def rows(self):
  return [{'match_id':str(i),'cohort':'eng.1','stage':'regular','date':(date(2020,1,1)+timedelta(days=i)).isoformat(),'home':'A','away':'B','home_goals':i%4,'away_goals':i%3,'score_basis':'regulation_90','neutral':False} for i in range(120)]
 def test_distribution_means_and_overdispersion_without_manual_goal_shift(self):
  x=np.arange(81)
  for family,param in [('poisson',None),('negative_binomial',5.),('bivariate',.3),('tempo_mixture',None)]:
   m,a=distribution([1.7,1.1],family,param);self.assertAlmostEqual(m.sum(),1.)
   self.assertAlmostEqual(x@m.sum(axis=1),1.7,places=5);self.assertAlmostEqual(x@m.sum(axis=0),1.1,places=5)
  p,_=distribution([1.7,1.1]);n,_=distribution([1.7,1.1],'negative_binomial',2.)
  self.assertGreater(n[4:,:].sum(),p[4:,:].sum())
 def test_strength_changes_with_past_results_and_new_teams_shrink_to_prior(self):
  rows=self.rows();m=fit(rows[:60]);before=copy.deepcopy(m);r=rates(m,rows[60]);self.assertTrue(np.isfinite(r).all());self.assertEqual(m,before)
  rows[59]['home_goals']=20;self.assertNotEqual(fit(rows[:60]),m)
 def test_test_labels_cannot_select_family_or_fit_diagnostic_model(self):
  rows=self.rows();a=experiment(rows)
  for r in rows[90:]:r['home_goals']=8;r['away_goals']=7
  b=experiment(rows);self.assertEqual(a['selected'],b['selected']);self.assertEqual(a['diagnostic_model'],b['diagnostic_model']);self.assertFalse(a['claims']['prospective_unseen_validation'])
if __name__=='__main__':unittest.main()
