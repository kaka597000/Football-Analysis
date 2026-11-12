"""Synthetic mathematical/time-contract tests, not football performance."""
import copy
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
import unittest
from research_program.elo_kelly_shadow import (
    EloConfig, elo_snapshot, kelly_snapshot, devig, expectation,
)
from research_program.mixture_selection_shadow import select


class EloTests(unittest.TestCase):
    def setUp(self):
        self.target = dict(match_id='target', cohort='eng.1', stage='regular',
                           kickoff_at='2026-06-04T18:00:00Z', cutoff_at='2026-06-04T12:00:00Z',
                           home='A', away='B', neutral=False)
        self.now = datetime(2026, 6, 4, 13, tzinfo=timezone.utc)
        self.row = dict(match_id='past', cohort='eng.1', stage='regular',
                        kickoff_at='2026-06-01T18:00:00Z', observed_at='2026-06-01T21:00:00Z',
                        home='A', away='B', neutral=False, home_goals=2, away_goals=0,
                        event_status='FT', score_basis='regulation_90')

    def snapshot(self, rows, config=EloConfig()):
        return elo_snapshot(rows, self.target, config, now=self.now)

    def test_equal_prior_is_expected_result_not_win_probability(self):
        r = self.snapshot([])
        self.assertEqual(r['expected_home_result'], .5)
        self.assertIsNone(r['wdl']); self.assertIsNone(r['score_matrix'])
        self.assertEqual(r['cold_start_teams'], ['A', 'B'])

    def test_win_zero_sum_and_underdog_update(self):
        r = self.snapshot([self.row])
        self.assertEqual((r['home_rating'], r['away_rating']), (1510., 1490.))
        self.assertGreater(20 * (1 - expectation(-200, 400)), 20 * (1 - expectation(200, 400)))

    def test_equal_draw_does_not_move_ratings(self):
        self.row['away_goals'] = 2
        r = self.snapshot([self.row]); self.assertEqual(r['rating_gap'], 0.)

    def test_late_capture_cannot_be_backdated_by_first_known_claim(self):
        self.row.update(observed_at='2026-06-04T12:01:00Z', first_known_at='2026-06-01T21:00:00Z')
        r = self.snapshot([self.row]); self.assertEqual(r['history_n'], 0)
        self.assertEqual(r['excluded']['captured_after_cutoff'], 1)

    def test_same_day_results_excluded(self):
        self.row.update(kickoff_at='2026-06-04T01:00:00Z', observed_at='2026-06-04T04:00:00Z')
        self.assertEqual(self.snapshot([self.row])['history_n'], 0)

    def test_other_competition_and_phase_do_not_update(self):
        a = dict(self.row, cohort='jpn.1'); b = dict(self.row, stage='final')
        self.assertEqual(self.snapshot([a, b])['history_n'], 0)

    def test_neutral_removes_explicit_home_bonus(self):
        cfg = EloConfig(home_advantage=50)
        nonneutral = self.snapshot([], cfg)['expected_home_result']
        self.target['neutral'] = True
        self.assertGreater(nonneutral, self.snapshot([], cfg)['expected_home_result'])

    def test_duplicate_alias_fixture_rejected(self):
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            self.snapshot([self.row, dict(self.row, match_id='alias')])

    def test_immutable_config_and_inputs(self):
        cfg = EloConfig(); before = copy.deepcopy(self.row)
        with self.assertRaises(FrozenInstanceError): cfg.scale = 600
        self.snapshot([self.row], cfg); self.assertEqual(self.row, before)

    def test_invalid_chronology_and_neutral_rejected(self):
        self.row['observed_at'] = '2026-06-01T18:05:00Z'
        with self.assertRaises(ValueError): self.snapshot([self.row])
        self.target['neutral'] = None
        with self.assertRaises(ValueError): self.snapshot([])

    def test_timezone_equivalence(self):
        a = self.snapshot([self.row])
        self.row['kickoff_at'] = '2026-06-02T02:00:00+08:00'
        b = self.snapshot([self.row]); self.assertEqual(a['home_rating'], b['home_rating'])

    def test_no_extreme_gap_overflow(self):
        self.assertEqual(expectation(1e6, 400), 1.)
        self.assertEqual(expectation(-1e6, 400), 0.)


class KellyTests(unittest.TestCase):
    def setUp(self):
        self.target = dict(match_id='target', cohort='eng.1', stage='regular',
                           cutoff_at='2026-06-04T12:00:00Z', kickoff_at='2026-06-04T18:00:00Z')
        self.now = datetime(2026, 6, 4, 13, tzinfo=timezone.utc)

    def quote(self, book, odds=(2., 3.5, 4.)):
        return {'match_id':'target', 'cohort':'eng.1', 'stage':'regular', 'provider':'P',
                'field':'market_quote', 'period':'regulation_90',
                'event_kickoff_at':self.target['kickoff_at'], 'observed_at':'2026-06-04T11:59:45Z',
                'value':{'bookmaker':book, 'market':'h2h', 'quoted_at':'2026-06-04T11:59:30Z',
                         'odds':dict(zip(('home','draw','away'), odds))}}

    def quotes(self):
        return [self.quote('TARGET'), self.quote('P1', (2.1, 3.2, 3.8)), self.quote('P2', (2.2, 3.3, 3.6))]

    def snapshot(self, rows):
        return kelly_snapshot(rows, self.target, provider='P', bookmaker='TARGET',
                              reference_bookmakers=['P1','P2'], max_age_seconds=60, now=self.now)

    def test_external_reference_formula_and_no_staking(self):
        rows = self.quotes(); r = self.snapshot(rows)
        p1, _ = devig(rows[1]['value']['odds']); p2, _ = devig(rows[2]['value']['odds'])
        self.assertAlmostEqual(r['indices']['home'], 2 * (p1['home'] + p2['home']) / 2)
        self.assertAlmostEqual(sum(r['consensus_wdl'].values()), 1)
        self.assertIsNone(r['stake_recommendation']); self.assertIsNone(r['score_matrix'])

    def test_self_reference_is_constant_and_not_allowed(self):
        odds = self.quote('TARGET')['value']['odds']; probabilities, payout = devig(odds)
        for side in probabilities: self.assertAlmostEqual(odds[side] * probabilities[side], payout)
        with self.assertRaises(ValueError):
            kelly_snapshot([], self.target, provider='P', bookmaker='TARGET',
                           reference_bookmakers=['TARGET','P1'], max_age_seconds=60, now=self.now)

    def test_missing_peer_does_not_change_panel(self):
        r = self.snapshot(self.quotes()[:-1])
        self.assertEqual(r['missing_books'], ['P2']); self.assertIsNone(r['indices'])

    def test_stale_peer_stays_unavailable(self):
        rows = self.quotes(); rows[-1]['value']['quoted_at'] = '2026-06-04T11:58:00Z'
        self.assertEqual(self.snapshot(rows)['missing_books'], ['P2'])

    def test_late_capture_never_used(self):
        rows = self.quotes(); rows[-1]['observed_at'] = '2026-06-04T12:01:00Z'
        self.assertEqual(self.snapshot(rows)['missing_books'], ['P2'])

    def test_quote_after_capture_rejected(self):
        rows = self.quotes(); rows[-1]['value']['quoted_at'] = '2026-06-04T12:00:00Z'
        with self.assertRaises(ValueError): self.snapshot(rows)

    def test_duplicate_feed_not_extra_book(self):
        rows = self.quotes(); duplicate = copy.deepcopy(rows[-1])
        duplicate['value']['quoted_at'] = '2026-06-04T19:59:30+08:00'
        self.assertEqual(self.snapshot(rows)['indices'], self.snapshot(rows+[duplicate])['indices'])

    def test_conflicting_duplicate_rejected(self):
        rows = self.quotes(); duplicate = copy.deepcopy(rows[-1]); duplicate['value']['odds']['home'] = 3.
        with self.assertRaisesRegex(ValueError, 'conflicting'): self.snapshot(rows+[duplicate])

    def test_other_provider_and_fixture_do_not_replace_peer(self):
        rows = self.quotes(); original = self.snapshot(rows)
        a = self.quote('P1'); a['provider'] = 'OTHER'
        b = self.quote('P2'); b['match_id'] = 'other-match'
        self.assertEqual(original['indices'], self.snapshot(rows+[a,b])['indices'])

    def test_latest_quote_chosen_and_inputs_unchanged(self):
        rows = self.quotes(); later = self.quote('TARGET', (2.4,3.4,3.3))
        later['value']['quoted_at'] = '2026-06-04T11:59:40Z'
        before = copy.deepcopy(rows)
        r = self.snapshot(rows+[later]); self.assertEqual(rows, before)
        self.assertEqual(r['quoted_at']['TARGET'], later['value']['quoted_at'])

    def test_invalid_odds_and_window_rejected(self):
        with self.assertRaises(ValueError): devig({'home':2, 'draw':1, 'away':3})
        with self.assertRaises(ValueError):
            kelly_snapshot([], self.target, provider='P', bookmaker='TARGET', reference_bookmakers=['P1','P2'], max_age_seconds=float('nan'), now=self.now)


class MixtureTests(unittest.TestCase):
    def rows(self):
        return [{'match_id':'x', 'dc':[[.7,.1],[.1,.1]], 'ml':[[.1,.1],[.7,.1]], 'home_goals':0, 'away_goals':0}]

    def test_zero_is_real_candidate_when_ml_hurts(self):
        r = select(self.rows()); self.assertEqual(r['selected_alpha'], 0.)
        self.assertFalse(r['active_model_changed']); self.assertEqual(len(r['trials']), 5)

    def test_ties_prefer_zero_extra_complexity(self):
        rows = self.rows(); rows[0]['ml'] = copy.deepcopy(rows[0]['dc'])
        self.assertEqual(select(rows)['selected_alpha'], 0.)

    def test_invalid_matrices_and_duplicate_population_rejected(self):
        with self.assertRaises(ValueError): select(self.rows()*2)
        rows = self.rows(); rows[0]['ml'][0][0] = 2
        with self.assertRaises(ValueError): select(rows)

    def test_strong_ml_can_win_without_forced_zero(self):
        rows = self.rows(); rows[0]['home_goals'] = 1
        self.assertEqual(select(rows)['selected_alpha'], 1.)


if __name__ == '__main__':
    unittest.main()
