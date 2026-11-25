from copy import deepcopy
from datetime import date, timedelta
import json
import unittest

import numpy as np
from scipy.special import expit

from research_program import penalty_opportunity as model


def event(mid, day, home=('1', 'Alpha'), away=('2', 'Bravo'), pair=(.6, 0.)):
    h, a = pair
    return dict(competition='eng.1', source_match_id=mid, date=day, home=home[1], away=away[1],
                home_id=home[0], away_id=away[0], source_sha256='a' * 64,
                source_history_sides=dict(home=dict(xG=1 + h, npxG=1, xGA=2 + a, npxGA=2),
                                          away=dict(xG=2 + a, npxG=2, xGA=1 + h, npxGA=1)))


def game(day, home='Alpha', away='Bravo', **fields):
    return dict(competition='eng.1', date=day, home=home, away=away, **fields)


class DesignTests(unittest.TestCase):
    def test_all_eight_values_cross_and_accept_only_rounding_sized_discrepancies(self):
        row = event('fixture', '2025-01-01', pair=(.6, .4))
        self.assertEqual(model._penalty_pair(row), (.6, .4))
        for side, values in row['source_history_sides'].items():
            for field in values:
                for bad in (None, True, -0.01, float('inf'), float('nan'), 'invalid'):
                    with self.subTest(side=side, field=field, bad=bad):
                        invalid = deepcopy(row)
                        invalid['source_history_sides'][side][field] = bad
                        self.assertIsNone(model._penalty_pair(invalid))

        home, away = (row['source_history_sides'][side] for side in ('home', 'away'))
        home['xG'], away['xGA'], home['xGA'] = '0.998', '1.000', '2.402'
        self.assertEqual(model._penalty_pair(row), (0., .401))
        home['xG'] = '0.997999'
        self.assertIsNone(model._penalty_pair(row))
        home['xG'], home['xGA'] = '0.998', '2.402001'
        self.assertIsNone(model._penalty_pair(row))

    def test_recent_twelve_slots_include_missing_and_center_on_all_earlier_league_games(self):
        origin = date(2025, 1, 1)
        recent = [event(str(i), (origin + timedelta(days=i)).isoformat()) for i in range(12)]
        del recent[0]['source_history_sides']['away']['npxGA']
        old = event('old', '2024-01-01', pair=(2., 0.))
        neutral = event('neutral', '2024-01-02', ('3', 'Older'), ('4', 'Other'), pair=(0., 0.))
        foreign = dict(event('foreign', '2025-01-03', pair=(10., 10.)), competition='esp.1')
        design = model.Design([foreign, neutral, old, *recent[::-1]], 'eng.1')
        target = game('2025-02-01')
        x, meta = design.context(target, target['date'])
        mean = (1 + 11 * .3) / 13
        self.assertAlmostEqual(meta['league_mean'], mean)
        self.assertEqual(meta['league_n'], 26)
        for side in ('home', 'away'):
            self.assertEqual(meta[side]['n'], 11)
            self.assertEqual([g['source_id'] for g in meta[side]['games']], [str(i) for i in range(12)])
            missing = meta[side]['games'][0]
            self.assertFalse(missing['valid'])
            self.assertIsNone(missing['xg_for'])
            self.assertEqual(missing['source_sha256'], 'a' * 64)
        np.testing.assert_allclose(x, [11 * (.6 - mean) / 19] * 2 + [-11 * mean / 19] * 2)
        swapped, _ = design.context(dict(target, home='Bravo', away='Alpha'), target['date'])
        np.testing.assert_array_equal(swapped, x[[2, 3, 0, 1]])

        x, meta = design.context(dict(target, home='Older', away='Unknown'), target['date'])
        np.testing.assert_allclose(x, [-mean / 9, 0., 0., -mean / 9])
        self.assertEqual(meta['home']['n'], 1)  # The contract has no day limit on these 12 slots.
        self.assertFalse(meta['home']['missing'])
        self.assertEqual(meta['home']['games'][0]['xg_for'], 0.)

    def test_walk_and_forward_context_exclude_the_whole_cutoff_day(self):
        days = [f'2025-01-0{i}' for i in range(1, 5)]
        source = [event(str(i), day) for i, day in enumerate(days)]
        panel = [game(day, 'Panel Name', 'Other Name') for day in days]
        same_day = game('2025-01-04T23:59:59Z', 'Panel Name', 'Other Name', home_id='wrong', home_goals=99)
        tomorrow = game('2025-01-05', 'Panel Name', 'Other Name')
        same_source_day = event('4', '2025-01-05 00:00:01', pair=(7., 0.))
        future_source = event('5', '2025-01-06 01:00:00', home=('7', 'New Name'), pair=(15., 0.))
        inputs = [tomorrow, same_day, *reversed(panel)]
        full = model.Design([same_source_day, future_source, *reversed(source)], 'eng.1')
        x, info = full.walk(inputs)
        y, baseline = model.Design(source, 'eng.1').walk(inputs)
        np.testing.assert_array_equal(x, y)
        self.assertEqual(info, baseline)
        self.assertIsNone(info[1]['home']['source_team_id'])
        self.assertEqual(info[1], info[2])
        self.assertEqual((info[0]['home']['source_team_id'], info[0]['home']['method']), ('1', 'calendar'))
        self.assertEqual(info[0]['home']['n'], 4)
        self.assertAlmostEqual(info[0]['league_mean'], .3)
        _, issue = full.context(dict(tomorrow, date='2025-01-11'), '2025-01-05T23:59:59Z')
        self.assertEqual(issue['cutoff'], '2025-01-05')
        self.assertEqual(issue['home'], info[0]['home'])
        self.assertEqual(issue['league_mean'], info[0]['league_mean'])
        _, before = full.context(game('2025-01-10'), '2025-01-01')
        self.assertIsNone(before['league_mean'])
        self.assertEqual(before['home']['n'], 0)
        clipped, _ = full.context(game('2025-01-07', 'New Name', 'Unknown'), '2025-01-07')
        self.assertEqual(clipped[0], .75)
        self.assertEqual(full.walk([])[0].shape, (0, 4))

    def test_ambiguous_target_and_missing_source_identity_keep_independent_missingness(self):
        rows = [event('one', '2025-01-01'), event('two', '2025-01-02', ('3', 'Alpha')),
                event('missing-id', '2025-01-03', ('2', 'Bravo'), (None, 'Unknown'), pair=(9., 9.))]
        x, meta = model.Design(rows, 'eng.1').context(game('2025-01-04'), '2025-01-04')
        self.assertIsNone(meta['home']['source_team_id'])
        self.assertEqual(meta['home']['method'], 'ambiguous')
        self.assertTrue(meta['home']['missing'])
        self.assertEqual(meta['away']['n'], 2)
        self.assertEqual(meta['away']['games'][-1]['source_id'], 'missing-id')
        self.assertFalse(meta['away']['games'][-1]['valid'])
        self.assertEqual(meta['league_n'], 4)
        np.testing.assert_allclose(x, [0., .06, -.06, 0.])


class ModelTests(unittest.TestCase):
    def test_full_joint_tilt_and_home_away_mirror(self):
        prior = np.array([[.4, 0., .2], [.15, .22, .03]])
        h, a = np.indices(prior.shape)
        x = np.array([-.75, .2, .3, -.45])
        fit = dict(beta=[1.2, -.8])
        bh, ba = x.reshape(2, 2) @ fit['beta']
        expected = prior * np.exp(h * bh + a * ba)
        full = model.matrix(prior, x, fit)
        np.testing.assert_allclose(full, expected / expected.sum(), rtol=1e-14)
        np.testing.assert_allclose(model.matrix(prior.T, x[[2, 3, 0, 1]], fit), full.T, rtol=1e-14)
        self.assertEqual(full[0, 1], 0.)

        symmetric = model.matrix(np.full((4, 4), 1 / 16), [.1] * 4, dict(beta=[1., 1.]))
        np.testing.assert_array_equal(symmetric, symmetric.T)

        sparse = np.zeros((300, 3))
        sparse[0, 0], sparse[-1, -1] = 1., 1e-310
        with np.errstate(over='raise', invalid='raise', divide='raise'):
            stress = model.matrix(sparse, [.75] * 4, dict(beta=[2., 2.]))
        self.assertAlmostEqual(stress.sum(), 1.)
        self.assertGreater(stress[0, 0], 0.)
        self.assertEqual(np.argmax(stress), sparse.size - 1)

    def test_doses_preserve_signed_r7_directions_and_zero_change_and_rank_ties(self):
        prior = np.array([[.2, .3, .05], [.25, .1, .1]])
        full = np.array([[.15, .25, .05], [.25, .15, .15]])
        control = dict(primary='1-1', top3=['1-1', '1-2', '0-2'], wdl='away',
                       total_side='under', total_line=2.25)
        absent = model.matrix(prior, np.zeros(4), dict(beta=[2., -2.]))
        for dose, alternative in ((0., full), (.5, absent), (1., absent)):
            pick, grid = model.choices(prior, alternative, dose, control, 2.25)
            self.assertEqual(pick, control)
            np.testing.assert_array_equal(grid, prior)
        half, mixed = model.choices(prior, full, .5, control, 2.25)
        np.testing.assert_array_equal(mixed, .5 * (prior + full))
        pick, _ = model.choices(prior, full, 1., control, 2.25)
        self.assertEqual(pick['top3'], ['0-1', '1-0', '0-0'])
        self.assertEqual(pick['primary'], pick['top3'][0])
        for result in (pick, half):
            for key in ('wdl', 'total_side', 'total_line'):
                self.assertEqual(result[key], control[key])
        with self.assertRaisesRegex(ValueError, 'dose'):
            model.choices(prior, full, .25, control, 2.25)
        with self.assertRaisesRegex(ValueError, 'total line'):
            model.choices(prior, full, .5, control, 2.5)

    def test_exact_score_fit_optimizes_both_regularized_coefficients_with_bounds(self):
        prior = np.full((2, 2), .25)
        x = [.75, 0., 0., .75]
        n = 80
        fit = model.fit([prior] * n, [x] * n, [[1, 0]] * n)
        beta = np.array(fit['beta'])
        self.assertGreater(beta[0], 0.)
        self.assertLess(beta[1], 0.)
        gradient = 16 * beta + n * .75 * (expit(.75 * beta) - [1, 0])
        np.testing.assert_allclose(gradient, [0., 0.], atol=1e-6)
        posterior = model.matrix(prior, x, fit)
        self.assertAlmostEqual(fit['penalized_loss'], -n * np.log(posterior[1, 0]) + 8 * (beta @ beta))
        n = 512
        boundary = model.fit([prior] * n, [x] * n, [[1, 0]] * n)
        self.assertEqual(boundary['beta'], [2., -2.])

    def test_unsupported_scores_have_fixed_finite_loss_and_repeatable_weights(self):
        prior = np.full((2, 2), .25)
        sparse = np.array([[.3, .3], [.4, 0.]])
        x = [.6, .3, -.2, .1]
        base = model.fit([prior] * 10, [x] * 10, [[1, 0]] * 10)
        grids, features, scores = [prior] * 11 + [sparse], [x] * 12, [[1, 0]] * 10 + [[2, 0], [1, 1]]
        fit = model.fit(grids, features, scores)
        self.assertEqual(fit['beta'], base['beta'])
        self.assertEqual((fit['training_rows'], fit['outside_grid'], fit['zero_prior_cells']), (12, 1, 1))
        self.assertAlmostEqual(fit['penalized_loss'], base['penalized_loss'] - 2 * np.log(1e-300))
        self.assertEqual(json.dumps(fit, allow_nan=False), json.dumps(model.fit(grids, features, scores)))
        neutral = model.fit([prior], [[0.] * 4], [[1, 0]])
        self.assertEqual(neutral['beta'], [0., 0.])


if __name__ == '__main__':
    unittest.main()
