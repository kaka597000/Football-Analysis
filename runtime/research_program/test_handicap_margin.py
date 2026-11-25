import unittest
import numpy as np
from research_program import handicap_margin as m
from research_program import all_scope_model as base


class TestHandicapMargin(unittest.TestCase):
    def setUp(self):
        p=np.exp(-.3*base.H-.4*base.A)
        self.prior=p/p.sum()
        self.control=m.two_market(self.prior,[1.8,3.4,4.4],[2.05,1.85])

    def test_current_snapshot_marginals_and_redundant_handicap(self):
        np.testing.assert_allclose([self.control[base.RESULT==i].sum() for i in range(3)],m.devig([1.8,3.4,4.4]))
        self.assertAlmostEqual(self.control[base.H+base.A>2.5].sum(),m.devig([2.05,1.85])[0])
        for q in (-.5,-.25,0,.25,.5):
            new,theta,informative=m.handicap_update(self.control,q,[1.8,2.1])
            self.assertIs(new,self.control)
            self.assertFalse(informative)

    def test_quarter_settlement_and_cell_conservation(self):
        new,theta,informative=m.handicap_update(self.control,-1.25,[2.05,1.85])
        self.assertTrue(informative)
        self.assertNotEqual(theta,0.)
        np.testing.assert_allclose([self.control[m.CELL==i].sum() for i in range(6)],
                                   [new[m.CELL==i].sum() for i in range(6)])
        won,lost,level=m.stake_codes(-5)
        idx=np.ravel_multi_index((2,1),self.control.shape)
        self.assertEqual((won[level[idx]],lost[level[idx]]),(0.,.5))
        self.assertAlmostEqual(new.sum(),1.)
