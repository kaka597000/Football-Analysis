"""Contract checks for score metrics, denominator accounting and paired inference."""
import json
import math
import unittest

import numpy as np

from evaluate import paired, score_prediction, settle_ou, summarize


def prediction(mid='m1', day='2025-01-01', matrix=None, **extra):
    if matrix is None:
        matrix = [[.1, .1], [.6, .2]]
    return dict(match_id=mid, date=day, model='A', status='PREDICTED', matrix=matrix, **extra)


def label(h=1, a=0):
    return dict(home_goals=h, away_goals=a)


class EvaluationTests(unittest.TestCase):
    def test_matrix_metrics_have_known_values(self):
        row = score_prediction(prediction(), label())
        self.assertEqual(row['mode_score'], [1, 0])
        self.assertEqual(row['top1'], 1)
        self.assertAlmostEqual(row['mu_total'], 1.1)
        self.assertAlmostEqual(row['total_error'], .1)
        self.assertEqual(row['mode_total_error'], 0)
        self.assertAlmostEqual(row['score_log_loss'], -math.log(.6))
        self.assertAlmostEqual(row['total_log_loss'], -math.log(.7))
        self.assertAlmostEqual(row['total_crps'], .05)
        self.assertAlmostEqual(row['wdl_brier'], .26)
        self.assertAlmostEqual(row['wdl_log_loss'], -math.log(.6))
        self.assertAlmostEqual(row['total_variance'], .29)
        self.assertAlmostEqual(row['poisson_deviance'], 2*(math.log(1/1.1)+.1))
        self.assertEqual(row['actual_market_status'], 'NA')
        self.assertIn('fixed_2_5_diagnostic_settlement', row)

    def test_mode_and_expected_total_are_separate(self):
        matrix = [[.4, 0, 0], [0, 0, 0], [0, 0, .6]]
        row = score_prediction(prediction(matrix=matrix), label(1, 1))
        self.assertEqual(row['mode_total'], 4)
        self.assertAlmostEqual(row['mu_total'], 2.4)
        self.assertAlmostEqual(row['total_error'], .4)
        self.assertEqual(row['mode_total_error'], 2)
        self.assertAlmostEqual(row['p_zero'], .4)
        self.assertAlmostEqual(row['p_four_plus'], .6)
        self.assertEqual(row['observed_zero'], 0)
        self.assertAlmostEqual(row['four_plus_brier'], .36)

    def test_out_of_support_labels_have_finite_loss_and_correct_crps(self):
        row = score_prediction(prediction(matrix=[[1.]]), label(3, 0))
        self.assertAlmostEqual(row['score_log_loss'], -math.log(1e-15))
        self.assertAlmostEqual(row['total_log_loss'], -math.log(1e-15))
        self.assertAlmostEqual(row['total_crps'], 3.)
        self.assertEqual(row['interval90_covered'], 0)
        json.dumps(row, allow_nan=False)

    def test_denominators_keep_rejected_and_pending(self):
        good = score_prediction(prediction(), label())
        rejected = score_prediction(dict(match_id='m2', date='2025-01-02', model='A', status='ABSTAIN'), label())
        pending = score_prediction(prediction('m3'), None)
        rejected_pending = score_prediction(dict(match_id='m4', date='2025-01-03', model='A', status='FAILED'), None)
        summary = summarize([good, rejected, pending, rejected_pending])
        self.assertEqual([summary[k] for k in ('N_scope', 'N_pred', 'N_settled', 'N_scored', 'N_pending', 'N_abstain')], [4, 2, 2, 1, 2, 2])
        self.assertEqual(summary['top1_rate'], 1.)
        self.assertEqual(summary['coverage'], .5)
        self.assertEqual(summary['coverage_adjusted_top1'], .5)
        self.assertEqual(summary['actual_market']['N_NA'], 4)
        self.assertEqual(summary['week_block_ci']['status'], 'INSUFFICIENT_WEEK_BLOCKS')
        self.assertIsNone(rejected['top1'])
        self.assertIsNone(pending['top1'])

    def test_integer_half_and_quarter_settlements(self):
        cases = [(2, 2.0, 'over', 'PUSH'), (3, 2.5, 'over', 'FULL_WIN'),
                 (2, 2.25, 'under', 'HALF_WIN'), (2, 2.25, 'over', 'HALF_LOSS'),
                 (3, 2.75, 'over', 'HALF_WIN'), (3, 2.75, 'under', 'HALF_LOSS'),
                 (2, 3.0, 'over', 'FULL_LOSS')]
        for total, line, side, expected in cases:
            with self.subTest(total=total, line=line, side=side):
                self.assertEqual(settle_ou(total, line, side), expected)
        self.assertEqual(settle_ou(np.int64(3), 2.75, 'over'), 'HALF_WIN')
        for bad in (2.1, float('nan'), -1):
            with self.assertRaises(ValueError):
                settle_ou(2, bad, 'over')
        with self.assertRaises(ValueError):
            settle_ou(2, 2.5, 'home')

    def test_actual_market_requires_identity_prices_and_cutoff(self):
        quote = dict(match_id='m1', bookmaker='book', line=2.25, over_odds=1.9,
                     under_odds=1.95, quote_at='2025-01-01T00:00:00Z', side='under')
        pred = prediction(actual_market=quote, cutoff_at='2025-01-01T01:00:00Z')
        row = score_prediction(pred, label(2, 0))
        self.assertEqual(row['actual_market_status'], 'SETTLED')
        self.assertEqual(row['actual_market_settlement'], 'HALF_WIN')
        for patch in ({'match_id': 'other'}, {'quote_at': '2025-01-01T02:00:00Z'},
                      {'under_odds': None}, {'side': None}):
            bad = dict(pred, actual_market=dict(quote, **patch))
            self.assertEqual(score_prediction(bad, label())['actual_market_status'], 'NA')
        pending = score_prediction(pred, None)
        self.assertEqual(pending['actual_market_status'], 'PENDING')
        self.assertIsNone(pending['actual_market_settlement'])

    def test_matrix_and_label_validation(self):
        for matrix in ([[.8]], [[-1., 1.], [0., 1.]], [[float('nan')]]):
            with self.assertRaises(ValueError):
                score_prediction(prediction(matrix=matrix), label())
        with self.assertRaises(ValueError):
            score_prediction(prediction(), dict(match_id='other', **label()))
        with self.assertRaises(ValueError):
            score_prediction(prediction(), label(1.5, 0))

    def test_pairing_uses_same_matches_and_preserves_full_scopes(self):
        a = [score_prediction(prediction('m1'), label()),
             score_prediction(prediction('m2', '2025-01-08'), label(0, 0))]
        b = [score_prediction(dict(prediction('m1', matrix=[[1., 0], [0, 0]]), model='B'), label()),
             score_prediction(dict(prediction('m2', '2025-01-08', [[1., 0], [0, 0]]), model='B'), label(0, 0)),
             score_prediction(dict(prediction('m3'), model='B'), None)]
        result = paired(a, b, replicates=200)
        self.assertEqual(result['A']['N_scope'], 2)
        self.assertEqual(result['B']['N_scope'], 3)
        self.assertEqual(result['N_common_scored'], 2)
        self.assertEqual(result['right_to_wrong'], 1)
        self.assertEqual(result['wrong_to_right'], 1)
        self.assertEqual(result['net_top1_hits'], 0)
        self.assertEqual(result['mean_difference']['top1'], 0)
        self.assertEqual(result['week_block_ci']['n_blocks'], 2)
        self.assertEqual(result['week_block_ci']['metrics']['top1'], [-1., 1.])

    def test_week_blocks_share_cross_league_matches(self):
        rows = [score_prediction(prediction('m1', '2025-01-01'), label()),
                score_prediction(prediction('m2', '2025-01-02'), label()),
                score_prediction(prediction('m3', '2025-01-08'), label(0, 0))]
        result = summarize(rows, replicates=200)
        self.assertEqual(result['week_block_ci']['n_blocks'], 2)
        self.assertEqual(result['week_block_ci']['metrics']['top1'], [0., 1.])
        self.assertGreater(result['week_block_ci']['standard_errors']['top1'], 0)
        self.assertEqual(result['week_block_ci'], summarize(rows, replicates=200)['week_block_ci'])

    def test_duplicate_versions_and_mismatched_pair_labels_fail_closed(self):
        row = score_prediction(prediction(), label())
        with self.assertRaises(ValueError):
            summarize([row, row])
        with self.assertRaises(ValueError):
            summarize([row, score_prediction(dict(prediction('m2'), model='B'), label())])
        with self.assertRaises(ValueError):
            paired([row], [score_prediction(dict(prediction(), model='B'), label(0, 0))])

    def test_empty_scope_has_na_rates(self):
        result = summarize([])
        self.assertEqual(result['N_scope'], 0)
        self.assertIsNone(result['coverage'])
        self.assertIsNone(result['top1_rate'])
        self.assertEqual(result['week_block_ci']['n_blocks'], 0)
        json.dumps(result, allow_nan=False)


if __name__ == '__main__':
    unittest.main()
