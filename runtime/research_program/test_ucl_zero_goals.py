import unittest

import numpy as np

from research_program import ucl_zero_goals as model


class ZeroGoalAllocationTests(unittest.TestCase):
    def setUp(self):
        self.prior = np.zeros((model.base.SIZE, model.base.SIZE))
        self.prior[0, 0] = .15
        self.prior[1, 0] = .05
        self.prior[0, 2] = .10
        self.prior[1, 1] = .40
        self.prior[2, 0] = .30

    def test_adjustment_keeps_each_total_and_moves_only_supported_cells(self):
        for dose in (.5, 1.):
            with self.subTest(dose=dose):
                adjusted = model.adjust(self.prior, [.2, .7], dose)
                np.testing.assert_allclose(model.totals(adjusted), model.totals(self.prior), rtol=0, atol=1e-15)
                np.testing.assert_array_equal(adjusted[self.prior == 0.], 0.)
                self.assertEqual(adjusted[0, 0], self.prior[0, 0])
                self.assertGreater(adjusted[2, 0], self.prior[2, 0])

    def test_fit_recovers_away_blanks_and_counts_scores_outside_grid(self):
        scores = [(2, 0)] * 40 + [(model.base.SIZE, 0)]
        beta, excluded = model.fit([self.prior] * len(scores), scores)
        self.assertGreater(beta[1], 0.)
        self.assertGreater(beta[1], beta[0])
        self.assertEqual(excluded, 1)

    def test_change_only_reorders_scores_and_zero_keeps_archived_choice(self):
        original = dict(primary='1-1', top3=['1-1', '2-0', '0-2'],
                        wdl='draw', total_side='under', total_line=2.25)
        zero = model.adjust(self.prior, [0., 1.], 0.)
        self.assertIs(model.selection(zero, self.prior, original), original)
        self.assertIs(model.adjust(self.prior, [0., 0.], 1.), self.prior)
        adjusted = model.adjust(self.prior, [0., 1.], 1.)
        selected = model.selection(adjusted, self.prior, original)
        self.assertEqual(selected['primary'], '2-0')
        self.assertEqual(len(set(selected['top3'])), 3)
        self.assertEqual([selected[k] for k in ('wdl', 'total_side', 'total_line')], ['draw', 'under', 2.25])
        self.assertEqual(original['primary'], '1-1')
