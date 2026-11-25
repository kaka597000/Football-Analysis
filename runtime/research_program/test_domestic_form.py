import unittest

import numpy as np

from research_program import all_scope_model as base
from research_program import domestic_form as model


def fixture(mid, day, h, a):
    return dict(competition='jpn.1', id=str(mid), date=day, home_id='1', away_id='2',
                home_goals=h, away_goals=a, source_sha256='a'*64)


class DomesticFormTest(unittest.TestCase):
    def test_cutoff_and_entire_target_day_are_excluded(self):
        history = model.DomesticHistory([fixture(1,'2025-01-03',1,0), fixture(2,'2025-01-09',9,0),
                                        fixture(3,'2025-01-10',12,0)])
        base_history = model.DomesticHistory([fixture(1,'2025-01-03',1,0)])
        for target, cutoff in [('2025-01-09', None), ('2025-01-10','2025-01-09')]:
            got, exposure = history.context(('1','2'), target, cutoff=cutoff)
            expected, _ = base_history.context(('1','2'), target, cutoff=cutoff)
            np.testing.assert_array_equal(got, expected)
            self.assertEqual(exposure, [1,1])
        np.testing.assert_array_equal(history.context(('1','2'),'2025-08-01')[0], np.zeros((2,4)))

    def test_orientation_venue_prior_and_unknown_opponent(self):
        history = model.DomesticHistory([fixture(7,'2026-01-01',3,0)])
        x, exposure = history.context(('1','91'), '2026-01-03')
        self.assertEqual(exposure, [1,0])
        self.assertAlmostEqual(x[0,0], np.log(8.6/7.))
        self.assertAlmostEqual(x[1,1], np.log(5.6/7.))
        np.testing.assert_array_equal(x[:,2:], [[.75,.25],[-.75,-.25]])
        swap, _ = history.context(('91','1'), '2026-01-03')
        np.testing.assert_array_equal(x, swap[::-1])
        with self.assertRaises(ValueError):
            model.DomesticHistory([fixture(7,'2026-01-01',3,0), fixture(7,'2026-01-03',1,1)])

    def test_zero_input_inherits_matrix_and_positive_shift_increases_home_mean(self):
        prior = np.exp(-base.H-base.A)
        prior /= prior.sum()
        x = np.zeros((2,4))
        fitted = dict(beta=[1.,0.,0.,0.])
        self.assertIs(model.adjust(prior, x, fitted), prior)
        x[0,0] = .7
        changed = model.adjust(prior, x, fitted)
        self.assertAlmostEqual(changed.sum(), 1.)
        self.assertGreater(np.sum(changed*base.H), np.sum(prior*base.H))
        self.assertAlmostEqual(np.sum(changed*base.A), np.sum(prior*base.A))
