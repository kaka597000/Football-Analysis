import unittest
from research_program import displayed_market_directions as market


class CompleteMarketTests(unittest.TestCase):
    def setUp(self):
        home=[(1,0),(2,0),(2,1),(3,0),(3,1),(3,2),(4,0),(4,1),(4,2),(5,0),(5,1),(5,2)]
        all_scores=home+[(a,h) for h,a in home]+[(i,i) for i in range(4)]
        self.offers=[dict(score=f'{h}-{a}',decimal=4. if (h,a)==(2,1) else 33.) for h,a in all_scores]
        self.groups=[dict(code=k,decimal=33.) for k in market.GROUPS]

    def test_groups_partition_margin_and_have_full_over_settlement_on_normal_lines(self):
        p=market.predict(self.offers,self.groups,2.75)
        self.assertAlmostEqual(sum(p['wdl_probabilities'].values()),1.)
        self.assertAlmostEqual(sum(p['total_settlements']['under'].values()),1.)
        self.assertEqual(p['wdl'],'home')
        self.assertEqual(p['minimum_unlisted_total_by_group'],{'HO':7,'OHN':6,'DO':8,'AO':7,'OAN':6})
        self.assertGreater(p['total_settlements']['over']['HALF_WIN'],0)
        self.assertEqual(p['total_settlements']['over']['FULL_WIN'],p['total_settlements']['under']['FULL_LOSS'])

    def test_an_unresolved_group_blocks_only_the_total_and_an_incomplete_book_fails(self):
        p=market.predict(self.offers,self.groups,6.25)
        self.assertEqual(p['wdl'],'home')
        self.assertIsNone(p['total_side'])
        with self.assertRaises(ValueError):
            market.predict(self.offers,self.groups[:-1],2.75)
