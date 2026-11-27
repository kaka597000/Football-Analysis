import unittest

import numpy as np

from research_program.j1_rolling_score_calibration import rolling_offsets


class RollingCalibrationTests(unittest.TestCase):
    def test_same_date_and_future_labels_cannot_change_offsets(self):
        query_days = np.array([1000])
        days = np.array([820, 999, 1000, 1000, 1001])
        queries = np.array([[2.5, .15]])
        contexts = np.repeat(queries, len(days), axis=0)
        observed = np.eye(5)
        expected = np.full((5, 5), .2)

        earlier_only = rolling_offsets(query_days, queries, days[:2], contexts[:2], observed[:2], expected[:2])
        full = rolling_offsets(query_days, queries, days, contexts, observed, expected)
        observed[2:] = np.array([[1., 0., 0., 0., 0.]] * 3)
        changed = rolling_offsets(query_days, queries, days, contexts, observed, expected)

        for original, extended, relabeled in zip(earlier_only, full, changed, strict=True):
            np.testing.assert_array_equal(original, extended)
            np.testing.assert_array_equal(original, relabeled)
        self.assertEqual(full[2][0, 0], 2.)
        self.assertAlmostEqual(full[2][0, 1], .5 + 2 ** (-1. / 180.))
