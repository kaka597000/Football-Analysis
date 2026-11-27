import unittest
from unittest.mock import patch

from research.analysis_freeze import aware
from research_program import j1_checkpoint_review as checkpoints


class J1CheckpointReviewTest(unittest.TestCase):
    def setUp(self):
        self.fixtures = {'game': {}, 'unissued': {}}
        self.finals = [dict(match_id='game', home_goals=2, away_goals=1,
                            observed_at='2026-11-28T15:05:00+08:00')]
        self.starts = {'game': dict(earliest='2026-11-28T13:01:00+08:00', latest='2026-11-28T13:02:00+08:00')}
        self.at = aware('2026-11-28T15:10:00+08:00')

    def test_checkpoints_use_independent_scores_and_actual_total_lines(self):
        overnight = self.record('first', '2026-11-27T12:55:00+08:00', ['1-0', '2-1', '1-1'], 3)
        morning = self.record('second', '2026-11-28T06:56:00+08:00', ['2-1', '1-0', '1-1'], 2.75)
        waves = [('T-24h', {'game': overnight}), ('T-6h', {'game': morning})]
        with patch.object(checkpoints, 'read_wave', side_effect=waves):
            report = checkpoints.review(['overnight', 'morning'], {}, self.fixtures,
                                        self.finals, self.starts, self.at)
        first, second = report['T-24h'], report['T-6h']
        for node in (first, second):
            self.assertEqual((node['registered'], node['issued'], node['settled']), (2, 1, 1))
            self.assertEqual(node['metrics']['score_top3']['hits'], 1)
        self.assertEqual(first['metrics']['score_top1']['hits'], 0)
        self.assertEqual(second['metrics']['score_top1']['hits'], 1)
        self.assertEqual(first['total_settlements']['PUSH'], 1)
        self.assertEqual(first['metrics']['actual_ou']['eligible'], 0)
        self.assertEqual(second['total_settlements']['HALF_WIN'], 1)
        self.assertEqual(second['metrics']['actual_ou'], dict(hits=0, eligible=1, accuracy=0))

    def test_unverified_start_and_late_regulation_do_not_score(self):
        record = self.record('first', '2026-11-27T12:55:00+08:00', ['2-1', '1-0', '1-1'], 3)
        premature_results = [dict(self.finals[0], observed_at='2026-11-28T14:31:59+08:00')]
        with patch.object(checkpoints, 'read_wave', return_value=('T-24h', {'game': record})):
            early = checkpoints.review(['wave'], {}, self.fixtures, [], {}, aware('2026-11-27T12:54:59+08:00'))
            unresolved = checkpoints.review(['wave'], {}, self.fixtures, self.finals, {}, self.at)
            premature = checkpoints.review(['wave'], {}, self.fixtures, premature_results, self.starts, self.at)
        self.assertEqual(early['T-24h']['issued'], 0)
        self.assertEqual(unresolved['T-24h']['per_match']['game']['status'], 'AWAITING_VERIFIED_REGULATION')
        self.assertEqual(premature['T-24h']['per_match']['game']['status'], 'UNVERIFIED_CHRONOLOGY')
        for report in (early, unresolved, premature):
            self.assertEqual(report['T-24h']['metrics']['score_top1']['eligible'], 0)

    @staticmethod
    def record(freeze_id, at, scores, line):
        forecast = dict(primary=scores[0], top3=scores, wdl_probabilities=dict(home=.5, draw=.3, away=.2),
                        full_win_total=dict(line=line, direction='over'))
        return dict(freeze_id=freeze_id, recorded_at=at, payload=dict(forecast=forecast))
