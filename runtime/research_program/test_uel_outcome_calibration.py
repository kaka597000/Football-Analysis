import unittest

import numpy as np
from scipy.special import softmax

from research_program import all_scope_model as base
from research_program import uel_outcome_calibration as calibration


class TestOutcomeCalibration(unittest.TestCase):
    def test_outcome_reweighting_preserves_conditional_scores(self):
        grid = np.exp(-.5 * base.H - .7 * base.A)
        grid /= grid.sum()
        original = np.bincount(base.RESULT.ravel(), weights=grid.ravel())
        updated = np.array([.41, .23, .36])
        adjusted = calibration.reweight(grid, original, updated)
        match = np.bincount(base.RESULT.ravel(), weights=adjusted.ravel())
        np.testing.assert_allclose(match, updated, atol=1e-12, rtol=0.)
        np.testing.assert_allclose(adjusted / match[base.RESULT], grid / original[base.RESULT], rtol=0., atol=1e-12)

    def test_fit_improves_draw_probability_when_draws_are_underrepresented(self):
        prior = np.tile([.4, .2, .4], (60, 1))
        truth = np.repeat(np.arange(3), [15, 30, 15])
        coefficients = calibration.fit(prior, truth)
        improved = softmax(calibration.logits(np.log(prior[0]), coefficients))
        self.assertGreater(improved[1], prior[0, 1])
        self.assertLess(-np.log(improved[truth]).mean(), -np.log(prior[0, truth]).mean())
