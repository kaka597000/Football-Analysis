import unittest

import numpy as np

from research_program import j1_team_allocation as model


class TeamAllocationTests(unittest.TestCase):
    def setUp(self):
        self.prior = np.zeros(model.base.H.shape)
        self.prior[0, 0] = .20
        self.prior[1, 1] = .40
        self.prior[2, 0] = .15
        self.prior[0, 2] = .10
        self.prior[2, 1] = .15
        self.design = np.zeros((25, 2))
        self.design[10, 0] = 1.

    def test_training_is_conditional_on_observed_total(self):
        altered = self.prior.copy()
        altered[0, 0] += .10
        altered[2, 1] -= .10
        scores = np.tile([2, 0], (10, 1))
        design = np.repeat(self.design[None], len(scores), axis=0)
        before = model.fit(np.repeat(self.prior[None], len(scores), axis=0), design, scores)
        after = model.fit(np.repeat(altered[None], len(scores), axis=0), design, scores)
        self.assertGreater(before[0], 0.)
        np.testing.assert_array_equal(before, after)

    def test_reallocation_preserves_each_total_and_zero_support(self):
        for dose in (.5, 1.):
            adjusted = model.adjust(self.prior, self.design, [1., 0.], dose)
            np.testing.assert_allclose(model.totals(adjusted), model.totals(self.prior), rtol=0, atol=1e-15)
            np.testing.assert_array_equal(adjusted[self.prior == 0], 0.)
            self.assertGreater(adjusted[2, 0], self.prior[2, 0])
        self.assertIs(model.adjust(self.prior, self.design, [1., 0.], 0.), self.prior)
        self.assertIs(model.adjust(self.prior, self.design, [0., 0.], 1.), self.prior)
