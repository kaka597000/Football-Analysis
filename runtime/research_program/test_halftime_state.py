"""The halftime hidden state is integrated before kickoff and only past halves train the model."""
import unittest

import numpy as np

from . import all_scope_model as base
from . import halftime_state as model
from .halftime_state_study import match_halves


class HalftimeStateTests(unittest.TestCase):
    def setUp(self):
        self.grid = np.outer(model._poisson(1.6), model._poisson(1.1))
        self.grid /= self.grid.sum()
        self.fit = dict(version=model.VERSION, shares=[.44,.48], leading=.82, trailing=1.21)

    def test_independent_enumeration_and_zero_effect(self):
        self.assertIs(model.adjusted(self.grid, dict(self.fit, leading=1., trailing=1.)), self.grid)
        adjusted = model.adjusted(self.grid, self.fit)
        self.assertAlmostEqual(adjusted.sum(), 1.)
        means = model.rates(self.grid)
        first = means * self.fit['shares']; second = means - first
        def weighted_cell(h, a):
            weight = 0.
            for u in range(h + 1):
                for v in range(a + 1):
                    lead = self.fit['leading']; trail = self.fit['trailing']
                    factors = (lead, trail) if u > v else (trail, lead) if v > u else (1., 1.)
                    weight += (model._poisson(first[0])[u] * model._poisson(first[1])[v] *
                               model._poisson(second[0] * factors[0])[h-u] * model._poisson(second[1] * factors[1])[a-v])
            unadjusted = model._poisson(means[0])[h] * model._poisson(means[1])[a]
            return self.grid[h,a] * weight / unadjusted
        for cell in ((1,0),(2,2),(1,3),(4,1)):
            self.assertAlmostEqual(adjusted[cell] / adjusted[0,0], weighted_cell(*cell) / weighted_cell(0,0))

    def test_training_shares_and_lead_exposures(self):
        fit = model.fit([self.grid]*3, [([2,0],[0,1]), ([0,1],[3,0]), None])
        self.assertEqual(fit['training_observed'], 2)
        self.assertEqual(fit['exposure']['leading']['team_halves'], 2)
        self.assertEqual(fit['exposure']['leading']['goals'], 0)
        self.assertEqual(fit['exposure']['trailing']['goals'],4)
        self.assertAlmostEqual(fit['shares'][0],20/45)
        self.assertAlmostEqual(fit['shares'][1],19/42)
        self.assertEqual(model.fit([self.grid], [None])['leading'], 1.)
        with self.assertRaises(ValueError): model.fit([self.grid], [([0,-1],[0,0])])

    def test_fixture_identity_complete_population_and_half_order(self):
        game = dict(id='espn:12',kickoff_at='2020-08-01T20:00Z',home_id='4',away_id='9',home_goals=2,away_goals=1)
        row = dict(match_id=game['id'],home_id='4',away_id='9',kickoff_utc=game['kickoff_at'],source_sha256='a'*64,
                   full=[2,1],half1=[1,1],half2=[1,0],eligible=True,reason=None)
        self.assertEqual(match_halves([game],[row]), [[[1,1],[1,0]]])
        self.assertEqual(match_halves([game],[dict(row,eligible=False,reason='ambiguous periods')]), [None])
        for rows in ([],[row,row],[dict(row,home_id='5')],[dict(row,half1=[2,0])],[dict(row,half2=[True,0])]):
            with self.assertRaises(ValueError): match_halves([game],rows)

    def test_matrix_domain(self):
        for grid, fit in ((self.grid*2,self.fit),(np.zeros((2,2)),self.fit),(self.grid,dict(self.fit,trailing=np.nan))):
            with self.assertRaises(ValueError): model.adjusted(grid,fit)
        flat = np.zeros((base.SIZE,base.SIZE)); flat[0,0]=1.
        np.testing.assert_equal(model.adjusted(flat,self.fit), flat)
