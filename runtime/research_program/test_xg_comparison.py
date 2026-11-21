import copy
from datetime import date, timedelta
import json
from pathlib import Path
import tempfile
import unittest

from research_program import xg_comparison as xc
from research_program.feature_ablation import build_features as old_features
from research_program.xg_comparison_audit import audit_output


def fixture(i, day=None, home='A', away='B', xg=True):
    day = day or (date(2020, 1, 1) + timedelta(days=i-1)).isoformat()
    row = {'match_id': str(i), 'date': day, 'cohort': 'eng.1', 'competition': 'eng.1',
           'stage': 'regular', 'season': '2020', 'home': home, 'away': away,
           'home_team_id': home, 'away_team_id': away,
           'kickoff_at': day + 'T15:00:00Z', 'feature_available_by': day + 'T14:00:00Z',
           'label_first_known_at': day + 'T18:00:00Z', 'score_basis': 'regulation_90',
           'home_goals_90m': i % 4, 'away_goals_90m': i % 3, 'features': {}}
    if xg:
        row['features']['xg'] = {'provider': 'understat', 'provider_match_id': str(i),
                                'source_payload_sha256': 'a' * 64, 'date': day,
                                'first_known_at': day + 'T19:00:00Z',
                                'home_xg': 1. + i/100, 'home_xga': .7,
                                'away_xg': .7, 'away_xga': 1. + i/100}
    return row


def mini_protocol():
    return dict(xc.PROTOCOL, min_training_fixtures=5)


class XgComparisonTests(unittest.TestCase):
    def test_original_recipe_matches_legacy_same_records(self):
        records = [fixture(i) for i in range(1, 20)]
        old, _ = old_features(records)
        new, _ = xc.build_features(records, 'retrospective')
        self.assertEqual([r['match_id'] for r in old], [r['match_id'] for r in new])
        for a, b in zip(old, new):
            self.assertEqual(a['base_x'], b['features']['goals_only'])
            self.assertEqual(a['plus_xg_x'], b['features']['original_xg'])

    def test_target_same_day_and_future_values_cannot_change_target_features(self):
        records = [fixture(i) for i in range(1, 8)]
        records.insert(5, fixture(100, records[5]['date'], away='C'))
        before, _ = xc.build_features(records)
        changed = copy.deepcopy(records)
        for r in changed:
            if r['date'] >= '2020-01-06':
                r['home_goals_90m'] = 99
                r['features']['xg'].update(home_xg=88., away_xga=88.)
        after, _ = xc.build_features(changed)
        a = next(r for r in before if r['match_id'] == '6')
        b = next(r for r in after if r['match_id'] == '6')
        self.assertEqual(a['features'], b['features'])
        self.assertEqual(a['dependencies'], b['dependencies'])
        self.assertTrue(all(e['date'] < a['date'] for side in a['dependencies'].values() for es in side.values() for e in es))

    def test_missing_or_late_first_known_does_not_substitute_fetch_time(self):
        records = [fixture(i) for i in range(1, 7)]
        records[0]['features']['xg']['first_known_at'] = None
        records[0]['features']['xg']['source_fetched_at_beijing'] = '2019-01-01T00:00:00+08:00'
        records[1]['features']['xg']['first_known_at'] = '2020-01-06T15:00:00Z'
        rows, _ = xc.build_features(records)
        self.assertEqual([r['match_id'] for r in rows], ['6'])
        self.assertEqual([e['match_id'] for e in rows[0]['dependencies']['home']['xg']], ['3', '4', '5'])

    def test_timezone_offsets_and_cutoff_are_compared_as_instants(self):
        records = [fixture(i) for i in range(1, 6)]
        records[0]['features']['xg']['first_known_at'] = '2020-01-04T23:00:00+08:00'
        self.assertEqual([r['match_id'] for r in xc.build_features(records)[0]], ['5'])
        records[0]['features']['xg']['first_known_at'] = '2020-01-04T22:00:00+08:00'
        self.assertEqual([r['match_id'] for r in xc.build_features(records)[0]], ['4', '5'])

    def test_naive_missing_or_nonprematch_cutoff_excluded_in_strict_mode(self):
        records = [fixture(i) for i in range(1, 7)]
        records[3]['feature_available_by'] = '2020-01-04T14:00:00'
        records[4]['feature_available_by'] = records[4]['kickoff_at']
        del records[5]['kickoff_at']
        self.assertEqual(xc.build_features(records)[0], [])
        self.assertEqual(len(xc.build_features(records, 'retrospective')[0]), 3)
        assumed = xc.build_features([dict(r, feature_available_by=None) for r in records], 'retrospective')[0][0]
        self.assertIsNone(assumed['feature_available_by'])
        self.assertTrue(assumed['cutoff_is_date_only_assumption'])

    def test_stale_xg_stays_missing_not_zero_and_denominator_stays_common(self):
        records = [fixture(i) for i in range(1, 4)] + [fixture(4, '2020-05-01')]
        r = xc.build_features(records)[0][0]
        self.assertEqual(set(r['features']), set(xc.VARIANTS))
        self.assertGreater(r['features']['original_xg'][0][5], 0)
        self.assertIsNone(r['features']['fresh_missingness'][0][5])
        self.assertEqual(r['features']['fresh_missingness'][0][7], 1)
        self.assertEqual(r['features']['fresh_missingness'][0][11], 1.)
        self.assertEqual(r['fresh_dependencies']['home'], [])

    def test_observed_zero_not_missing(self):
        records = [fixture(i) for i in range(1, 5)]
        for r in records:
            r['features']['xg'].update(home_xg=0., away_xga=0.)
        features = xc.build_features(records)[0][0]['features']['fresh_missingness'][0]
        self.assertEqual(features[5], 0.)
        self.assertEqual(features[7], 0)

    def test_date_decay_is_not_match_order_decay(self):
        events = [{'date': '2020-01-01', 'for': 4.}, {'date': '2020-01-31', 'for': 1.}]
        means, n = xc.weighted(events, '2020-02-01', ['for'], xc.PROTOCOL)
        self.assertAlmostEqual(means[0], 2.)
        self.assertAlmostEqual(n, 1.8)
        old = [dict(events[0], date='2019-01-01')]
        self.assertEqual(xc.fresh(old, '2020-02-01', xc.PROTOCOL), [])

    def test_residual_uses_opponent_history_before_its_own_fixture(self):
        records = [fixture(i) for i in range(1, 8)]
        r = next(r for r in xc.build_features(records)[0] if r['match_id'] == '6')
        event = r['residual_dependencies']['home'][0]
        self.assertEqual(event['match_id'], '4')
        self.assertEqual([e['match_id'] for e in event['opponent_dependencies']], ['1', '2', '3'])
        self.assertAlmostEqual(event['for'], 1.04 - event['opponent_expectation'][1])
        self.assertTrue(all(e['date'] < event['date'] for e in event['opponent_dependencies']))
        changed = copy.deepcopy(records)
        changed[4]['features']['xg'].update(home_xg=50., away_xga=50.)
        later = next(r for r in xc.build_features(changed)[0] if r['match_id'] == '6')
        self.assertEqual(event, later['residual_dependencies']['home'][0])

    def test_phase_identity_and_provider_are_not_pooled(self):
        records = [fixture(i) for i in range(1, 4)]
        r = fixture(4); r['stage'] = 'playoffs'; records.append(r)
        records.append(fixture(5, home='C'))
        self.assertEqual(xc.build_features(records)[0], [])
        records = [fixture(i) for i in range(1, 5)]
        records[0]['features']['xg']['provider'] = 'other'
        self.assertEqual(xc.build_features(records)[0], [])

    def test_bad_source_hash_negative_nonfinite_fractional_labels_rejected(self):
        for mutation in ('hash', 'negative', 'nan', 'fractional_label'):
            records = [fixture(i) for i in range(1, 5)]
            if mutation == 'hash': records[0]['features']['xg']['source_payload_sha256'] = 'bad'
            if mutation == 'negative': records[0]['features']['xg'].update(home_xg=-1., away_xga=-1.)
            if mutation == 'nan': records[0]['features']['xg'].update(home_xg=float('nan'), away_xga=float('nan'))
            if mutation == 'fractional_label': records[0]['home_goals_90m'] = 1.5
            self.assertEqual(xc.build_features(records)[0], [], mutation)

    def test_duplicate_match_and_alias_identity_rejected(self):
        first = fixture(1)
        with self.assertRaises(ValueError): xc.build_features([first, copy.deepcopy(first)])
        with self.assertRaises(ValueError): xc.build_features([first, dict(first, match_id='alias')])

    def test_invalid_protocol_and_mode_rejected(self):
        for change in ({'half_life_days': 0}, {'max_xg_age_days': 0}, {'window_matches': 2}):
            with self.assertRaises(ValueError): xc.build_features([], protocol=dict(xc.PROTOCOL, **change))
        with self.assertRaises(ValueError): xc.build_features([], mode='pretend')

    def test_train_only_imputation_and_all_missing_columns_neutralized(self):
        rows, _ = xc.build_features([fixture(i) for i in range(1, 15)])
        for r in rows:
            for x in r['features']['fresh_missingness']: x[5] = None
        model = xc.fit_model(rows, 'fresh_missingness', xc.PROTOCOL)
        self.assertIn(5, model['unlearned_columns'])
        x = copy.deepcopy(rows[0]['features']['fresh_missingness'])
        first = xc.predict(model, x)
        for side in x: side[5] = 999.
        self.assertEqual(first, xc.predict(model, x))
        self.assertEqual(model['coef'][5], 0.)

    def test_monthly_fit_uses_same_matches_and_rejects_late_labels(self):
        records = [fixture(i) for i in range(1, 15)] + [fixture(20, '2020-02-01'), fixture(21, '2020-02-20')]
        records[7]['label_first_known_at'] = '2020-02-02T00:00:00Z'
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / 'run'
            summary = xc.run(records, out, 'test-input', protocol=mini_protocol())
            self.assertEqual(summary['coverage']['paired_predictions'], 2)
            fits = [json.loads(line) for line in (out/'fits.jsonl').read_text().splitlines()]
            self.assertEqual(len(fits), 1)
            self.assertNotIn('8', fits[0]['training_match_ids'])
            self.assertEqual(fits[0]['training_fixtures'], 10)
            self.assertEqual(set(fits[0]['models']), set(xc.VARIANTS))
            preds = [json.loads(line) for line in (out/'predictions.jsonl').read_text().splitlines()]
            self.assertTrue(all(set(r['models']) == set(xc.VARIANTS) for r in preds))
            self.assertTrue(all(s['n'] == 2 for s in summary['comparisons'].values()))
            with self.assertRaises(FileExistsError): xc.run(records, out, 'test-input')

    def test_fit_boundary_not_after_target_feature_cutoff(self):
        records = [fixture(i) for i in range(1, 15)] + [fixture(20, '2020-02-01')]
        records[-1]['feature_available_by'] = '2020-01-31T23:00:00Z'
        self.assertEqual(xc.build_features(records)[0][-1]['month'], '2020-01')

    def test_strict_historical_data_blocked_without_invented_availability(self):
        records = [fixture(i) for i in range(1, 20)]
        for r in records:
            r['label_first_known_at'] = None
            r['kickoff_at'] = None
            r['feature_available_by'] = None
            r['features']['xg']['first_known_at'] = None
        with tempfile.TemporaryDirectory() as td:
            summary = xc.run(records, Path(td)/'strict', 'test-input')
            self.assertEqual(summary['status'], 'BLOCKED_NO_PAIRED_PREDICTIONS')
            self.assertEqual(summary['coverage']['fitted_models'], 0)
            self.assertEqual(summary['coverage']['selected'], 19)
            self.assertFalse(summary['availability_audit']['collection_time_used_as_first_known'])
            self.assertEqual(sum(summary['coverage']['exclusion_reasons'].values()), 19)

    def test_audit_checks_output_hashes_and_full_dependency_chain(self):
        records = [fixture(i) for i in range(1, 15)] + [fixture(20, '2020-02-01')]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'run'
            xc.run(records, root, 'test-input', protocol=mini_protocol())
            audit = audit_output(root)
            self.assertEqual(audit['violations'], {})
            self.assertGreater(audit['residual_nested_dependencies_checked'], 0)
            with (root/'predictions.jsonl').open('a') as stream:
                stream.write('{}\n')
            with self.assertRaises(ValueError): audit_output(root)

    def test_summary_gain_loss_and_probabilities(self):
        records = [fixture(i) for i in range(1, 15)] + [fixture(20, '2020-02-01')]
        with tempfile.TemporaryDirectory() as td:
            summary = xc.run(records, Path(td)/'run', 'test-input', protocol=mini_protocol())
            for comparison in summary['comparisons'].values():
                self.assertEqual(comparison['top1_net'], comparison['top1_gains'] - comparison['top1_losses'])
                self.assertLess(comparison['plus_xg']['max_probability_mass_error'], 1e-10)


if __name__ == '__main__':
    unittest.main()
