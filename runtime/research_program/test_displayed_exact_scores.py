import unittest

from research_program.displayed_exact_scores import compare


class DisplayedScoresTests(unittest.TestCase):
    def test_other_is_never_counted_and_price_ties_are_audited(self):
        singles = [{'score':s,'decimal':p} for s,p in [('1-1',6.),('2-1',7.),('0-1',7.),('1-0',8.)]]
        result = compare(singles,[{'label':'other home','decimal':6.5}])
        self.assertEqual(result['top3'],['1-1','0-1','2-1'])
        self.assertEqual(result['price_tie_counts'],[1,2,2])
        self.assertEqual(result['other_buckets_at_or_shorter_than_third'],['other home'])
        self.assertIsNone(result['inferred_same_page'])

    def test_only_identical_offered_prices_use_the_same_page_model(self):
        singles = [{'score':s,'decimal':p} for s,p in [('1-1',5.),('0-1',7.),('2-1',7.),('3-0',8.)]]
        result = compare(singles,[],h2h={'home':1.34,'draw':5.4,'away':7.9},
                         totals=[{'line':3.5,'over':2.5,'under':1.52},{'line':3.,'over':1.95,'under':1.93}])
        self.assertEqual(result['top3'],['1-1','2-1','0-1'])
        self.assertEqual(result['inferred_same_page']['total']['line'],3.)
        with self.assertRaisesRegex(ValueError,'one price'):
            compare(singles + singles[:1],[])
