import unittest

import numpy as np

from research_program import all_scope_model as base
from research_program import draw_preserving as model


def grid():
    value = np.arange(1, base.H.size + 1, dtype=float).reshape(base.H.shape)
    return value / value.sum()


class DrawPreservingTests(unittest.TestCase):
    def test_projection_keeps_every_draw_cell_and_all_three_wdl_masses(self):
        prior, full = grid(), grid()[:, ::-1].copy()
        protected = model.project(prior, full)
        np.testing.assert_array_equal(protected[base.H == base.A], prior[base.H == base.A])
        for mask in (base.H > base.A, base.H == base.A, base.H < base.A):
            self.assertAlmostEqual(float(protected[mask].sum()), float(prior[mask].sum()), places=14)
        self.assertAlmostEqual(float(protected.sum()), 1.0, places=14)

    def test_zero_mass_non_draw_block_inherits_prior_distribution(self):
        prior = grid()
        full = np.zeros_like(prior)
        full[base.H == base.A] = prior[base.H == base.A]
        full /= full.sum()
        protected = model.project(prior, full)
        np.testing.assert_array_equal(protected[base.H > base.A], prior[base.H > base.A])
        np.testing.assert_array_equal(protected[base.H < base.A], prior[base.H < base.A])

    def test_tiny_positive_block_mass_is_normalized_before_rescaling(self):
        prior = grid()
        full = np.zeros_like(prior)
        draws = base.H == base.A
        home = base.H > base.A
        away = base.H < base.A
        full[draws] = prior[draws]
        full[home] = 1e-320
        # Keep total normalized while the home-win block has a subnormal mass.
        full /= full.sum()
        projected = model.project(prior, full)
        self.assertTrue(np.isfinite(projected).all())
        self.assertAlmostEqual(float(projected[home].sum()), float(prior[home].sum()), places=14)
        self.assertAlmostEqual(float(projected[away].sum()), float(prior[away].sum()), places=14)

    def test_choices_keep_r7_wdl_and_total_and_inherit_signed_zero_dose(self):
        prior, full = grid(), grid()[::-1, ::-1].copy()
        original = dict(primary='1-0', top3=['1-0','0-1','1-1'], wdl='home',
                        total_side='under', total_line=2.5)
        forecasts, grids = model.choices(prior, full, 0.0, 1.0, original, 2.5)
        self.assertEqual(set(forecasts), set(model.FORECASTS))
        self.assertEqual(set(grids), set(model.FORECASTS))
        self.assertEqual(forecasts['candidate']['primary'], original['primary'])
        self.assertEqual(forecasts['candidate']['top3'], original['top3'])
        for forecast in forecasts.values():
            self.assertEqual(forecast['wdl'], 'home')
            self.assertEqual((forecast['total_side'], forecast['total_line']), ('under', 2.5))
        np.testing.assert_array_equal(grids['candidate'], prior)
        with self.assertRaisesRegex(ValueError, 'total line'):
            model.choices(prior, full, .5, 1.0, original, 3.5)

    def test_invalid_inputs_and_unregistered_doses_reject(self):
        prior = grid()
        original = dict(primary='1-0', top3=['1-0','0-1','1-1'], wdl='home', total_side=None)
        with self.assertRaises(ValueError):
            model.project(prior * 2, prior)
        with self.assertRaisesRegex(ValueError, 'dose'):
            model.choices(prior, prior, .25, 0.0, original, None)


if __name__ == '__main__':
    unittest.main()
