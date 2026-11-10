import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from model import fit_strength, aware
from test_model import history
from training_inputs import history_inputs, context_inputs, fit_context, validate_context


def samples():
    start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    rows = []
    for i in range(240):
        kickoff = start + timedelta(days=i)
        cutoff = kickoff - timedelta(hours=1)
        rows.append(dict(cohort='TEST', kickoff_at=kickoff.isoformat(), cutoff_at=cutoff.isoformat(),
                         baseline_observed_at=(start-timedelta(days=1)).isoformat(),
                         result_observed_at=(kickoff+timedelta(hours=3)).isoformat(),
                         baseline_home=1.5, baseline_away=1., home_goals=1, away_goals=1,
                         features={'strength': i % 2},
                         evidence=[dict(source_url='https://example.org/official', query_time=cutoff.isoformat(), source_tier='L1')]))
    return rows


class TrainingInputTests(unittest.TestCase):
    def test_history_real_latest_observation_survives_mixed_timezones(self):
        rows = history()
        for row in rows:
            row['observed_at'] = '2026-01-01T00:00:00+00:00'
        rows[0]['observed_at'] = '2026-01-01T08:00:00+08:00'
        rows[1]['observed_at'] = '2026-01-01T01:00:00+00:00'
        original = deepcopy(rows)
        model = fit_strength(history_inputs(list(reversed(rows))))
        self.assertEqual(model['observed_at'], '2026-01-01T01:00:00+00:00')
        self.assertEqual(rows, original)

    def test_context_mixed_zone_late_observation_cannot_pass_validation(self):
        rows = samples()
        # The earlier wall-clock label is actually later in UTC and exceeds
        # the first holdout cutoff. Lexical max picks the other observation.
        rows[0]['result_observed_at'] = '2020-06-17T01:00:00+08:00'
        rows[1]['result_observed_at'] = '2020-06-16T23:30:00+00:00'
        with self.assertRaisesRegex(ValueError, 'artifact not available'):
            validate_context(rows, ['strength'])

    def test_context_same_instant_has_same_split_day(self):
        rows = samples()
        rows[0]['kickoff_at'] = '2020-01-01T08:00:00+08:00'
        prepared = context_inputs(rows, ['strength'])
        self.assertEqual(prepared[0]['kickoff_at'], '2020-01-01T00:00:00+00:00')
        self.assertEqual(rows[0]['kickoff_at'], '2020-01-01T08:00:00+08:00')

    def test_context_rejects_holdout_cohort_and_unfinished_result(self):
        rows = samples(); rows[-1]['cohort'] = 'OTHER'
        with self.assertRaisesRegex(ValueError, 'cohorts'):
            validate_context(rows, ['strength'])
        rows = samples(); rows[-1]['result_observed_at'] = rows[-1]['kickoff_at']
        with self.assertRaisesRegex(ValueError, 'regulation completion'):
            validate_context(rows, ['strength'])

    def test_context_rejects_nonfinite_baselines_and_invalid_labels(self):
        for field, value in [('baseline_home', float('nan')), ('baseline_away', float('inf')),
                             ('home_goals', -1), ('away_goals', True)]:
            rows = samples(); rows[-1][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                validate_context(rows, ['strength'])

    def test_guarded_training_still_fits_valid_samples(self):
        fit = fit_context(samples(), ['strength'])
        self.assertEqual(fit['n'], 240)
        self.assertFalse(fit['validated'])


if __name__ == '__main__':
    unittest.main()
