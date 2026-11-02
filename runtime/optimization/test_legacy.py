import copy
import importlib.util
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from legacy import fit_legacy, predict_legacy, source_hashes, _oof_items, _v6


def history(n=180):
    teams = ['A', 'B', 'C', 'D']
    return [dict(match_id=str(i), cohort='eng.1', competition='eng.1',
                 date=(date(2020, 1, 1)+timedelta(days=i)).isoformat(),
                 home=teams[i % 4], away=teams[(i+1+i//4 % 3) % 4],
                 home_goals=(i*7+2) % 4, away_goals=(i*3+1) % 3,
                 neutral=i % 7 == 0) for i in range(n)]


def target():
    return dict(match_id='target', cohort='eng.1', date='2021-01-01',
                home='A', away='B', neutral=False)


class LegacyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = history()

    def test_m0_exact_original_equation_and_source_unchanged(self):
        before = source_hashes()
        path = Path(__file__).resolve().parents[1] / 'v2/model.py'
        spec = importlib.util.spec_from_file_location('_test_original_v2', path)
        original = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(original)
        expected_fit = original.fit_baseline(self.rows)
        expected = original.matrix(*original.rates(expected_fit, target())[:2])
        fit = fit_legacy(list(reversed(self.rows)), 'M0')
        actual = predict_legacy(fit, target())
        self.assertFalse(actual['abstain'])
        np.testing.assert_array_equal(expected, actual['matrix'])
        self.assertEqual(before, source_hashes())

    def test_unknown_venue_and_labels_do_not_silently_pass(self):
        rows = copy.deepcopy(self.rows)
        rows[0]['neutral'] = None
        fit = fit_legacy(rows, 'M0')
        self.assertEqual(fit['train_rows'], 179)
        self.assertEqual(fit['excluded_unknown_venue'], 1)
        for changes in ({'neutral': None}, {'home_goals': 3},
                        {'date': self.rows[-1]['date']}, {'cohort': 'esp.1'}):
            result = predict_legacy(fit, dict(target(), **changes))
            self.assertTrue(result['abstain'])
            self.assertIsNone(result['matrix'])

    def test_too_small_explicit_abstention(self):
        fit = fit_legacy(self.rows[:20], 'M1')
        self.assertTrue(fit['abstain'])
        self.assertIn('60 rows', predict_legacy(fit, target())['reason'])

    def test_m1_fixed_alpha_three_oof_folds_and_normalized_matrix(self):
        fit = fit_legacy(self.rows, 'M1')
        self.assertFalse(fit['abstain'], fit.get('reason'))
        self.assertEqual(fit['alpha'], .25)
        self.assertEqual(len(fit['folds']), 3)
        for fold in fit['folds']:
            self.assertLess(fold['fit_end'], fold['first_target'])
        prediction = predict_legacy(fit, target())
        self.assertFalse(prediction['abstain'], prediction.get('reason'))
        self.assertEqual(prediction['matrix'].shape, (51, 51))
        self.assertAlmostEqual(float(prediction['matrix'].sum()), 1, 12)
        self.assertTrue((prediction['matrix'] >= 0).all())

    def test_features_exclude_own_and_later_labels(self):
        row = self.rows[120]
        changed = copy.deepcopy(self.rows)
        for r in changed:
            if r['date'] >= row['date']:
                r['home_goals'] = 30
                r['away_goals'] = 30
        a = _v6().feature_matrix(self.rows, row, (1.4, 1.1))
        b = _v6().feature_matrix(changed, row, (1.4, 1.1))
        np.testing.assert_array_equal(a, b)
        first, _ = _oof_items(self.rows)
        second, _ = _oof_items(changed)
        a = next(it for it in first if it['row']['match_id'] == row['match_id'])
        b = next(it for it in second if it['row']['match_id'] == row['match_id'])
        np.testing.assert_array_equal(a['features'], b['features'])
        self.assertEqual(a['rates'], b['rates'])

    def test_no_generic_module_or_sys_path_changes(self):
        before_path = list(sys.path)
        before_modules = {key: sys.modules.get(key) for key in ('model', 'main_model', 'weighted_model', 'ml_model')}
        fit_legacy(self.rows, 'M1')
        self.assertEqual(before_path, sys.path)
        for key, module in before_modules.items():
            self.assertIs(module, sys.modules.get(key))


if __name__ == '__main__':
    unittest.main()
