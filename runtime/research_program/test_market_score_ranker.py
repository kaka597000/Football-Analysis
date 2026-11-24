from unittest import TestCase
from unittest.mock import patch

import numpy as np

from research_program import market_score_ranker as ranker
from research_program import test_market_baseline as market_tests


class ScoreRankerTests(TestCase):
    def test_same_line_quotes_only(self):
        main = dict(bookmaker='a', h2h={'home':2.,'draw':3.,'away':4.}, total={'line':2.5,'over':1.9,'under':2.})
        peer = dict(bookmaker='b', h2h={'home':1.8,'draw':3.5,'away':5.}, total={'line':2.75,'over':2.,'under':1.9})
        x, combined = ranker.market_features(main, {'b':peer})
        single = ranker.normalize_market(main)
        self.assertEqual(len(x),14)
        self.assertTrue(np.allclose(single, ranker.normalize_market(combined)))
        peer['total']['line']=2.5
        _, multi = ranker.market_features(main, {'b':peer})
        self.assertFalse(np.allclose(single, ranker.normalize_market(multi)))

    def test_joint_tail_retains_within_bucket_proportions(self):
        main = {'h2h':{'home':2.,'draw':3.,'away':4.},'total':{'line':2.5,'over':1.9,'under':2.}}
        m = ranker.normalize_market(main)
        target = np.full((1,25),1/25.)
        revised = ranker._redistribute(m[None,:,:],target)[0]
        self.assertAlmostEqual(revised.sum(),1.)
        self.assertAlmostEqual(revised[4:,0].sum(),1/25.)
        self.assertAlmostEqual(revised[5,0]/revised[4,0],m[5,0]/m[4,0])

    def test_tied_direction_is_stable(self):
        self.assertEqual(ranker.stable_wdl([.41,.18,.41+1e-15]),0)
        self.assertEqual(ranker.stable_wdl([.41,.18,.41+1e-5]),2)

    def test_form_arm_reverts_to_only_verified_market_if_form_absent(self):
        fixture = dict(market_tests.MarketBaselineTests().fixture(),cohort='eng.1',stage='regular')
        a = {'version':ranker.VERSION,'arm':'form_rank_25','market_model':'quoted','form_model':'form',
             'built_at':'2026-11-23T10:00:00Z','selection_end':'2025-05-31'}
        a['artifact_sha256']=ranker.artifact_identity(a)
        with patch.object(ranker,'mix') as unused:
            unused.side_effect=lambda anchors, text, features, fraction: anchors
            result = ranker.predict(a,fixture)
        self.assertEqual(result['status'],'AVAILABLE')
        self.assertEqual(result['arm'],'market_rank_25')
        self.assertEqual(result['fallback_from'],'form_rank_25')
        self.assertEqual(unused.call_args.args[1],'quoted')
        self.assertEqual(len(set(result['top3'])),3)

    def test_peer_only_same_capture_and_valid_clock(self):
        fixture = market_tests.MarketBaselineTests().fixture()
        other = [dict(row,bookmaker='other') for row in fixture['market_snapshots']]
        fixture['market_snapshots'] += other
        quote = ranker.market_forecast(fixture)['quote_snapshot']
        self.assertIn('other',ranker.simultaneous_peers(fixture,quote))
        other[-1]['query_time']='2026-11-23T10:02:00Z'
        self.assertFalse(ranker.simultaneous_peers(fixture,quote))
