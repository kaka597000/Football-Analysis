import unittest

import numpy as np
from scipy.special import expit

from research_program import continuation_scores as m
from research_program.lagged_pass_study import prior_passing
from research_program.score_form import poisson_matrix


class ContinuationTests(unittest.TestCase):
    def test_factorization_reconstructs_every_cell_and_its_tail(self):
        prior=np.stack([poisson_matrix([h,a],m.base.H.shape) for h,a in [(1.2,1.5),(.3,3.8)]])
        xs=np.zeros((2,len(m.COMMON)))
        design=m.hazard_inputs(prior,xs)
        actual=m.reconstruct(prior,*[expit(off) for _,off in design])
        np.testing.assert_allclose(prior,actual,rtol=2e-11,atol=1e-15)
        np.testing.assert_allclose(actual.sum((1,2)),1.)
        self.assertTrue((actual>=0).all())
        mask,labels=m.labels([[0,0],[2,1],[9,0]])[0]
        self.assertEqual(mask.sum(1).tolist(),[1,4,9])
        self.assertEqual(labels.sum(1).tolist(),[0,3,9])
        share_mask,share_y=m.labels([[2,1]])[1]
        self.assertEqual(share_mask.sum(),3)
        self.assertEqual(share_y[share_mask].sum(),2)

    def test_target_day_and_peer_results_do_not_enter_prematch_features(self):
        games=[dict(id=str(i),stage='regular',home=h,away=a,date=d,home_goals=1,away_goals=0,
                    neutral=False,h2h=None,total=None,paired_market=False)
               for i,(h,a,d) in enumerate([('A','B','2020-01-01'),('A','C','2020-01-02'),
                                            ('D','B','2020-01-02'),('A','B','2020-01-04')])]
        before,x,_=m.inputs(games,'eng.1')
        altered=[dict(g,home_goals=9,away_goals=7) if i==1 else g for i,g in enumerate(games)]
        after,z,_=m.inputs(altered,'eng.1')
        np.testing.assert_array_equal(before[:3],after[:3])
        np.testing.assert_array_equal(x[:3],z[:3])
        self.assertFalse(np.array_equal(before[3],after[3]))

    def test_graph_ablation_only_uses_previous_dates(self):
        games=[dict(home='H',away='A',date=date,home_passing=[500,.8,.5,.6,.15,.16,.6],
                    away_passing=[400,.7,.4,.5,.17,.18,.5]) for date in ('2017-10-01','2017-10-01','2017-10-08')]
        first=prior_passing(games)
        changed=prior_passing([dict(games[0],home_passing=[650,.9,.6,.7,.2,.2,.8]),*games[1:]])
        np.testing.assert_array_equal(first[:2],changed[:2])
        self.assertFalse(np.array_equal(first[2],changed[2]))


if __name__=='__main__':unittest.main()
