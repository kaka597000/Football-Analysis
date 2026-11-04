import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from research_program.feature_ablation import (
    PROTOCOL, build_features, fit_model, market_gate, model_predict, paired_summary,
    run, score_prediction,
)


def fixture(i, date, xg=True, stage='regular', home='A', away='B'):
    r = {'match_id': str(i), 'cohort': 'eng.1', 'competition': 'eng.1', 'stage': stage,
         'date': date, 'season': '2020', 'home_team_id': home, 'away_team_id': away,
         'home': home, 'away': away, 'score_basis': 'regulation_90',
         'home_goals_90m': i % 4, 'away_goals_90m': i % 3,
         'label_first_known_at': None, 'features': {}}
    if xg:
        r['features']['xg'] = {'provider': 'understat', 'date': date,
                              'home_xg': 1.2 + i/100, 'away_xg': .8,
                              'home_xga': .8, 'away_xga': 1.2 + i/100,
                              'source_payload_sha256': 'hash' + str(i)}
    return r


class FeatureAblationTests(unittest.TestCase):
    def test_current_and_same_day_process_do_not_enter_features(self):
        rows = [fixture(i, f'2020-01-0{i}') for i in range(1, 4)]
        rows += [fixture(4, '2020-01-04'), fixture(5, '2020-01-04')]
        before, _ = build_features(rows)
        changed = copy.deepcopy(rows)
        for r in changed[3:]:
            r['features']['xg'].update(home_xg=99., away_xg=98., home_xga=98., away_xga=99.)
            r['home_goals_90m'] = 90
        after, _ = build_features(changed)
        self.assertEqual([r['base_x'] for r in before], [r['base_x'] for r in after])
        self.assertEqual([r['plus_xg_x'] for r in before], [r['plus_xg_x'] for r in after])
        for r in before:
            deps = [d for side in r['dependencies'].values() for ds in side.values() for d in ds]
            self.assertTrue(all(d['date'] < r['date'] for d in deps))
            self.assertNotIn(r['match_id'], [d['match_id'] for d in deps])

    def test_missing_xg_is_not_zero_and_observed_zero_is_preserved(self):
        rows = [fixture(i, f'2020-01-0{i}', xg=i != 2) for i in range(1, 6)]
        rows[0]['features']['xg'].update(home_xg=0., away_xga=0.)
        prepared, excluded = build_features(rows)
        self.assertEqual([r['match_id'] for r in prepared], ['5'])
        hist = prepared[0]['dependencies']['home']['xg']
        self.assertEqual([d['match_id'] for d in hist], ['1', '3', '4'])
        self.assertEqual(hist[0]['for'], 0.)
        self.assertTrue(any(r['match_id'] == '4' for r in excluded))

    def test_phase_and_identity_do_not_share_history(self):
        rows = [fixture(i, f'2020-01-0{i}') for i in range(1, 4)]
        rows += [fixture(4, '2020-01-04', stage='playoffs'), fixture(5, '2020-01-05', home='C')]
        self.assertEqual(build_features(rows)[0], [])

    def test_freshness_filters_dependencies_before_paired_eligibility(self):
        rows = [fixture(1, '2020-01-01'), fixture(2, '2020-04-01'),
                fixture(3, '2020-04-02'), fixture(4, '2020-04-03'), fixture(5, '2020-04-04')]
        prepared, excluded = build_features(rows, max_xg_age_days=90)
        self.assertEqual([r['match_id'] for r in prepared], ['5'])
        self.assertEqual([d['match_id'] for d in prepared[0]['dependencies']['home']['xg']], ['2','3','4'])
        self.assertEqual(len(prepared[0]['dependencies']['home']['goals']), 4)
        self.assertEqual(len(prepared[0]['base_x']), len(prepared[0]['plus_xg_x']))
        with self.assertRaises(ValueError):
            build_features(rows, max_xg_age_days=0)

    def test_duplicate_fixture_rejected(self):
        with self.assertRaises(ValueError):
            build_features([fixture(1, '2020-01-01')]*2)

    def test_source_mismatch_cannot_supply_xg(self):
        rows = [fixture(i, f'2020-01-0{i}') for i in range(1, 6)]
        rows[0]['features']['xg']['provider'] = 'other-provider'
        rows[1]['features']['xg']['date'] = '2021-01-02'
        self.assertEqual(build_features(rows)[0], [])

    def test_fitted_coefficients_and_probability_support(self):
        rows = [fixture(i, f'2020-01-{i:02}') for i in range(1, 20)]
        prepared, _ = build_features(rows)
        model = fit_model(prepared, 'plus_xg_x')
        self.assertEqual(len(model['coef']), 9)
        pred = score_prediction(model_predict(model, prepared[0]['plus_xg_x']), [7, 0])
        self.assertLess(pred['probability_mass_error'], 1e-10)
        self.assertTrue(np.isfinite(pred['score_log_loss']))
        self.assertEqual(score_prediction([2., 1.], [1, 0])['statistical_top1'], [1, 0])

    def test_market_quote_time_fail_closed(self):
        r = fixture(1, '2020-01-01')
        r['kickoff_at'] = '2020-01-01T20:00:00+00:00'
        r['market_features'] = {'captured_at': '2020-01-01T19:00:00+00:00',
                                'closing': True, 'quote_time': None}
        result = market_gate([r])
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['certified_prematch_quote_fixtures'], 0)
        self.assertEqual(result['training_fixtures'], 0)

    def test_month_cutoff_and_paired_denominator(self):
        rows = [fixture(i, f'2020-01-{i:02}') for i in range(1, 15)]
        rows += [fixture(20, '2020-02-01'), fixture(21, '2020-02-20')]
        with tempfile.TemporaryDirectory() as td, patch.dict(PROTOCOL, {'min_training_fixtures': 5}):
            summary = run(rows, td, 'testhash', {})
            fits = [json.loads(v) for v in (Path(td)/'fits.jsonl').read_text().splitlines()]
            self.assertEqual(len(fits), 1)
            self.assertLess(fits[0]['training_max_fixture_date'], '2020-02-01')
            self.assertEqual(fits[0]['training_fixtures'], 11)
            self.assertEqual(summary['overall']['n'], 2)
            self.assertEqual(summary['coverage']['predicted_paired_fixtures'], 2)
            preds = [json.loads(v) for v in (Path(td)/'predictions.jsonl').read_text().splitlines()]
            self.assertTrue(all('baseline' in v and 'plus_xg' in v for v in preds))
            with self.assertRaises(FileExistsError):
                run(rows, td, 'testhash', {})

    def test_gain_loss_accounting(self):
        preds = []
        for i, (base, extra) in enumerate([(True, True), (False, True), (True, False), (False, False)]):
            r = {'cohort': 'eng.1', 'stage': 'regular', 'month': '2020-01', 'actual_score': [1, 0]}
            r['baseline'] = score_prediction([1.2, .8], [1, 0])
            r['plus_xg'] = copy.deepcopy(r['baseline'])
            r['baseline']['top1_hit'], r['plus_xg']['top1_hit'] = base, extra
            preds.append(r)
        s = paired_summary(preds)
        self.assertEqual((s['top1_gains'], s['top1_losses'], s['top1_net'], s['top1_both_hit']), (1, 1, 0, 1))
        self.assertEqual(s['n'], 4)


if __name__ == '__main__':
    unittest.main()
