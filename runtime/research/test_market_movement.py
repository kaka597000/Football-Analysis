import copy,unittest
from market_movement import analyze

class Tests(unittest.TestCase):
    def fixture(self):
        def row(t,line=2.5):return {'match_id':'synthetic','bookmaker':'Example','market':'totals','line':line,'decimal_odds':{'over':1.9,'under':1.9},'quoted_at':t,'query_time':t,'source_url':'https://example.test/odds'}
        return {'match_id':'synthetic','cutoff_at':'2026-01-01T12:00:00Z','kickoff_at':'2026-01-02T12:00:00Z','market_snapshots':[row('2026-01-01T10:00:00Z'),row('2026-01-01T11:00:00Z')]}
    def test_observed_same_line_price_change_no_prediction_mutation(self):
        f=self.fixture();f['market_snapshots'][1]['decimal_odds']['over']=1.8;before=copy.deepcopy(f);r=analyze(f)
        self.assertEqual(r['status'],'COMPARABLE_HISTORY');self.assertAlmostEqual(r['comparisons'][0]['decimal_odds_change']['over'],-.1);self.assertEqual(f,before);self.assertFalse(r['prediction_modified'])
    def test_different_book_or_line_does_not_create_price_movement(self):
        for field,value in [('bookmaker','Other'),('line',3.0)]:
            f=self.fixture();f['market_snapshots'][1][field]=value;self.assertEqual(analyze(f)['comparisons'],[])
    def test_untimed_future_wrong_identity_or_invalid_odds_rejected(self):
        for key,value in [('quoted_at',None),('quoted_at','2026-01-03T10:00:00Z'),('match_id','other'),('decimal_odds',{'over':float('nan'),'under':1.9})]:
            f=self.fixture();f['market_snapshots'][1][key]=value;self.assertEqual(analyze(f)['rejected_snapshots'],1);self.assertEqual(analyze(f)['comparisons'],[])
if __name__=='__main__':unittest.main()
