import unittest
from automation import research_api as api
from automation import collector
from research_program import three_arm_review


class PublicReviewContractTests(unittest.TestCase):
    def test_v1_preserves_queue_and_frozen_review_implementations(self):
        self.assertEqual(api.API_VERSION, 1)
        self.assertIs(api.ScoreboardQueue, collector._ScoreboardQueue)
        self.assertIs(api.read_predictions, three_arm_review._predictions)
        self.assertIs(api.read_results, three_arm_review._results)
        self.assertIs(api.certify_result, three_arm_review._certify)
