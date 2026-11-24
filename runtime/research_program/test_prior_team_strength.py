from datetime import date, timedelta
import unittest

import numpy as np

from research_program.kpi_score_candidate import artifact_identity
from research_program.prior_team_strength import (
    ARMS, VERSION, LeagueStrength, allocate_picks, observations, predict,
)


class StrengthTests(unittest.TestCase):
    def setUp(self):
        self.day = date(2025, 1, 2)
        self.state = LeagueStrength()
        self.target = observations([3, 0], [2.5, .5], [2., 1.])
        self.state.update_day(self.day, [('A','B',self.target)])

    def test_excludes_same_day_and_learning_decays(self):
        with self.assertRaisesRegex(ValueError, 'previous'):
            self.state.coefficients(self.day)
        with self.assertRaisesRegex(ValueError, 'new match date'):
            self.state.update_day(self.day,[('B','C',self.target)])
        next_day = self.state.coefficients(self.day+timedelta(days=1))
        later = self.state.coefficients(self.day+timedelta(days=365))
        early = self.state.rates(next_day,'A','B')
        far = self.state.rates(later,'A','B')
        self.assertGreater(early[0, 0], far[0, 0])
        self.assertLess(early[1, 0], far[1, 0])
        # The unobserved team's own attack has no coefficient. Its opponent still matters.
        self.assertGreater(early[0, 0], self.state.rates(next_day,'new','B')[0, 0])
        np.testing.assert_allclose(LeagueStrength.restore(self.state.snapshot()).rates(next_day,'A','B'),early)
        np.testing.assert_allclose(observations([3,0]), [[3,3,3],[0,0,0]])
        np.testing.assert_allclose(self.target[:,-1], [2.4,.6])

    def test_date_block_order_and_ratings_across_home_away(self):
        games = [('B','C',self.target),('D','A',self.target)]
        other = LeagueStrength.restore(self.state.snapshot())
        for state, schedule in ((other,games[::-1]),(self.state,games)):
            state.update_day(self.day+timedelta(days=1),schedule)
        def rates(state):
            return state.rates(state.coefficients(self.day+timedelta(days=2)),'D','B')
        np.testing.assert_allclose(rates(other),rates(self.state))

    def test_first_and_two_backups_have_independent_direction(self):
        old = {'top3':['1-1','1-0','2-1'], 'wdl':'home','total_side':'under'}
        new = {'top3':['0-1','0-2','1-0'], 'wdl':'away','total_side':'over'}
        policy = {'scores':'incumbent_first_strength_backups','wdl':'strength','total':'strength'}
        out = allocate_picks(old,new,policy)
        self.assertEqual(out,{'primary':'1-1','top3':['1-1','0-1','0-2'],'wdl':'away','total_side':'over'})
        self.assertEqual(allocate_picks({},new,policy)['top3'],new['top3'])
        new['top3'] = ['0-1','1-1','0-2']
        self.assertEqual(allocate_picks(old,new,policy)['top3'],['1-1','0-1','0-2'])

    def test_forward_uses_frozen_history_and_real_line_only(self):
        a = {'version':VERSION,'selected_strength':ARMS[-1],'use_market_mix':False,
             'selection_end':'2025-01-01','built_at':'2025-01-03T01:00:00Z',
             'team_map':{'ita.1':{'espn:1':'A','espn:2':'B'}},'states':{'ita.1':self.state.snapshot()}}
        a['artifact_sha256'] = artifact_identity(a)
        f = {'match_id':'espn:44','home':'espn:1','away':'espn:2','cohort':'ita.1','stage':'regular',
             'neutral':False,'score_basis':'regulation_90','cutoff_at':'2025-01-03T02:00:00Z',
             'kickoff_at':'2025-01-05T20:00:00Z'}
        out = predict(a, f)
        self.assertEqual(out['primary'],out['top3'][0])
        self.assertIsNone(out['total'])
        self.assertEqual(out['last_team_dates'],[self.day.isoformat()]*2)
        self.assertEqual(predict(a, dict(f,home='espn:9'))['status'],'ABSTAIN_UNMAPPED_TEAMS')
        self.assertEqual(predict(a, dict(f,cohort='jpn.1'))['status'],'ABSTAIN_OUTSIDE_TRAINED_SCOPE')
        with self.assertRaisesRegex(ValueError,'identity'):
            predict(dict(a,selected_strength='prior_goals'),f)
