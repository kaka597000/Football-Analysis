import copy
import unittest
from research_program.bounded_fusion import choose, study
from research_program.score_mixture_shadow import BASELINE


def score(hits, loss=2.):
    return {'n': 20, 'statistical_top1_hits': hits, 'wdl_hits': 10,
            'score_log_loss': loss, 'wdl_brier': .5, 'coverage': 1.}


class BoundedFusionTests(unittest.TestCase):
    def test_gate_does_not_try_successful_runner_up(self):
        first, second = ('lightgbm', 1.), ('lightgbm', .5)
        train = {BASELINE: score(2), first: score(4), second: score(3)}
        gate = {BASELINE: score(2), first: score(1), second: score(5)}
        result = choose([train, gate, gate])
        self.assertEqual(result['selected'], first)
        self.assertEqual(result['retained'], BASELINE)

    def test_diagnostic_outcomes_cannot_change_retained_weight(self):
        key = ('lightgbm', .25)
        inputs = [{BASELINE: score(2), key: score(3)} for _ in range(3)]
        before = choose(inputs)
        after = copy.deepcopy(inputs)
        after[2][key] = score(0, 20.)
        self.assertEqual(before['retained'], choose(after)['retained'])

    def test_quality_regression_prevents_top1_only_selection(self):
        values = {BASELINE: score(2), ('lightgbm', 1.): score(8, 3.)}
        self.assertEqual(choose([values] * 3)['retained'], BASELINE)

    def test_training_overlap_and_duplicate_ids_rejected(self):
        row = {'historical_proxy': True, 'game_date': '2025-01-01', 'match_id': 'a'}
        with self.assertRaises(ValueError):
            study([row], {'cutoff_at': '2025-01-01'})
        with self.assertRaises(ValueError):
            study([row, row], {'cutoff_at': '2024-01-01'})
