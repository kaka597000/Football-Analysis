import unittest

import numpy as np

from research_program import europe_close_score_agreement as agreement


class CloseScoreAgreementTests(unittest.TestCase):
    def setUp(self):
        self.reference = ['1-1', '1-0', '2-1']
        self.grid = np.full((3, 3), .295 / 3)
        for score, probability in zip(self.reference+['0-1', '2-0', '0-0'],
                                      [.14, .132, .117, .114, .107, .095], strict=True):
            self.grid[agreement.cell(score)] = probability

    def test_pools_preserve_original_rank_and_probability_limits(self):
        self.assertEqual(agreement.pools(self.grid, self.reference), (['1-1', '1-0'], ['0-1', '2-0']))
        self.assertEqual(agreement.pools(self.grid, ['1-0', '1-1', '2-1']), (['1-0'], []))

    def test_both_models_must_support_a_permitted_change(self):
        first, third = agreement.pools(self.grid, self.reference)
        a, b = agreement.choose_scores(self.reference, first, third, ['1-0', '0-1', '2-1'], ['1-0', '2-0', '0-1'])
        self.assertEqual(a, ['1-0', '1-1', '2-1'])
        self.assertEqual(b, ['1-0', '1-1', '0-1'])
        a, b = agreement.choose_scores(self.reference, first, third, ['0-1', '1-1', '1-0'], ['2-0', '1-0', '1-1'])
        self.assertEqual(a, self.reference)
        self.assertEqual(b, self.reference)

    def test_third_comparison_respects_both_model_rankings(self):
        first, third = agreement.pools(self.grid, self.reference)
        a, b = agreement.choose_scores(self.reference, first, third, ['0-1', '2-1', '1-0'], ['2-1', '0-1', '1-0'])
        self.assertEqual(a, b)
        self.assertEqual(b, self.reference)


if __name__ == '__main__':
    unittest.main()
