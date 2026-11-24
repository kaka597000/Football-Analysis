import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from inputs import HERE, file_hash, save, store_rows
from recipe import conditional_mass, fit, geometry, predict
from study import checked_pair, picks, read_lock, seal, signed_masses
from grade import evaluate, summary
from research_program.score_form import poisson_matrix
from research_program.three_arm_model import _matrix
from optimization.evaluate import settle_ou


class FixedMarginTest(unittest.TestCase):
    def setUp(self):
        self.grid=poisson_matrix(np.array([1.8,.9]),(31,31))

    def test_all_signed_margins_and_every_quarter_handicap_are_identical(self):
        old,new=checked_pair(self.grid,.4)
        np.testing.assert_allclose(signed_masses(old),signed_masses(new),atol=1e-15)
        h,a=np.indices(old.shape)
        for line in np.arange(-5.,5.01,.25):
            legs=(line,) if line*2==int(line*2) else (line-.25,line+.25)
            net=sum(np.sign(h-a+leg) for leg in legs)/len(legs)
            for outcome in (-1,-.5,0,.5,1):
                self.assertAlmostEqual(old[net==outcome].sum(),new[net==outcome].sum(),places=14)

    def test_nuisance_ablation_preserves_every_cell_and_zeros(self):
        g=np.array([[0,.1,.1,.05],[.15,0,.05,.05],[.02,.2,.18,.1]])
        self.assertAlmostEqual(1.,g.sum())
        self.assertTrue(np.array_equal(g,predict(g,0)))
        changed=predict(g,.52)
        self.assertTrue(np.array_equal(g==0,changed==0))
        self.assertAlmostEqual(changed.sum(),1.)
        np.testing.assert_allclose(signed_masses(changed),signed_masses(g),atol=1e-14)

    def test_same_margin_odds_one_versus_every_other_cell(self):
        b=.7
        alt=predict(self.grid,b)
        for one,first,third in [((1,1),(0,0),(2,2)),((2,1),(1,0),(3,2)),((1,4),(0,3),(2,5))]:
            self.assertAlmostEqual((alt[one]/alt[first])/(self.grid[one]/self.grid[first]),np.exp(b))
            self.assertAlmostEqual(alt[first]/alt[third],self.grid[first]/self.grid[third])
        h,a=np.indices(alt.shape)
        self.assertNotAlmostEqual(alt[(h+a)>2.5].sum(),self.grid[(h+a)>2.5].sum())

    def test_conditional_mass_fit_and_home_away_symmetry(self):
        q=conditional_mass(self.grid)
        idx,_=geometry(self.grid.shape)
        self.assertAlmostEqual(q[self.grid.shape[1]-1],self.grid[1,1]/self.grid[idx==self.grid.shape[1]-1].sum())
        self.assertAlmostEqual(fit([.5,.5],[0,1]),0.)
        self.assertGreater(fit([.25]*4,[0,1,1,1]),0.)
        self.assertAlmostEqual(fit([.00001]*100,[1]*100),np.log(3.))
        with self.assertRaisesRegex(ValueError,'support'):
            fit([0.],[1])
        np.testing.assert_allclose(predict(self.grid,.2).T,predict(self.grid.T,.2),rtol=2e-15)


class ForecastContractTest(unittest.TestCase):
    def files(self,root):
        data=root/'inputs'
        f=root/'fit'
        row=dict(id='synthetic-a',league='eng.1',date='2023-01-01',line=2.5,rates=[1.3,.85])
        store_rows(data,[row,dict(row,id='abstain',rates=None)])
        save(f,dict(beta=.31,protocol_sha256=file_hash(HERE/'protocol.json')))
        return data,f,row

    def test_missing_projection_retains_full_population_and_lock_is_immutable(self):
        with TemporaryDirectory() as d:
            root=Path(d); data,f,row=self.files(root)
            lock=seal(data,f,root/'locked','january')
            self.assertEqual((lock['n'],lock['n_issued']),(2,1))
            _,rows=read_lock(root/'locked/lock.json',root/'locked/forecasts.jsonl','january')
            self.assertIsNone(rows[0]['base'])
            with self.assertRaises(FileExistsError):
                seal(data,f,root/'locked','january')
            store_rows(data,[dict(row,actual=[1,0])])
            with self.assertRaisesRegex(ValueError,'outcome'):
                seal(data,f,root/'invalid','january')

    def test_wrong_forecast_identity_or_market_stays_unscored(self):
        with TemporaryDirectory() as d:
            root=Path(d);data,f,row=self.files(root)
            seal(data,f,root/'locked','january')
            lp=root/'locked/lock.json'; fp=root/'locked/forecasts.jsonl'
            _,rows=read_lock(lp,fp,'january')
            with self.assertRaisesRegex(ValueError,'total lines'):
                evaluate(rows[1],row,dict(score=[1,0],observed_line=3.),.31)
            r=evaluate(rows[1],row,dict(score=[1,0],observed_line=None),.31)
            self.assertIsNone(r['base']['values'][-1])
            self.assertEqual(summary(rows,[r])['metrics']['total_full_win']['n'],0)
            fp.write_bytes(fp.read_bytes()+b'\n')
            with self.assertRaisesRegex(ValueError,'lock mismatch'):
                read_lock(lp,fp,'january')

    def test_actual_asian_settlements_remain_separate(self):
        self.assertEqual(settle_ou(3,3.,'over'),'PUSH')
        self.assertEqual(settle_ou(3,3.25,'under'),'HALF_WIN')
        self.assertEqual(settle_ou(2,2.25,'over'),'HALF_LOSS')
        self.assertEqual(settle_ou(3,2.5,'over'),'FULL_WIN')


if __name__=='__main__':
    unittest.main()
