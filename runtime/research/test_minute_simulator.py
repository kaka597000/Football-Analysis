"""Synthetic simulator checks, never historical football training or KPI evidence."""
import copy
import hashlib
import json
import math
from pathlib import Path
import tempfile
import unittest

from minute_simulator import simulate


class MinuteSimulatorTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        path = Path(self.folder.name)/'synthetic-rates.json'
        path.write_text(json.dumps({'expected_goals': [1.7, 1.1], 'synthetic': True}))
        self.reference = {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                          'rates_pointer': '/expected_goals', 'observed_at': '2026-01-02T00:00:00+00:00'}
        self.artifact = {'schema': 'WCPP-MINUTE-RATES-1', 'cohort': 'SYNTHETIC_TEST_ONLY',
                         'score_basis': 'regulation_90_including_stoppage',
                         'trained_through': '2026-01-01T00:00:00+00:00',
                         'created_at': '2026-01-02T00:00:00+00:00', 'source': self.reference}
        self.state = {'minute': 0, 'score': [0, 0], 'clock_basis': 'regulation_elapsed_minutes',
                      'observed_at': '2026-01-03T00:00:00+00:00', 'events': [], 'events_complete': True}
        self.as_of = '2026-01-03T00:00:00+00:00'

    def run_sim(self, **kwargs):
        return simulate(self.artifact, self.state, as_of=self.as_of, **kwargs)

    def test_seeded_minute_grid_matches_analytic_poisson(self):
        before = copy.deepcopy((self.artifact, self.state))
        result = self.run_sim(draws=50000, seed=71)
        for observed, expected in zip(result['mean_final_goals'], [1.7, 1.1]):
            self.assertLess(abs(observed-expected), .025)
        probability_1_0 = next(x['frequency'] for x in result['score_frequencies'] if x['score'] == [1, 0])
        self.assertLess(abs(probability_1_0 - math.exp(-2.8)*1.7), .005)
        self.assertAlmostEqual(sum(result['wdl_frequencies'].values()), 1)
        self.assertAlmostEqual(sum(x['frequency'] for x in result['score_frequencies']), 1)
        self.assertEqual(result['state_effect_status'], 'NOT_TRAINED_IDENTITY_ONLY')
        self.assertFalse(result['active_forecast_modified'])
        self.assertEqual((self.artifact, self.state), before)
        self.assertEqual(result, self.run_sim(draws=50000, seed=71))

    def test_observed_score_is_preserved_and_only_remaining_time_is_simulated(self):
        self.state.update(minute=48, score=[1, 0], red_cards=[0, 0], events=[{
            'minute': 25, 'type': 'goal', 'beneficiary_side': 'home', 'period': 'first_half',
            'observed_at': self.as_of}])
        result = self.run_sim(draws=20000, seed=4, regulation_minutes=96)
        self.assertEqual(result['analytic_identity_mean'], [1.85, .55])
        self.assertTrue(all(x['score'][0] >= 1 for x in result['score_frequencies']))
        self.assertEqual(len(result['minute_mean_trajectory']), 48)

    def test_invalid_future_events_extra_time_and_negative_minutes_rejected(self):
        self.state.update(minute=45, score=[1, 0], red_cards=[0, 0])
        event = {'minute': 25, 'type': 'goal', 'beneficiary_side': 'home', 'period': 'first_half',
                 'observed_at': self.as_of}
        for patch in ({'minute': 46}, {'minute': -.5}, {'period': 'extra_time'},
                      {'period': 'shootout'}, {'observed_at': '2026-01-04T00:00:00+00:00'}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                self.state['events'] = [{**event, **patch}]
                self.run_sim(draws=100)

    def test_untrained_red_cards_never_get_arbitrary_multiplier(self):
        self.state.update(minute=10, red_cards=[0, 1], events=[
            {'minute': 1, 'type': 'red_card', 'carded_side': 'away',
             'period': 'first_half', 'observed_at': self.as_of}])
        with self.assertRaisesRegex(ValueError, 'NOT_TRAINED'):
            self.run_sim(draws=100)

    def test_partial_live_events_cannot_hide_unknown_or_known_red_cards(self):
        self.state.update(minute=50, events_complete=False, events=[])
        with self.assertRaisesRegex(ValueError, 'UNKNOWN'):
            self.run_sim(draws=100)
        self.state['red_cards'] = [1, 0]
        with self.assertRaisesRegex(ValueError, 'NOT_TRAINED'):
            self.run_sim(draws=100)
        self.state['red_cards'] = [0, 0]
        self.assertEqual(self.run_sim(draws=100)['red_card_effect_status'], 'NOT_TRAINED')

    def test_event_card_counts_must_agree_with_explicit_card_state(self):
        event = {'minute': 10, 'type': 'red_card', 'carded_side': 'home',
                 'period': 'first_half', 'observed_at': self.as_of}
        self.state.update(minute=50, events_complete=False, events=[event], red_cards=[0, 0])
        with self.assertRaisesRegex(ValueError, 'exceed'):
            self.run_sim(draws=100)
        self.state.update(events_complete=True, events=[], red_cards=[1, 0])
        with self.assertRaisesRegex(ValueError, 'complete events.*red-card'):
            self.run_sim(draws=100)

    def test_source_hash_future_artifact_and_missing_rate_provenance_rejected(self):
        original = copy.deepcopy(self.artifact)
        for patch in ({'source': {**self.reference, 'sha256': 'bad'}},
                      {'created_at': '2026-01-04T00:00:00+00:00'},
                      {'trained_through': self.as_of},
                      {'score_basis': 'including_extra_time'},
                      {'source': {**self.reference, 'rates_pointer': '/synthetic'}}):
            self.artifact = {**original, **patch}
            with self.assertRaises(ValueError):
                self.run_sim(draws=100)
        self.artifact = original
        with self.assertRaisesRegex(ValueError, 'future'):
            simulate(self.artifact, self.state, as_of='2099-01-01T00:00:00+00:00', draws=100)

    def test_state_parameters_require_fit_coverage_and_immutable_evidence(self):
        hazards = {'schema': 'WCPP-MINUTE-STATE-HAZARDS-1', 'status': 'FITTED_RESEARCH',
                   'cohort': 'SYNTHETIC_TEST_ONLY',
                   'score_basis': 'regulation_90_including_stoppage',
                   'trained_through': '2026-01-01T00:00:00+00:00',
                   'created_at': '2026-01-02T00:00:00+00:00',
                   'coverage': {'matches': 199, 'complete_fraction': .99},
                   'training_manifest': self.reference, 'temporal_validation_report': self.reference,
                   'score_state_multipliers': {'home_ahead': [1., 1.], 'draw': [1., 1.], 'home_behind': [1., 1.]}}
        with self.assertRaisesRegex(ValueError, 'coverage'):
            self.run_sim(draws=100, state_artifact=hazards)
        hazards['coverage']['matches'] = 200
        result = self.run_sim(draws=100, state_artifact=hazards)
        self.assertEqual(result['state_effect_status'], 'FITTED_RESEARCH_NOT_PROMOTED')
        hazards['temporal_validation_report'] = {**self.reference, 'sha256': 'bad'}
        with self.assertRaises(ValueError):
            self.run_sim(draws=100, state_artifact=hazards)


if __name__ == '__main__':
    unittest.main()
