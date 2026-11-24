import unittest

import numpy as np
from scipy.special import expit

from research_program import same_line_motion as study
from research_program.three_arm_model import probabilities


def row():
    return {'latest_h2h': {'home': 2.2, 'draw': 3.4, 'away': 3.0},
            'opening_total': {'line': 2.5, 'over': 2.1, 'under': 1.8},
            'current_total': {'line': 2.5, 'over': 1.8, 'under': 2.1}}


class SameLineMotionTests(unittest.TestCase):
    def test_change_at_identical_line_moves_only_the_registered_target(self):
        r = row()
        a, b = study.paired_matrices(r, .5)
        status, movement = study.observation(r)
        self.assertEqual(status, 'same_line')
        np.testing.assert_allclose(probabilities(a), probabilities(b), rtol=0, atol=1e-12)
        np.testing.assert_array_equal(a == 0, b == 0)
        self.assertAlmostEqual(b.sum(), 1.)
        h, v = np.indices(b.shape)
        self.assertAlmostEqual(b[h+v > 2.5].sum(), expit(np.log(2.1/1.8) + .5*movement))

    def test_line_change_and_missing_opening_preserve_identical_forecasts(self):
        for opening, reason in [(None, 'opening_unavailable'),
                                ({'line': 3., 'over': 1.8, 'under': 2.1}, 'different_total_line')]:
            r = dict(row(), opening_total=opening)
            self.assertEqual(study.observation(r), (reason, 0.))
            a, b = study.paired_matrices(r, 1.)
            self.assertIs(a, b)
        for bad in (dict(row(), excluded='outside_scope'), dict(row(), latest_h2h={'home': 2.})):
            self.assertIsNone(study.observation(bad)[1])
            with self.assertRaises(ValueError):
                study.paired_matrices(bad, 1.)

    def test_half_stakes_and_push_do_not_receive_a_full_training_vote(self):
        r = row()
        for line, expected in [(2., None), (2.25, (0., .5)), (1.75, (1., .5))]:
            r['opening_total']['line'] = r['current_total']['line'] = line
            term = study.training_term(r, (1,1))
            self.assertEqual(term[2:] if term else None, expected)

    def test_fit_and_bounds_use_only_supplied_eligible_outcomes(self):
        self.assertIsNone(study.fit([(0., .2, 1., 1.)]*11))
        found = study.fit([(0., .2, 1., 1.)]*12)
        self.assertGreater(found, 0)
        self.assertEqual(study.fit([(0., .5, 0., 1.)]*30), -1.)
        with self.assertRaises(ValueError):
            study.paired_matrices(row(), 1.01)
