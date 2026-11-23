import unittest
import numpy as np
from research_program import conditional_scores as m
from research_program import score_gap_study as study


class ConditionalScoresTests(unittest.TestCase):
    def fixture(self,**changes):
        return dict(dict(id='i',competition='swe.1',date='2017-04-02',stage='league',home='H',away='A',
                    home_goals=3,away_goals=0,neutral=False,h2h=None,total=None,paired_market=False),**changes)

    def test_different_market_sources_constrain_both_marginals_and_preserve_book_identity(self):
        g=self.fixture(h2h={'home':1.65,'draw':4.1,'away':5.3},total={'line':2.25,'over':2.1,'under':1.8})
        p=m.components(m.blank(),g,100,270)
        self.assertEqual(p['market']['kind'],'separate_markets')
        a=p['structural']
        sides=np.bincount(m.base.RESULT.ravel(),weights=a.ravel(),minlength=3)
        np.testing.assert_allclose(sides,p['market']['hprob'])
        outcomes=m.base._net(2.25)
        self.assertAlmostEqual((a*np.maximum(outcomes,0)).sum()/(a*np.abs(outcomes)).sum(),1.8/3.9)
        pred,_=m.forecast(g,p,dict(goals=m.ZERO_COEFS,process=m.ZERO_COEFS))
        self.assertNotIn('primary',pred['market'])
        self.assertEqual(len(pred['price_pair_proxy']['top3']),3)
        self.assertEqual(pred['conditional']['total_side'],pred['market']['total_side'])

    def test_prior_process_and_cross_phase_information_only_become_available_after_settlement(self):
        g=self.fixture()
        state=m.blank()
        m.advance(state,[g],100,270)
        a=m.components(state,g,100,270)
        process=dict(g,home_shots=18,away_shots=0,home_sot=9,away_sot=0)
        scores=dict(goals=m.ZERO_COEFS,process=m.ZERO_COEFS)
        old=m.forecast(g,a,scores)[0]
        changed=m.forecast(self.fixture(home_goals=0,away_goals=9),a,scores)[0]
        self.assertEqual(old,changed)
        self.assertIsNone(m.process_proxy({}))
        np.testing.assert_allclose(m.process_proxy(dict(home_xg=0,away_xg=0)),[0,0])
        m.update(state,[(g,a)],{g['id']:process},100,270)
        nxt=self.fixture(stage='final',id='j')
        m.advance(state,[nxt],120,270)
        b=m.components(state,nxt,120,270)
        self.assertEqual(b['local_games'],[0,0])
        self.assertEqual(b['pooled_games'],[1,1])
        self.assertGreater(b['process_exposure'],0)
        self.assertNotEqual(b['signal'].tolist(),b['goal_signal'].tolist())

    def test_conditional_fit_improves_log_score_and_top_three_contains_the_first(self):
        game=self.fixture()
        p=m.components(m.blank(),game,100,270); raw=p['structural']
        examples=[m.example(raw,p['goal_signal'],[0,0]) for _ in range(45)]
        fitted=m.fit(examples)
        changed=m.distribution(raw,p['goal_signal'],fitted)
        self.assertGreater(changed[0,0],raw[0,0])
        self.assertAlmostEqual(changed.sum(),1.)
        self.assertTrue(np.all(np.abs(fitted)<=m.BOUND))
        picks,_=m.forecast(game,p,dict(goals=fitted,process=fitted))
        self.assertEqual(picks['conditional']['primary'],picks['conditional']['top3'][0])
        self.assertEqual(len(set(picks['conditional']['top3'])),3)
        self.assertIsNone(picks['conditional']['total_side'])

    def test_early_totals_require_matching_source_and_no_replacement(self):
        g=self.fixture(source_sha256='a')
        quote={'i':dict(line=2.5,over=2.,under=1.9,same_book=False,source_sha256='b')}
        with self.assertRaises(ValueError):study.add_totals([g],quote)
        quote['i']['source_sha256']='a';study.add_totals([g],quote)
        self.assertFalse(g['paired_market'])
        with self.assertRaises(ValueError):study.add_totals([g],quote)

if __name__=='__main__':
    unittest.main()
