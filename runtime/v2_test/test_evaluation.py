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
    def test_frozen_direction_is_not_score_direction_or_probability_mode(self):
        row={'matrix':[[.4,.1],[.2,.3]],'home_goals':1,'away_goals':0,'primary_score':[1,0],'wdl_reference':'主胜'}
        r=evaluate([row,dict(row,primary_score=None,wdl_reference=None)])
        self.assertEqual(r['metrics']['wdl_accuracy'],0.)
        self.assertEqual(r['frozen_wdl']['hits'],1);self.assertEqual(r['frozen_wdl']['n'],1)
        self.assertEqual(r['frozen_wdl']['coverage'],.5)
        self.assertEqual(r['frozen_wdl']['net_hits_vs_probability_direction_same_subset'],1)
        self.assertEqual(evaluate([dict(row,wdl_reference=None)])['frozen_wdl']['n'],0)
    def test_primary_outside_probability_support_is_not_a_valid_recommendation(self):
        with self.assertRaisesRegex(ValueError,'primary'):
            evaluate([{'matrix':[[1.]],'home_goals':0,'away_goals':0,'primary_score':[1,0]}])
if __name__=='__main__':unittest.main()
