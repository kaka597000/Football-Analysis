import unittest

import numpy as np

from research_program import all_scope_model as base
from research_program import europe_score_dispersion as study


class TestEuropeanScoreDispersion(unittest.TestCase):
    def test_tilt_preserves_outcomes_and_means(self):
        prior = np.exp(-.4 * base.H - .6 * base.A)
        prior /= prior.sum()
        adjusted, errors = study.adjust(prior[None, :, :], [.15, -.18])
        self.assertLess(max(errors.values()), 2e-12)
        old, new = (study.bucket_masses(m)[0] for m in (prior[None, :, :], adjusted))
        original_wdl = old.sum(axis=1)
        np.testing.assert_allclose(new.sum(axis=1), original_wdl, atol=2e-12, rtol=0.)
        second_moment_shift = (new - old) @ study.GOALS**2
        self.assertGreater(second_moment_shift[0], 0.)
        self.assertLess(second_moment_shift[1], 0.)
        np.testing.assert_array_equal(new == 0, old == 0)

    def test_third_keeps_first_two_and_respects_original_mass_budget(self):
        prior = np.array([[.25, .22, 0.], [.19, .18, 0.], [.16, 0., 0.]])
        changed = np.array([[.23, .22, 0.], [.10, .20, 0.], [.25, 0., 0.]])
        original = ['0-0', '0-1', '1-0']
        self.assertEqual(study.third_pick(prior, changed, original), ['0-0', '0-1', '1-1'])
        self.assertEqual(study.third_pick(prior, prior, original), original)
