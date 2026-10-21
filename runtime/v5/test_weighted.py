import copy
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import weighted_model as model


class WeightTests(unittest.TestCase):
    def test_learning_prefers_better_out_of_sample_predictions(self):
        p = np.tile([.20, .04, .03, .04, .02], (60, 1))
        weights = model.learn_weights(p)
        self.assertGreater(weights[0], .9)
        self.assertAlmostEqual(sum(weights), 1., 12)
        self.assertTrue(all(w >= 0 for w in weights))

    def test_invalid_weights_and_distributions_rejected(self):
        matrices = np.zeros((5, 51, 51)); matrices[:, 1, 1] = 1.
        for w in [[1., 1., 0., 0., 0.], [-1., 2., 0., 0., 0.], [float('nan')]*5]:
            with self.assertRaises(ValueError): model.pooled_matrix(matrices, w)
        matrices[0, 1, 1] = .5
        with self.assertRaises(ValueError): model.pooled_matrix(matrices, [.2]*5)
        with self.assertRaises(ValueError): model.learn_weights([[.1]*5]*3)

    def test_mixture_preserves_distinct_score_cells_and_quarter_settlements(self):
        matrices = np.zeros((5, 51, 51))
        matrices[0, 1, 1] = 1.; matrices[1:, 2, 1] = 1.
        mixed = model.pooled_matrix(matrices, [.6, .1, .1, .1, .1])
        self.assertAlmostEqual(mixed[1, 1], .6)
        self.assertAlmostEqual(mixed[2, 1], .4)
        under = model.legacy.total_settlement(mixed, 2.75, 'under')
        self.assertAlmostEqual(under['win'], .6)
        self.assertAlmostEqual(under['half_loss'], .4)

    def test_dates_are_kept_together_and_ordered(self):
        rows = [{'date': (datetime(2020, 1, 1)+timedelta(days=i//2)).date().isoformat()}
                for i in range(200)]
        first, calibration, test = model.chronological_split(rows)
        self.assertLess(first[-1]['date'], calibration[0]['date'])
        self.assertLess(calibration[-1]['date'], test[0]['date'])

    def test_rolling_does_not_use_target_or_future_results(self):
        from unittest.mock import patch
        rows = [{'match_id': str(i), 'cohort': 'eng.1', 'date': f'2020-01-{i+1:02}',
                 'home': 'A', 'away': 'B', 'home_goals': i, 'away_goals': 0}
                for i in range(20)]
        captured = []
        def core(prior): captured.extend(prior); return {}
        def distribution(*args):
            m = np.zeros((51, 51)); m[1, 0] = 1.
            return m, {'unseen_teams': []}
        with patch.object(model.legacy, 'fit_core', core), patch.object(model.legacy, 'distribution', distribution):
            _, folds = model.rolling_predictions(rows, rows[10:])
        self.assertEqual([r['match_id'] for r in captured], [str(i) for i in range(10)])
        self.assertLess(folds[0]['fit_end'], folds[0]['first_target'])

    def test_failed_acceptance_preserves_base_primary_and_shows_candidate(self):
        from unittest.mock import patch
        fixture = {'match_id': 'x'}
        base_matrix = np.zeros((51, 51)); base_matrix[1, 0] = 1.
        candidate = np.zeros((51, 51)); candidate[0, 1] = 1.
        base = {'matrix': base_matrix.tolist(), 'primary': '1-0',
                'wdl': {'主胜': 1., '平局': 0., '客胜': 0.}, 'total': None, 'reasons': []}
        artifact = {'version': model.VERSION, 'code_hash': model.code_hash(), 'config': model.CONFIG,
                    'anchor': {'history': [], 'core': {}}, 'history': [], 'core': {},
                    'experts': list(model.EXPERTS), 'weights': [0., 1., 0., 0., 0.],
                    'acceptance': {'acceptance_gate_passed': False}, 'context_modules': {}}
        with patch.object(model.legacy, 'predict', return_value=base), patch.object(model.legacy, 'distribution', return_value=(candidate, {'unseen_teams': []})):
            output = model.predict(artifact, fixture)
        self.assertEqual(output['primary'], '1-0')
        self.assertEqual(output['weighted_shadow']['primary'], '0-1')
        self.assertFalse(output['weighted_execution_enabled'])
        self.assertFalse(output['formal_kpi_eligible'])

    def test_explicit_test_track_is_independent_even_when_acceptance_failed(self):
        from unittest.mock import patch
        base_matrix = np.zeros((51, 51)); base_matrix[1, 0] = 1.
        candidate = np.zeros((51, 51)); candidate[0, 1] = 1.
        base = {'matrix': base_matrix.tolist(), 'primary': '1-0',
                'wdl': {'主胜': 1., '平局': 0., '客胜': 0.}, 'total': None, 'reasons': []}
        artifact = {'version': model.VERSION, 'code_hash': model.code_hash(), 'config': model.CONFIG,
                    'anchor': {'history': [], 'core': {}}, 'history': [], 'core': {},
                    'experts': list(model.EXPERTS), 'weights': [0., 1., 0., 0., 0.],
                    'acceptance': {'acceptance_gate_passed': False}, 'context_modules': {}}
        with patch.object(model.legacy, 'predict', return_value=base), patch.object(model.legacy, 'distribution', return_value=(candidate, {'unseen_teams': []})):
            output = model.predict(artifact, {}, variant='test')
        self.assertEqual(output['primary'], '0-1')
        self.assertEqual(base['primary'], '1-0')
        self.assertEqual(output['model_label'], 'test模型')
        self.assertFalse(output['acceptance']['acceptance_gate_passed'])
        self.assertFalse(output['formal_kpi_eligible'])

    def test_paired_review_scores_each_model_independently(self):
        from paired_review import review
        forecasts = []
        fixture = {'match_id': 'historical-plumbing-test', 'cutoff_at': '2020-01-01T00:00:00+00:00',
                   'kickoff_at': '2020-01-01T01:00:00+00:00'}
        for h, a in [(1, 0), (0, 1)]:
            m = np.zeros((51, 51)); m[h, a] = 1.
            forecasts.append({'input': dict(fixture), 'matrix': m.tolist(), 'matrix_hash': model.legacy.digest(m.tolist()),
                              'generated_at': fixture['cutoff_at'], 'primary': f'{h}-{a}',
                              'wdl_reference': '主胜' if h > a else '客胜', 'total': None})
        pair = {'match_id': fixture['match_id'], 'current_model': forecasts[0], 'test_model': forecasts[1]}
        result = {'match_id': fixture['match_id'], 'home_goals': 1, 'away_goals': 0,
                  'score_basis': 'regulation_90', 'source_url': 'https://example.org/test-only',
                  'observed_at': '2020-01-01T03:00:00+00:00'}
        output = review([pair], [result])
        self.assertEqual(output['models']['当前模型']['primary_hits'], 1)
        self.assertEqual(output['models']['test模型']['primary_hits'], 0)
        self.assertEqual(output['formal_kpi_added'], 0)
        self.assertEqual(review([pair], [])['pending'], [fixture['match_id']])
        with self.assertRaises(ValueError): review([pair, pair], [result])
        forecasts[1]['input']['cutoff_at'] = '2020-01-01T00:30:00+00:00'
        with self.assertRaises(ValueError): review([pair], [result])

    def test_review_does_not_pool_different_test_versions(self):
        from paired_review import review
        pairs,results=[],[]
        for mid,ver in [('a','WCPP-WEIGHTED-0.1'),('b','WCPP-ML-0.1')]:
            fixture={'match_id':mid,'cutoff_at':'2020-01-01T00:00:00Z','kickoff_at':'2020-01-01T01:00:00Z'}
            matrix=np.zeros((51,51));matrix[1,0]=1.
            f={'input':fixture,'matrix':matrix.tolist(),'matrix_hash':model.legacy.digest(matrix.tolist()),
               'generated_at':fixture['cutoff_at'],'primary':'1-0','wdl_reference':'主胜','total':None}
            pairs.append({'match_id':mid,'current_model':dict(f,version='WCPP-MAIN-1.2'),
                          'test_model':dict(f,version=ver)})
            results.append({'match_id':mid,'home_goals':1,'away_goals':0,'score_basis':'regulation_90',
                            'source_url':'https://example.test/test-only','observed_at':'2020-01-01T03:00:00Z'})
        r=review(pairs,results)
        self.assertEqual(r['models']['当前模型']['primary_hits'],2)
        self.assertNotIn('primary_hits',r['models']['test模型'])
        self.assertEqual(len(r['models']['test模型']['by_version']),2)


if __name__ == '__main__': unittest.main()
