import unittest
from research_program.portfolio_lifecycle import decide


def rows(n=20, *, pending=False, missing=False, worse=False):
    return [{'match_id':str(i), 'matchday':f'2026-11-{i+1:02d}', 'outcomes': None if pending and i==0 else
             {'baseline': {'score_top1':False,'score_log_loss':2.,'wdl_brier':.5,'total_settlement':'PUSH'},
              **({} if missing and i==0 else {'candidate': {'score_top1':True,'score_log_loss':3. if worse else 1.9,
                                                         'wdl_brier':.49,'total_settlement':'FULL_WIN'}})}} for i in range(n)]


class LifecycleTests(unittest.TestCase):
    def status(self, data):
        return decide(data, ['baseline','candidate'], 'baseline')['decisions']['candidate']['status']

    def test_pending_round_never_retired_or_promoted(self):
        self.assertEqual(self.status(rows(pending=True)), 'PENDING_ORIGINAL_VALIDATION')

    def test_missing_and_quality_regression_close_without_promotion(self):
        self.assertEqual(self.status(rows(missing=True)), 'CLOSED_INCOMPLETE_COVERAGE')
        self.assertEqual(self.status(rows(worse=True)), 'CLOSED_QUALITY_REGRESSION')

    def test_success_only_allows_review_and_keeps_baseline(self):
        value = decide(rows(), ['baseline','candidate'], 'baseline')
        self.assertEqual(value['one_review_slot'], 'candidate')
        self.assertFalse(value['automatic_promotion'])
        self.assertTrue(value['baseline_remains_active'])

    def test_single_matchday_is_not_validation_strength(self):
        data=rows()
        for row in data: row['matchday']='2026-11-18'
        self.assertEqual(self.status(data), 'CLOSED_INCONCLUSIVE')

    def test_no_advantage_closes_and_duplicate_matches_rejected(self):
        data=rows()
        for row in data:row['outcomes']['candidate']['score_top1']=False
        self.assertEqual(self.status(data), 'CLOSED_NO_TOP1_ADVANTAGE')
        with self.assertRaises(ValueError):decide(data+data, ['baseline','candidate'], 'baseline')

    def test_only_one_review_slot_and_family_correction(self):
        import copy
        data=rows()
        for row in data:row['outcomes']['second']=copy.deepcopy(row['outcomes']['candidate'])
        result=decide(data,['baseline','candidate','second'],'baseline')
        self.assertEqual(result['one_review_slot'],'candidate')
        self.assertEqual(result['decisions']['second']['status'],'SUPERSEDED_FOR_NEXT_REVIEW')
        self.assertEqual(result['decisions']['candidate']['family_adjusted_alpha'],.025)
