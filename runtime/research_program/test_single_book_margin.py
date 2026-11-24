import unittest
from pathlib import Path

import numpy as np
from research_program.market_baseline import quote_only_matrices
from research_program.single_book_margin import fit_weight, paired_matrices, price_probabilities
from research_program.three_arm_model import SIDES, score_picks
from v2.model import total_settlement


class SingleBookMarginTests(unittest.TestCase):
    prices = {"home": 1.6, "draw": 3.5, "away": 5.3}

    def test_fair_book_is_a_fixed_point(self):
        baseline, power = price_probabilities({"home": 2.0, "draw": 4.0, "away": 4.0})
        np.testing.assert_array_equal(baseline, power)

    def test_unequal_overround_keeps_order_and_transfers_more_mass_to_favourite(self):
        baseline, power = price_probabilities(self.prices)
        self.assertGreater(power[0], baseline[0])
        self.assertLess(power[2], baseline[2])
        self.assertAlmostEqual(float(power.sum()), 1.0)
        np.testing.assert_array_equal(np.argsort(baseline), np.argsort(power))

    def test_underround_and_equal_book_stay_probabilities(self):
        for odds in ((2.3, 3.5, 4.5), (2.8, 2.8, 2.8)):
            with self.subTest(odds=odds):
                baseline, power = price_probabilities(dict(zip(SIDES, odds)))
                self.assertTrue(np.all(power > 0))
                self.assertAlmostEqual(float(baseline.sum()), 1.0)
                self.assertAlmostEqual(float(power.sum()), 1.0)
                if odds[0] == odds[1]:
                    np.testing.assert_allclose(baseline, power)

    def test_ablation_exactly_reproduces_benchmark(self):
        t = dict(line=2.75, over=2.01, under=1.87)
        _, original = quote_only_matrices(self.prices, t)
        baseline, ablated = paired_matrices(self.prices, t, 0)
        np.testing.assert_array_equal(original, baseline)
        np.testing.assert_array_equal(original, ablated)

    def test_wdl_and_original_asian_settlement_constraints(self):
        b, p = price_probabilities(self.prices)
        for line in (2.5, 2.75, 3.0):
            with self.subTest(line=line):
                totals = dict(line=line, over=1.9, under=2.02)
                baseline, candidate = paired_matrices(self.prices, totals, 0.4)
                for grid, target in ((baseline, b), (candidate, b + 0.4 * (p - b))):
                    observed = score_picks(grid)["wdl_probabilities"]
                    np.testing.assert_allclose([observed[k] for k in SIDES], target, atol=1e-11)
                    v = total_settlement(grid, line, "over")
                    win, lose = v["win"] + v["half_win"] / 2, v["loss"] + v["half_loss"] / 2
                    self.assertAlmostEqual(win / (win + lose), 2.02 / (2.02 + 1.9))
                    self.assertAlmostEqual(float(grid.sum()), 1.0)

    def test_fit_learns_only_from_supplied_past_labels(self):
        self.assertEqual(fit_weight([(self.prices, "home")]), 1.0)
        self.assertEqual(fit_weight([(self.prices, "away")]), 0.0)
        self.assertEqual(fit_weight([({"home": 2, "draw": 4, "away": 4}, "draw")]), 0.0)

    def test_bad_inputs_cannot_supply_forecasts(self):
        for bad in (np.nan, np.inf, 0., 1.):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                price_probabilities(dict(self.prices, away=bad))
        with self.assertRaises(ValueError):
            fit_weight([])
        with self.assertRaises(ValueError):
            paired_matrices(self.prices, dict(line=2.5, over=2, under=2), 1.01)


def load_tests(loader, standard_tests, pattern):
    study = Path(__file__).resolve().parents[2] / "research" / "20261124-single-book-score"
    evidence = unittest.TestLoader().discover(str(study), pattern="test_single_book_archive.py")
    standard_tests.addTests(evidence)
    return standard_tests
