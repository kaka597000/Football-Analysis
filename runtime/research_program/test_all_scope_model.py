from datetime import date
import unittest

import numpy as np

from research_program import all_scope_model as m
from research_program import all_scope_study as study


class AllScopeTests(unittest.TestCase):
    def game(self, **changes):
        return dict(dict(id='x',competition='swe.1',stage='regular',date='2025-02-01',
                         home='home',away='visitor',neutral=False,home_goals=2,away_goals=0,
                         h2h=None,total=None,paired_market=False,source_sha256='a'*64),**changes)

    def test_neutral_cold_start_is_symmetric_and_missing_prices_stay_missing(self):
        state=m.blank_state()
        row=self.game(neutral=True)
        p=m.prepare(state,row,date.fromisoformat(row['date']).toordinal(),270)
        self.assertEqual(p['kind'],'team_fallback')
        np.testing.assert_allclose(p['team_m'],p['team_m'].T)
        picks=study.forecasts(state,p,None,row,dict(first='team',backups='team',wdl='team',total='team'))
        self.assertEqual(picks['market'],{})
        self.assertIsNone(picks['candidate']['total_side'])
        self.assertEqual(len(set(picks['candidate']['top3'])),3)

    def test_two_independent_quotes_must_not_turn_into_a_paired_score_market(self):
        state=m.blank_state()
        row=self.game(h2h=dict(home=2.1,draw=3.4,away=3.5),total=dict(line=2.75,over=1.85,under=2.05))
        p=m.prepare(state,row,100,270)
        self.assertEqual(p['kind'],'hda_only')
        out=study.forecasts(state,p,None,row)['market']
        self.assertEqual(set(out),{'wdl','total_side','total_line'})
        row['paired_market']=True
        paired=m.prepare(state,row,100,270)
        self.assertEqual(paired['kind'],'paired')
        self.assertEqual(len(study.forecasts(state,paired,None,row)['market']['top3']),3)

    def test_same_day_and_future_labels_are_inaccessible_to_prediction(self):
        a=self.game()
        b=self.game(id='y',away='other',home_goals=0,away_goals=4)
        c=self.game(id='z',date='2025-02-02')
        original=study.walk([a,b,c],'swe.1',{}, {})
        changed=study.walk([dict(a,home_goals=7),dict(b,home_goals=7),c],'swe.1',{}, {})
        self.assertEqual(original[0]['forecast'],changed[0]['forecast'])
        self.assertEqual(original[1]['forecast'],changed[1]['forecast'])
        self.assertNotEqual(original[2]['forecast']['team'],changed[2]['forecast']['team'])
        isolated=study.walk([dict(a,stage='final'),c],'swe.1',{}, {})
        self.assertEqual(isolated[1]['forecast'],study.walk([c],'swe.1',{}, {})[0]['forecast'])

    def test_line_only_settles_candidate_and_retains_unpriced_market_baseline(self):
        row=self.game(total=dict(line=2.25,over=None,under=None))
        scores=study.walk([row],'swe.1',{}, {},policy=dict.fromkeys(('first','wdl','backups','total'),'anchor'))
        self.assertIsNotNone(scores[0]['forecast']['candidate']['total_side'])
        self.assertEqual(scores[0]['forecast']['market'],{})
        self.assertEqual(study._score(scores,'market')['actual_ou'],[0,0])
        self.assertEqual(study.boundaries('fifa.world')[:2],('2022-01-01','2026-01-01'))

    def test_selection_compares_the_same_first_plus_two_distinct_backups(self):
        one=dict(primary='1-1',top3=['1-1','1-0','0-0'],wdl='draw',total_side='under',total_line=2.5)
        other=dict(primary='2-1',top3=['2-1','1-1','3-0'],wdl='home',total_side='over',total_line=2.5)
        rows=[dict(actual=list(s),line=2.5,forecast={**dict.fromkeys(m.ARMS,one),'team':other})
              for s in [(1,1),(1,1),(2,1),(3,0),(3,1)]]
        policy,_=study.choose(rows)
        self.assertEqual(policy,dict(first='anchor',backups='team',wdl='team',total='team'))
        self.assertEqual(study.choose([])[0],dict.fromkeys(('first','backups','wdl','total'),'anchor'))


class AdditionalScoreTests(unittest.TestCase):
    def test_neighborhood_uses_past_matches_and_shrinks_sparse_cells(self):
        from research_program import all_scope_neighbors as n
        game=AllScopeTests().game()
        p=m.prepare(m.blank_state(),game,101,270)
        memory=n.Memory()
        self.assertIs(memory.matrix(p,101,270),p['anchor'])
        for day in range(1,90):
            memory.append(day,n.context(p),6,p['cell'])
        changed=memory.matrix(p,101,270)
        self.assertAlmostEqual(changed.sum(),1.)
        self.assertGreater(changed[1,1],p['anchor'][1,1])
        with self.assertRaises(ValueError):
            memory.matrix(p,89,270)

    def test_kleague_verifies_both_teams_year_stage_and_final_score(self):
        import json
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from research_program.kleague_result import regulation
        g=dict(year=2026,leagueId=1,roundId=36,gameId=213,gameDate='2026.11.21',gameTime='16:30',
               codeName='파이널A',gameStatus='FE',endYn='Y',homeTeam='K21',awayTeam='K10',homeGoal=1,awayGoal=3)
        r=dict(match_id='kleague:213',cohort='kor.1',stage='파이널A',kickoff_at='2026-11-21T07:30Z',
               home='kleague:K21',away='kleague:K10',home_goals=1,away_goals=3)
        e={'url':'https://www.kleague.com/getScheduleList.do'}
        with TemporaryDirectory() as d:
            p=Path(d)/'raw.json'
            p.write_text(json.dumps(dict(resultCode=200,data=dict(scheduleList=[g]))))
            regulation(r,e,p)
            for bad in (dict(r,stage='파이널B'),dict(r,away_goals=1),dict(r,home='kleague:K10')):
                with self.assertRaises(ValueError):
                    regulation(bad,e,p)

    def test_forward_uses_the_two_sides_of_the_same_time_and_line(self):
        from research_program.all_scope_forward import prices
        hd={'prices':{s:{'decimal':v} for s,v in zip(m.SIDES,(2.,3.,4.))}}
        ou={'over':{'decimal':2.1,'line':2.5},'under':{'decimal':1.8,'line':2.5}}
        r=dict(h2h={'close':hd},total={'open':ou})
        self.assertFalse(prices(r)[2])
        r['total']['close']=ou
        self.assertTrue(prices(r)[2])
        self.assertEqual(prices(r)[3],'close')
