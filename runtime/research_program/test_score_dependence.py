from datetime import date, timedelta
import unittest

import numpy as np

from research_program import score_dependence as model


class DependenceTests(unittest.TestCase):
    def test_copula_preserves_both_marginals_and_changes_only_joint(self):
        anchor = np.array([[.29, .13, .08], [.12, .18, .20]])
        fit = dict(version=model.VERSION, features=list(model.FEATURES), beta=[.7, .4, -.2])
        x = np.array([1., -.2, .4])
        new = model.matrix(anchor, x, fit)
        np.testing.assert_allclose(new.sum(axis=0), anchor.sum(axis=0), atol=1e-14)
        np.testing.assert_allclose(new.sum(axis=1), anchor.sum(axis=1), atol=1e-14)
        independent = np.outer(anchor.sum(axis=1), anchor.sum(axis=0))
        self.assertGreater(new[0, 0], independent[0, 0])
        self.assertLess(new[0, -1], independent[0, -1])
        np.testing.assert_allclose(model.matrix(anchor, x, dict(fit, beta=[0, 0, 0])), independent)
        with self.assertRaises(ValueError):
            model.matrix(2*anchor, x, fit)

    def test_fits_opposite_dependence_and_validates_marginal_support(self):
        n = 64
        anchors = np.full((n, 2, 2), .25)
        x = np.tile([1., 0., 0.], (n, 1))
        scores = np.arange(n) % 2
        same = np.column_stack((scores, scores))
        opposite = np.column_stack((scores, 1 - scores))
        for y, sign in ((same, 1), (opposite, -1)):
            weights = model.fit(anchors, x, y, constant=True)
            self.assertGreater(sign * weights['beta'][0], 0)
            new = model.matrix(anchors[0], x[0], weights)
            self.assertGreater(sign * (new[0, 0] - .25), 0)
        with self.assertRaises(ValueError):
            model.fit(anchors, x, same + 2)

    def test_conditioning_uses_only_earlier_slots_and_leaves_missing_in_window(self):
        day = date(2025, 3, 1)
        def record(i):
            value = .3 + .1*i
            return dict(competition='eng.1', source_match_id=str(i), date=(day+timedelta(days=i)).isoformat(),
                        home_id='a', away_id='b', home='Home', away='Away',
                        source_history_sides=dict(home=dict(npxG=value, npxGA=2*value),
                                                  away=dict(npxG=2*value, npxGA=value)))
        history = [record(i) for i in range(11)]
        history[8]['source_history_sides']['away']['npxGA'] = 7.
        event = dict(competition='eng.1', home='Home', away='Away', date='2025-03-11')
        source = model.Design(history, 'eng.1')
        design, info = source.walk([event])
        self.assertEqual(info[0]['home']['usable'], 7)
        self.assertEqual([r['source_id'] for r in info[0]['home']['games']], list(map(str, range(2, 10))))
        self.assertAlmostEqual(design[0, 1], 7/15)
        x, meta = source.context(event, cutoff='2025-03-08')
        self.assertEqual(meta['away']['usable'], 7)
        self.assertEqual(meta['away']['games'][-1]['source_id'], '6')
        other = model.Design(history[:7], 'eng.1')
        np.testing.assert_allclose(x, other.context(event, cutoff='2025-03-08')[0])
        unknown = dict(event, home='Unseen', away='Unknown')
        np.testing.assert_array_equal(source.context(unknown, cutoff='2025-03-08')[0], [1., 0., 0.])

    def test_conflicting_original_pair_is_not_silently_reused(self):
        row = dict(competition='eng.1', source_match_id='99', date='2025-03-01', home_id='a', away_id='b',
                   home='Home', away='Away', source_history_sides=dict(home=dict(npxG=1., npxGA=2.),
                                                                     away=dict(npxG=2., npxGA=1.)))
        bad = dict(row, source_history_sides={})
        with self.assertRaisesRegex(ValueError, 'conflicting npxG'):
            model.Design([row, bad], 'eng.1')


if __name__ == '__main__':
    unittest.main()
