"""Unit tests for the Egidi blend research candidate. Synthetic rows only."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from scipy.stats import poisson

sys.path.insert(0, str(Path(__file__).resolve().parent))

from blend import blend_batch
from constants import Config, season_start_for
from dixon_coles import poisson_objective, score_matrices
from load import load_jsonl, matches_from_football_data_file
from market import shin_probabilities
from metrics import hits_from_lists, mcnemar
from walkforward import walk_forward


class DistributionTests(unittest.TestCase):
    def test_zero_nuisance_matches_renormalised_independent_poisson(self):
        cap = 8
        lh, la = 1.35, 0.95
        got = score_matrices([lh], [la], 0.0, 0.0, 0.0, cap)[0]
        ks = np.arange(cap + 1)
        expect = np.outer(poisson.pmf(ks, lh), poisson.pmf(ks, la))
        expect /= expect.sum()
        np.testing.assert_allclose(got, expect, atol=1e-10)

    def test_negative_rho_lifts_low_score_diagonal(self):
        base = score_matrices([1.3], [1.2], 0.0, 0.0, 0.0, 8)[0]
        adj = score_matrices([1.3], [1.2], -0.05, 0.0, 0.0, 8)[0]
        self.assertGreater(adj[0, 0] + adj[1, 1], base[0, 0] + base[1, 1])

    def test_diagonal_inflation_increases_draw_mass(self):
        base = score_matrices([1.4], [1.1], 0.0, 0.0, 0.0, 8)[0]
        inflated = score_matrices([1.4], [1.1], 0.0, 0.0, 0.12, 8)[0]
        self.assertGreater(np.trace(inflated), np.trace(base))

    def test_shared_poisson_raises_goal_covariance(self):
        def cov(matrix):
            goals = np.arange(matrix.shape[0])
            mean_h = (goals * matrix.sum(axis=1)).sum()
            mean_a = (goals * matrix.sum(axis=0)).sum()
            cross = (goals[:, None] * goals[None, :] * matrix).sum()
            return cross - mean_h * mean_a
        plain = score_matrices([1.5], [1.2], 0.0, 0.0, 0.0, 10)[0]
        shared = score_matrices([1.5], [1.2], 0.0, 0.2, 0.0, 10)[0]
        self.assertGreater(cov(shared), cov(plain) + 0.02)

    def test_strength_gradient_matches_finite_difference(self):
        rng = np.random.default_rng(0)
        n_teams = 3
        home = rng.integers(0, n_teams, 12)
        away = rng.integers(0, n_teams, 12)
        away = np.where(away == home, (away + 1) % n_teams, away)
        hg = rng.integers(0, 4, 12).astype(float)
        ag = rng.integers(0, 4, 12).astype(float)
        objective = poisson_objective(home, away, hg, ag, np.ones(12), np.ones(12), n_teams, 1.0)
        point = np.zeros(2 + 2 * n_teams)
        point[0] = 0.2
        point[1] = 0.1
        point[2:] = rng.normal(scale=0.05, size=2 * n_teams)
        _, grad = objective(point)
        eps = 1e-6
        numeric = np.zeros_like(point)
        for i in range(len(point)):
            step = np.zeros_like(point)
            step[i] = eps
            numeric[i] = (objective(point + step)[0] - objective(point - step)[0]) / (2 * eps)
        np.testing.assert_allclose(grad, numeric, atol=1e-4)


class MarketBlendTests(unittest.TestCase):
    def test_fair_book_is_unchanged(self):
        probs, z = shin_probabilities([2.0, 4.0, 4.0])
        np.testing.assert_allclose(probs, [0.5, 0.25, 0.25], atol=1e-8)
        self.assertEqual(z, 0.0)

    def test_overround_sums_to_one_and_keeps_order(self):
        probs, z = shin_probabilities([1.7, 3.6, 5.2])
        self.assertAlmostEqual(float(probs.sum()), 1.0, places=8)
        self.assertGreater(z, 0.0)
        self.assertGreater(probs[0], probs[1])
        self.assertGreater(probs[1], probs[2])

    def test_full_1x2_weight_moves_marginal_toward_market(self):
        model = score_matrices([0.6], [0.55], 0.0, 0.0, 0.0, 6)
        market = np.array([0.72, 0.18, 0.10])
        blended = blend_batch(model, 1.0, 0.0, 0.0, market.reshape(1, 3), np.array([np.nan]), [None])
        g = np.arange(blended.shape[1])
        home, away = np.meshgrid(g, g, indexing='ij')
        ph = float(blended[0, home > away].sum())
        base = float(model[0, home > away].sum())
        self.assertLess(abs(ph - 0.72), abs(base - 0.72))
        self.assertAlmostEqual(ph, 0.72, places=5)

    def test_mcnemar_balanced_discordance(self):
        result = mcnemar([True, True, False, False], [True, False, True, False])
        self.assertEqual(result['n_discordant'], 2)
        self.assertEqual(result['n_candidate_hit_baseline_miss'], 1)
        self.assertAlmostEqual(result['p_value'], 1.0)

    def test_always_one_one_list_has_no_extra_scores(self):
        hits = hits_from_lists(np.array([[1]]), np.array([[1]]), np.array([2]), np.array([0]))
        self.assertFalse(bool(hits[1][0]))
        self.assertFalse(bool(hits[3][0]))
        hits = hits_from_lists(np.array([[1]]), np.array([[1]]), np.array([1]), np.array([1]))
        self.assertTrue(bool(hits[1][0]))
        self.assertTrue(bool(hits[3][0]))


class LoaderTests(unittest.TestCase):
    def test_football_data_csv_prefers_closing_and_does_not_invent_scores_or_korea(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '2024_E0.csv'
            path.write_text(
                'Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,B365H,B365D,B365A,PSCH,PSCD,PSCA,PC>2.5,PC<2.5\n'
                'E0,16/08/2024,Alpha,Beta,2,1,H,1.8,3.5,4.4,1.7,3.6,5.0,1.91,1.91\n'
                'E0,20/05/2024,Gamma,Delta,1,1,D,2.5,3.2,2.8,,,,,,\n'
                'E0,16/08/2024,Alpha,Beta,,,,\n',
                encoding='utf-8')
            rows = matches_from_football_data_file(path)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].league, 'eng.1')
        self.assertTrue(rows[0].odds_1x2_closing)
        self.assertEqual(rows[0].odds_1x2, (1.7, 3.6, 5.0))
        self.assertTrue(rows[0].odds_ou_closing)
        self.assertIsNone(rows[0].odds_cs)
        self.assertEqual(rows[1].season_start, 2023)
        self.assertFalse(rows[1].odds_1x2_closing)
        self.assertEqual(rows[1].odds_1x2, (2.5, 3.2, 2.8))
        self.assertIsNone(rows[1].odds_ou)
        self.assertNotIn('kor.1', {r.league for r in rows})

    def test_jsonl_calendar_and_cross_seasons(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'rows.jsonl'
            path.write_text(
                json.dumps({'league': 'jpn.1', 'date': '2024-03-02', 'home': 'Tokyo', 'away': 'Kobe',
                            'home_goals': 1, 'away_goals': 0}) + '\n'
                + json.dumps({'league': 'kor.1', 'date': '2024-05-05', 'home': 'Ulsan', 'away': 'Jeonbuk',
                              'home_goals': 2, 'away_goals': 2}) + '\n'
                + json.dumps({'league': 'aus.1', 'date': '2024-05-05', 'home': 'Sydney', 'away': 'Melbourne',
                              'home_goals': 1, 'away_goals': 1, 'odds_cs': {'1-1': 7.5, '1-0': 8.0}}) + '\n',
                encoding='utf-8')
            rows = {r.league: r for r in load_jsonl(path)}
        self.assertEqual(rows['jpn.1'].season_start, 2024)
        self.assertEqual(rows['kor.1'].season_start, 2024)
        self.assertEqual(rows['aus.1'].season_start, 2023)
        self.assertEqual(season_start_for('eng.1', '2024-08-16'), 2024)
        self.assertIsNone(season_start_for('jpn.1', '2026-03-01'))

    def test_downloader_source_does_not_call_espn(self):
        text = Path(__file__).resolve().parent.joinpath('load.py').read_text(encoding='utf-8')
        self.assertNotIn('espn.com', text)
        self.assertNotIn('kleague', text.lower())
        backtest = Path(__file__).resolve().parent.joinpath('backtest.py').read_text(encoding='utf-8')
        self.assertNotIn('import predict_active', backtest)
        self.assertNotIn('from predict_active', backtest)


class WalkForwardTests(unittest.TestCase):
    def test_synthetic_origins_keep_asia_out_of_training(self):
        config = Config().tiny()
        rng = np.random.default_rng(1)
        teams = [f'T{i}' for i in range(6)]
        attack = {t: float(v) for t, v in zip(teams, np.linspace(-0.25, 0.25, 6))}

        def season(league, year, odds):
            rows = []
            day = 0
            for home in teams:
                for away in teams:
                    if home == away:
                        continue
                    lam_h = float(np.exp(-0.05 + 0.25 + attack[home] - attack[away]))
                    lam_a = float(np.exp(-0.05 + attack[away] - attack[home]))
                    rows.append(type('M', (), {})())
                    # real Match objects
                    from load import Match
                    rows[-1] = Match(
                        match_id=f'{league}-{year}-{home}-{away}', league=league, season=str(year),
                        season_start=year, date=f'{year}-09-{1 + (day % 27):02d}',
                        home=home, away=away,
                        home_goals=int(rng.poisson(lam_h)), away_goals=int(rng.poisson(lam_a)),
                        odds_1x2=(1.9, 3.4, 4.1) if odds else None,
                        odds_1x2_closing=bool(odds),
                        source='synthetic')
                    day += 1
            return rows

        matches = []
        for year in range(2012, 2018):
            matches.extend(season('eng.1', year, True))
        for year in range(2014, 2018):
            matches.extend(season('aus.1', year, False))
        report, scored = walk_forward(matches, config)
        self.assertFalse(report['wired_into_predict_active'])
        self.assertFalse(report['formal_promotion'])
        self.assertGreater(report['metrics']['europe_pooled']['n'], 0)
        self.assertGreater(report['metrics']['ood_asia_pooled']['n'], 0)
        self.assertTrue(all(fold['leakage_guard_passed'] for fold in report['folds']))
        for fold in report['folds']:
            if fold['scope'] == 'europe':
                self.assertNotIn('aus.1', fold['ranker'].get('training_leagues', []))
                self.assertNotIn('aus.1', fold['weight_fit'].get('leagues', []))
            else:
                self.assertEqual(fold['league'], 'aus.1')
                self.assertTrue(fold['weight_fit_excludes_this_league'])
                self.assertTrue(fold['ranker_excludes_this_league'])
        self.assertTrue(all(row['league'] != 'aus.1' or row['ood'] for row in scored))
        self.assertTrue(any(row['league'] == 'eng.1' and not row['ood'] for row in scored))


if __name__ == '__main__':
    unittest.main()
