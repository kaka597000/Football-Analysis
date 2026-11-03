import math,unittest
from evaluation import evaluate
class Tests(unittest.TestCase):
    def test_known_matrix_and_primary_net_gain(self):
        r=evaluate([{'matrix':[[.4,.1],[.2,.3]],'home_goals':1,'away_goals':1,'primary_score':[1,1],'market_wdl':[.2,.7,.1]}])
        self.assertEqual(r['metrics']['exact_top1'],0);self.assertEqual(r['metrics']['exact_top3'],1);self.assertAlmostEqual(r['metrics']['score_log_loss'],-math.log(.3));self.assertEqual(r['primary']['net_hits_vs_probability_top1_same_subset'],1);self.assertEqual(r['market_paired_n'],1);self.assertIsNone(r['tail']['total5+']['recall_at_preregistered_half'])
    def test_invalid_matrix_and_missing_scores_not_silently_dropped(self):
        with self.assertRaises(ValueError):evaluate([{'matrix':[[.2]],'home_goals':0,'away_goals':0}])
        with self.assertRaises(ValueError):evaluate([])
    def test_abstention_is_in_population_but_not_conditional_accuracy(self):
        rows=[{'matrix':[[.4,.1],[.2,.3]],'home_goals':1,'away_goals':1,'primary_score':[1,1]},
              {'matrix':[[.4,.1],[.2,.3]],'home_goals':1,'away_goals':1}]
        p=evaluate(rows)['primary']
        self.assertEqual(p['hits'],1);self.assertEqual(p['n'],1)
        self.assertEqual(p['coverage'],.5);self.assertEqual(p['accuracy'],1.)
        self.assertEqual(p['full_population_accuracy'],.5)
        p=evaluate(rows[1:])['primary']
        self.assertIsNone(p['accuracy']);self.assertEqual(p['full_population_accuracy'],0.)
if __name__=='__main__':unittest.main()
