import json
import unittest
from itertools import combinations

import numpy as np

from research_program import territory_hurdle as model


def fixed_slopes():
    fitted = model.fit([], [], [])
    fitted['scoring_beta'][0] = 1.
    fitted['positive_beta'][1] = 1.
    return fitted


def expected_margin(mass, scoring, positive):
    # Independent probability-space calculation on the supplied goal grid.
    tail = mass[1:] * np.exp(positive * np.arange(len(mass) - 1))
    binary = np.array([mass[0], mass[1:].sum() * np.exp(scoring)])
    binary /= binary.sum()
    return np.r_[binary[0], binary[1] * (tail / tail.sum())]


class TerritoryHurdleTests(unittest.TestCase):
    def setUp(self):
        # A dependent, rectangular anchor with a structural zero and a long tail.
        self.prior = np.array([[12, 4, 3, 1, 2, 1, 1],
                               [3, 11, 2, 0, 3, 1, 1],
                               [4, 2, 5, 2, 1, 2, 1],
                               [1, 3, 4, 6, 2, 1, 4]], dtype=float)
        self.prior /= self.prior.sum()

    def assert_projection(self, prior, joint, home, away):
        self.assertIsNot(joint, prior)
        self.assertEqual(joint.shape, prior.shape)
        self.assertTrue(np.isfinite(joint).all())
        self.assertTrue((joint >= 0.).all())
        self.assertAlmostEqual(joint.sum(), 1., places=12)
        np.testing.assert_allclose(joint.sum(axis=1), home, rtol=1e-12, atol=0.)
        np.testing.assert_allclose(joint.sum(axis=0), away, rtol=1e-12, atol=0.)
        np.testing.assert_array_equal(joint > 0., prior > 0.)
        # Every 2x2 odds ratio on common positive support, including tiny cells.
        for i, j in combinations(range(len(prior)), 2):
            common = (prior[i] > 0.) & (prior[j] > 0.)
            if np.count_nonzero(common) >= 2:
                before = np.log(prior[i, common]) - np.log(prior[j, common])
                after = np.log(joint[i, common]) - np.log(joint[j, common])
                np.testing.assert_allclose(after - before, (after - before)[0], atol=2e-12, rtol=0.)

    def test_both_margins_odds_ratios_and_inference_limits(self):
        prior = self.prior
        saved = prior.copy()
        x = np.zeros((2, 6))
        x[:, :2] = [[1., .34], [-.43, -1.]]
        fitted = fixed_slopes()
        joint = model.apply(prior, x, fitted)
        self.assert_projection(prior, joint, expected_margin(prior.sum(1), .65, .34),
                               expected_margin(prior.sum(0), -.43, -.65))
        np.testing.assert_array_equal(prior, saved)
        np.testing.assert_allclose(model.apply(prior.T, x[::-1], fitted), joint.T, atol=0., rtol=1e-12)

    def test_conditional_positive_goals_and_scoring_rate_move_independently(self):
        prior = self.prior.T
        home, away = prior.sum(1), prior.sum(0)
        x = np.zeros((2, 6))
        x[0, 1] = .4
        joint = model.apply(prior, x, fixed_slopes())
        changed = joint.sum(axis=1)
        self.assert_projection(prior, joint, expected_margin(home, 0., .4), away)
        self.assertAlmostEqual(changed[0], home[0], places=12)
        # Compare the complete positive conditional, all the way through score six.
        np.testing.assert_allclose(changed[1:] / changed[1], home[1:] / home[1] * np.exp(.4 * np.arange(6)))
        self.assertGreater(changed @ np.arange(len(home)), home @ np.arange(len(home)))

        x[0, :2] = [.4, 0.]
        joint = model.apply(prior, x, fixed_slopes())
        changed = joint.sum(axis=1)
        self.assert_projection(prior, joint, expected_margin(home, .4, 0.), away)
        self.assertLess(changed[0], home[0])
        np.testing.assert_allclose(changed[1:] / changed[1:].sum(), home[1:] / home[1:].sum())

    def test_fit_satisfies_pooled_penalized_likelihood_on_numpy_batch(self):
        rng = np.random.default_rng(14)
        n = 48
        anchors = rng.uniform(.05, 1., (n, 4, 7))
        anchors /= anchors.sum(axis=(1, 2), keepdims=True)
        x = rng.uniform(-1., 1., (n, 2, 6))
        scores = np.column_stack((rng.integers(4, size=n), rng.integers(7, size=n)))
        fitted = model.fit(anchors, x, scores)
        self.assertEqual(json.loads(json.dumps(fitted, allow_nan=False)), fitted)
        self.assertEqual((fitted['training_games'], fitted['fit_games'], fitted['scoring_fit_sides'],
                          fitted['positive_fit_sides']), (n, n, 2 * n, np.count_nonzero(scores)))
        self.assertEqual((fitted['ridge'], fitted['coefficient_bound'], fitted['shift_limit']), (24., 1.5, .65))
        scoring, positive = np.array(fitted['scoring_beta']), np.array(fitted['positive_beta'])
        ds, dp = 24. * scoring, 24. * positive
        for matrix, features, actual in zip(anchors, x, scores, strict=True):
            for mass, side, g in zip((matrix.sum(1), matrix.sum(0)), features, actual, strict=True):
                tail = mass[1:].sum() * np.exp(side @ scoring)
                ds += side * (tail / (mass[0] + tail) - (g > 0))
                if g > 0:
                    excess = np.arange(len(mass) - 1)
                    weights = mass[1:] * np.exp(excess * (side @ positive))
                    dp += side * (weights @ excess / weights.sum() - (g - 1))
        np.testing.assert_allclose([ds, dp], 0., atol=3e-6)
        swapped = model.fit(anchors.transpose(0, 2, 1), x[:, ::-1], scores[:, ::-1])
        for name in ('scoring_beta', 'positive_beta'):
            np.testing.assert_allclose(swapped[name], fitted[name], atol=1e-10)

    def test_training_prefix_and_held_out_labels_are_distinct(self):
        n, cut = 256, 64
        anchors = np.repeat(self.prior[None], n, axis=0)
        x = np.zeros((n, 2, 6))
        x[:, :, 0] = [1., -1.]
        scores = np.tile([3, 0], (n, 1))
        train = model.fit(anchors[:cut], x[:cut], scores[:cut])
        stored = json.dumps(train, allow_nan=False)
        issued = model.apply(self.prior, x[cut], train)
        home, away = self.prior.sum(1), self.prior.sum(0)
        self.assertTrue(train['scoring_beta'][0] > 0. and train['positive_beta'][0] > 0.)
        self.assertLess(issued.sum(1)[0], home[0])
        self.assertGreater(issued.sum(1) @ np.arange(len(home)), home @ np.arange(len(home)))
        self.assertGreater(issued.sum(0)[0], away[0])

        scores[cut:] = [0, 6]
        future = model.fit(anchors[cut:], x[cut:], scores[cut:])
        self.assertEqual(future['scoring_beta'][0], -1.5)
        self.assertLess(future['positive_beta'][0], 0.)
        self.assertEqual(model.fit(anchors[:cut], x[:cut], scores[:cut]), train)
        np.testing.assert_array_equal(model.apply(self.prior, x[cut], json.loads(stored)), issued)
        self.assertGreater(model.apply(self.prior, x[cut], future).sum(1)[0], home[0])

    def test_stage_labels_do_not_leak_into_each_other(self):
        n = 20
        anchors = [self.prior] * n
        x = np.ones((n, 2, 6))
        scores = np.tile([0, 1], (n, 1))
        low = model.fit(anchors, x, scores)
        scores[:, 1] = 6
        high = model.fit(anchors, x, scores)
        self.assertEqual(low['scoring_beta'], high['scoring_beta'])
        self.assertLess(low['positive_beta'][0], 0.)
        self.assertGreater(high['positive_beta'][0], 0.)
        x[:, 0] = -1.
        zero_features_changed = model.fit(anchors, x, scores)
        self.assertEqual(high['positive_beta'], zero_features_changed['positive_beta'])
        self.assertNotEqual(high['scoring_beta'], zero_features_changed['scoring_beta'])

    def test_xg_only_uses_first_two_columns_in_both_fit_and_apply(self):
        n = 32
        anchors = [self.prior] * n
        x = np.zeros((n, 2, 6))
        x[:, :, 4] = [1., -1.]
        scores = np.tile([3, 0], (n, 1))
        full = model.fit(anchors, x, scores)
        xg = model.fit(anchors, x, scores, xg_only=True)
        self.assertEqual(xg['training_games'], full['training_games'])
        self.assertEqual(xg['fit_games'], 0)
        self.assertEqual(xg['features'], full['features'][:2])
        self.assertIs(model.apply(self.prior, x[0], xg), self.prior)
        self.assertIsNot(model.apply(self.prior, x[0], full), self.prior)
        x[:, :, 0] = [.6, -.3]
        prefix = model.fit(anchors, x[:, :, :2], scores, xg_only=True)
        x[:, :, 2:] = np.nan
        self.assertEqual(model.fit(anchors, x, scores, xg_only=True), prefix)
        x[:, :, 2:] = -1.
        self.assertEqual(model.fit(anchors, x, scores, xg_only=True), prefix)
        np.testing.assert_array_equal(model.apply(self.prior, x[0], prefix),
                                      model.apply(self.prior, x[0, :, :2], prefix))
        unused = x.astype(object)
        unused[:, :, 2:] = 'unavailable'
        self.assertEqual(model.fit(anchors, unused, scores, xg_only=True), prefix)
        np.testing.assert_array_equal(model.apply(self.prior, unused[0], prefix),
                                      model.apply(self.prior, x[0], prefix))
        x[0, :, :2] = np.nan
        self.assertIs(model.apply(self.prior, x[0], prefix), self.prior)

    def test_empty_or_neutral_training_and_zero_effect_preserve_identity(self):
        x = np.ones((2, 6))
        for empty in ([], np.empty((0, 4, 7))):
            fitted = model.fit(empty, [], [])
            self.assertEqual(fitted['fit_games'], 0)
            self.assertIs(model.apply(self.prior, x, fitted), self.prior)
        fitted = model.fit([self.prior], [np.zeros_like(x)], [[3, 6]])
        self.assertEqual(fitted['scoring_fit_sides'], 0)
        self.assertIs(model.apply(self.prior, x, fitted), self.prior)
        fitted = fixed_slopes()
        self.assertIs(model.apply(self.prior, np.zeros_like(x), fitted), self.prior)
        # Both input sides have signal but both projected shifts cancel exactly.
        fitted['scoring_beta'][:2] = [1., -1.]
        fitted['positive_beta'][:2] = [-.5, .5]
        self.assertIs(model.apply(self.prior, x, fitted), self.prior)

    def test_scoreless_training_fits_only_the_scoring_hurdle(self):
        x = np.zeros((24, 2, 6))
        x[:, :, 0] = 1.
        fitted = model.fit([self.prior] * len(x), x, np.zeros((len(x), 2)))
        self.assertEqual((fitted['scoring_fit_sides'], fitted['positive_fit_sides']), (48, 0))
        self.assertLess(fitted['scoring_beta'][0], 0.)
        self.assertEqual(fitted['positive_beta'], [0.] * 6)
        changed = model.apply(self.prior, x[0], fitted)
        self.assertGreater(changed.sum(1)[0], self.prior.sum(1)[0])
        self.assertGreater(changed.sum(0)[0], self.prior.sum(0)[0])

    def test_exact_zero_and_one_scoring_rates_and_unusable_observations(self):
        prior = np.zeros((4, 3))
        prior[[1, 3], 0] = [.7, .3]
        x = np.ones((2, 6))
        x[0, 1] = 0.
        self.assertIs(model.apply(prior, x, fixed_slopes()), prior)
        x[0, 1] = .4
        changed = model.apply(prior, x, fixed_slopes())
        self.assert_projection(prior, changed, expected_margin(prior.sum(1), 0., .4), prior.sum(0))
        scores = [[1, 0], [3, 0], [2, 4], [None, 1], [20, 0]]
        fitted = model.fit([prior] * 5, [x] * 5, scores)
        self.assertEqual((fitted['scoring_fit_sides'], fitted['positive_fit_sides'], fitted['fit_games']),
                         (0, 2, 2))
        self.assertEqual(fitted['scoring_beta'], [0.] * 6)
        self.assertTrue(np.isfinite(fitted['positive_beta']).all())
        # A positive outside the goal grid can still inform a nondegenerate scoring rate.
        fitted = model.fit([self.prior], [x], [[20, None]])
        self.assertEqual((fitted['scoring_fit_sides'], fitted['positive_fit_sides']), (1, 0))
        self.assertEqual(fitted['positive_beta'], [0.] * 6)

    def test_sparse_support_rare_rates_and_infeasible_margins(self):
        x = np.zeros((2, 6))
        x[:, 0] = [.05, -.03]
        fitted = fixed_slopes()
        thin = np.array([[.47, 1e-120, 0.], [1e-120, .53, 0.], [0., 0., 0.]])
        rare = np.array([[.3, .7], [.25e-250, .75e-250], [.59e-250, .41e-250]])
        for prior in (thin, rare, rare[::-1], rare.T):
            changed = model.apply(prior, x, fitted)
            self.assert_projection(prior, changed, expected_margin(prior.sum(1), .05, 0.),
                                   expected_margin(prior.sum(0), -.03, 0.))
        diagonal = np.diag([.47, .53])
        self.assertIs(model.apply(diagonal, x, fitted), diagonal)
        x[1] = x[0]
        same = expected_margin(diagonal.sum(1), .05, 0.)
        self.assert_projection(diagonal, model.apply(diagonal, x, fitted), same, same)

        blocks = np.full((5, 5), 1e-60)
        np.fill_diagonal(blocks, [2, 3, 4, 3, 2])
        blocks /= blocks.sum()
        x[:, :2] = [[.05, .2], [.049, .21]]
        self.assert_projection(blocks, model.apply(blocks, x, fitted),
                               expected_margin(blocks.sum(1), .05, .2),
                               expected_margin(blocks.sum(0), .049, .21))

    def test_unrepresentable_cell_or_margin_inherits_anchor(self):
        x = np.zeros((2, 6))
        x[:, 0] = [.05, -.03]
        fitted = fixed_slopes()
        # The first would round a required positive cell to zero. The second
        # retains a subnormal cell but distorts the sole odds ratio by >1e-4.
        for bridge in (1e-250, 1e-161):
            prior = np.array([[.47, bridge], [bridge, .53]])
            with np.errstate(all='raise'):
                self.assertIs(model.apply(prior, x, fitted), prior)
        prior = np.array([[1.], [np.finfo(float).smallest_subnormal]])
        self.assertIs(model.apply(prior, x, fitted), prior)

    def test_rejects_misaligned_batches_and_invalid_probability_anchors(self):
        x = np.zeros((2, 6))
        with self.assertRaises(ValueError):
            model.fit([self.prior], [], [])
        with self.assertRaises(ValueError):
            model.fit([self.prior], [x[:, :2]], [[1, 2]])
        with self.assertRaises(ValueError):
            model.fit([self.prior], [x], [[.5, 1]])
        with self.assertRaises(ValueError):
            model.apply(np.zeros((3, 4)), x, fixed_slopes())


if __name__ == '__main__':
    unittest.main()
