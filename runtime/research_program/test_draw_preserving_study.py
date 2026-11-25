import unittest
from unittest.mock import patch

import numpy as np

from research_program import all_scope_model as base
from research_program import draw_preserving_study as study


def prior_grid():
    value = np.arange(1, base.H.size + 1, dtype=float).reshape(base.H.shape)
    return value / value.sum()


class DrawPreservingStudyTests(unittest.TestCase):
    def test_validation_selects_only_preregistered_score_doses_and_ties_to_lower(self):
        prior = prior_grid()
        prior[1, 0] = 1000.0
        prior /= prior.sum()
        game = dict(id='one', competition='eng.1', date='2024-08-01', home_goals=1, away_goals=0)
        r7 = base.pick(prior, None)
        with patch.object(study.model, 'choices', wraps=study.model.choices) as choices, \
             patch.object(study.r21.model, 'fit', side_effect=AssertionError('R21 coefficients must stay fixed')):
            chosen, trials = study._selection([prior], [prior], [game], [r7])
        self.assertEqual(chosen, 0.0)
        self.assertEqual([row['dose'] for row in trials], [0.0, 0.5, 1.0])
        self.assertEqual(choices.call_count, 3)
        self.assertTrue(all(row['first_hits'] == 1 and row['top3_hits'] == 1 for row in trials))

    def test_r21_input_pin_matches_independent_reference_and_repeat(self):
        self.assertEqual(study.PROTOCOL_SHA, '71ffd263c1d1835ef008d594f39e6e6112c0902b2b872887ab080fb443ea594c')
        self.assertEqual(study.R21_INPUTS_SHA, '4426c16417eab543aedd54001357c846f10a6569fe9084a12333e260f611487d')
        self.assertEqual(study.WINDOWS[1:], ('evaluation','recent','november_extension'))


if __name__ == '__main__':
    unittest.main()
