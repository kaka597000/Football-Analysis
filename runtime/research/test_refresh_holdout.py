import copy
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
import refresh_holdout as subject


def row(mid, day, score=1):
    return {'match_id': mid, 'cohort': 'eng.1', 'date': day,
            'home': 'espn:1', 'away': 'espn:2', 'home_goals': score,
            'away_goals': 0, 'score_evidence': {'source_key': 'espn:'+mid}}


class HoldoutTests(unittest.TestCase):
    def test_same_day_games_stay_together_and_split_ignores_labels(self):
        rows = [row('1', '2020-01-01'), row('2', '2020-01-02'),
                row('3', '2020-01-02'), row('4', '2020-01-03')]
        fit, targets = subject.split_dates(rows)
        self.assertEqual([r['match_id'] for r in fit], ['1'])
        self.assertEqual([r['match_id'] for r in targets], ['2', '3', '4'])
        changed = copy.deepcopy(rows[::-1])
        for r in changed: r['home_goals'] = 9
        second = subject.split_dates(changed)
        self.assertEqual([[r['match_id'] for r in block] for block in second],
                         [[r['match_id'] for r in block] for block in (fit, targets)])

    def test_same_date_and_cross_date_identity_overlap_are_rejected(self):
        for target in (row('2', '2020-01-01'), row('1', '2020-01-02')):
            with self.assertRaises(ValueError): subject.assert_disjoint([row('1', '2020-01-01')], [target])
        with self.assertRaises(ValueError): subject.split_dates([row('1', '2020-01-01')])

    def test_legacy_provider_identity_preserved_without_inventing_score_source(self):
        old = row('1', '2020-01-01'); old.pop('score_evidence')
        old['api_evidence'] = {'source_key': '5dollar:987'}
        old['neutral_evidence'] = {'source_url': 'https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/summary?event=123'}
        original = copy.deepcopy(old)
        self.assertEqual(subject.source_keys(old), {'5dollar:987', 'espn:123'})
        subject.assert_disjoint([old], [row('2', '2020-01-02')])
        with self.assertRaisesRegex(ValueError, 'source identity'):
            subject.assert_disjoint([old], [row('123', '2020-01-02')])
        self.assertEqual(old, original)

    def test_wdl_is_aggregated_separately_from_top_score(self):
        matrix = np.array([[.30, .21], [.20, .29]])
        scored = subject.summarize_matrix(matrix, row('1', '2020-01-01', 0))
        self.assertEqual(scored['primary'], [0, 0])
        self.assertTrue(scored['primary_hit'])
        self.assertEqual(scored['wdl_pick'], 1)
        self.assertAlmostEqual(scored['wdl_brier'], .20**2 + .41**2 + .21**2)

    def test_abstentions_preserve_population_and_pair_denominators(self):
        good = subject.summarize_matrix(np.array([[1., 0.], [0., 0.]]), row('1', '2020-01-01', 0))
        refused = {'status': 'ABSTAIN', 'reason': 'unseen team'}
        records = [{'date': '2020-01-01', 'old': good, 'new': good},
                   {'date': '2020-01-02', 'old': refused, 'new': good}]
        result = subject.metrics(records, 'old')
        self.assertEqual((result['n'], result['scored'], result['primary_hits']), (2, 1, 1))
        self.assertEqual(result['primary_rate_all_targets'], .5)
        delta = subject.paired_delta(records, 'old', 'new')
        self.assertEqual((delta['all_targets'], delta['paired_n']), (2, 1))
        self.assertEqual(delta['primary_date_block_bootstrap95'], [0., 0.])

    def test_invalid_probability_matrix_rejected(self):
        for matrix in (np.array([[.8]]), np.array([[float('nan')]]), np.array([[1.1, -.1]])):
            with self.assertRaises(ValueError): subject.summarize_matrix(matrix, row('1', '2020-01-01', 0))


class RetainedModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        current = Path('/workspace/WCPP/models/weighted-20261012/eng.1/artifact.json')
        test = Path('/workspace/WCPP/models/ml-20261012/eng.1/artifact.json')
        if not current.exists() or not test.exists():
            raise unittest.SkipTest('retained model integration assets unavailable')
        cls.current = subject.refresh.read(current); cls.test = subject.refresh.read(test)

    def test_target_labels_never_change_actual_model_matrices(self):
        old = self.current['history'][-1]
        fixture = dict(old, date='2026-11-08', home_goals=0, away_goals=0)
        first = subject.numerical_pair(self.current, self.test, fixture)
        fixture.update(home_goals=5, away_goals=4)
        second = subject.numerical_pair(self.current, self.test, fixture)
        for model in ('current', 'test'):
            self.assertEqual(first[model]['matrix_sha256'], second[model]['matrix_sha256'])
            self.assertEqual(first[model]['primary'], second[model]['primary'])
            self.assertNotEqual(first[model]['score_log_loss'], second[model]['score_log_loss'])

    def test_unknown_team_rejected_like_current_entry(self):
        fixture = dict(self.current['history'][-1], date='2026-11-08', home='espn:999999999')
        with self.assertRaisesRegex(ValueError, 'unseen teams'):
            subject.numerical_pair(self.current, self.test, fixture)

    def test_test_feature_failure_does_not_remove_current_prediction(self):
        fixture = dict(self.current['history'][-1], date='2026-11-08')
        with patch.object(subject.refresh.ml, 'feature_matrix', side_effect=ValueError('insufficient feature history')):
            result = subject.numerical_pair(self.current, self.test, fixture)
        self.assertEqual(result['current']['status'], 'SCORED')
        self.assertEqual(result['test']['status'], 'ABSTAIN')


if __name__ == '__main__': unittest.main()
