import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime,timezone,timedelta
from collector import collect,regulation_row,phase_for,ASIAN


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.scope={'cohort':'eng.league_cup:single_leg','competition':'eng.league_cup','stages':['fourth-round']}
        self.event={'id':'123','date':'2026-10-28T19:45Z','season':{'year':2026,'name':'2026 Carabao Cup, Fourth Round'},'competitions':[{'status':{'type':{'completed':True}},'competitors':[{'homeAway':'home','team':{'id':'1'}},{'homeAway':'away','team':{'id':'2'}}]}]}
        self.summary={'format':{'regulation':{'periods':2}},'header':{'id':'123','season':self.event['season'],'competitions':[{'date':self.event['date'],'neutralSite':False,'status':{'type':{'completed':True}},'competitors':[{'homeAway':'home','team':{'id':'1'},'linescores':[{'displayValue':'1'},{'displayValue':'1'},{'displayValue':'1'},{'displayValue':'5'}]},{'homeAway':'away','team':{'id':'2'},'linescores':[{'displayValue':'0'},{'displayValue':'2'},{'displayValue':'1'},{'displayValue':'4'}]}]}]}}
        self.now=datetime(2026,10,30,tzinfo=timezone.utc)

    def test_only_regulation_periods_count_and_source_time_is_capture(self):
        r=regulation_row(self.scope,self.event,self.summary,self.now.isoformat(),{})
        self.assertEqual((r['home_goals'],r['away_goals']),(2,2));self.assertEqual(r['known_at'],self.now.isoformat())
        d=copy.deepcopy(self.summary);d['format']={}
        with self.assertRaises(ValueError):regulation_row(self.scope,self.event,d,self.now.isoformat(),{})

    def test_stage_era_and_japanese_transition_rejected(self):
        s={'competition':'afc.cup','cohort':'afc.cup:CHAMPIONS_TWO:group_stage','stages':['group_stage']}
        self.assertIsNone(phase_for(s,{'season':{'year':2023,'name':'Group Stage'}}))
        self.assertEqual(phase_for(s,{'season':{'year':2026,'name':'Group Stage'}}),'group_stage')
        self.assertIsNone(phase_for(self.scope,{'season':{'name':'Semifinals'}}))
        self.assertIsNone(phase_for({'competition':'jpn.1','cohort':'jpn.1','stages':['regular']},{'date':'2026-05-01','season':{'name':'J1 League'}}))

    def test_research_phase_uses_exact_native_slug(self):
        scope={'competition':'uefa.europa','cohort':'uefa.europa','stages':['league-phase']}
        self.assertEqual(phase_for(scope,{'season':{'slug':'league-phase'}}),'league-phase')
        self.assertIsNone(phase_for(scope,{'season':{'slug':'knockout-round-playoffs','name':'League Phase'}}))
        self.assertIsNone(phase_for(scope,{'season':{'name':'League Phase'}}))

    def test_research_phase_survives_header_without_slug(self):
        scope={'competition':'uefa.europa','cohort':'uefa.europa','stages':['league-phase']}
        self.event['season']['slug']='league-phase'
        self.summary['header']['season']={'name':'UEFA Europa League, League Phase'}
        r=regulation_row(scope,self.event,self.summary,self.now.isoformat(),{})
        self.assertEqual(r['stage'],'league-phase')

    def test_collect_deduplicates_sources_and_results_and_keeps_private_raw(self):
        calls=[]
        def fetch(url,headers=None):
            calls.append(url)
            if url==ASIAN:return {'events':[]}
            if '/summary?' in url:return self.summary
            return {'events':[self.event]}
        with tempfile.TemporaryDirectory() as folder:
            first=collect(folder,[self.scope,self.scope],self.now,fetch,lookback_days=0)
            self.assertEqual(first['new_result_observations'],1)
            self.assertEqual(first['request_count'],3)
            self.assertTrue(list((Path(folder)/'raw').glob('*.json.gz')))
            second=collect(folder,[self.scope],self.now,fetch,lookback_days=0)
            self.assertEqual(second['new_result_observations'],0)
            self.assertEqual(len((Path(folder)/'normalized-results.jsonl').read_text().splitlines()),1)

    def test_missing_linescores_quarantined_without_label(self):
        self.summary['header']['competitions'][0]['competitors'][0].pop('linescores')
        def fetch(url,headers=None):
            return {} if url==ASIAN else self.summary if '/summary?' in url else {'events':[self.event]}
        with tempfile.TemporaryDirectory() as folder:
            result=collect(folder,[self.scope],self.now,fetch,lookback_days=0)
            self.assertEqual(result['new_result_observations'],0);self.assertEqual(result['quarantined'],1)

    def test_budget_and_single_retry_are_bounded(self):
        def failure(url,headers=None):raise TimeoutError('not persisted')
        with tempfile.TemporaryDirectory() as folder:
            result=collect(folder,[self.scope],self.now,failure,lookback_days=2)
            self.assertEqual(result['request_count'],5)
            self.assertTrue(result['retry_used'])
            self.assertNotIn('not persisted',(Path(folder)/'requests.jsonl').read_text())

    def test_bounded_runs_rotate_scoreboards_across_competitions_and_days(self):
        scopes=[{'competition':comp,'cohort':comp,'stages':['regular']}
                for comp in ('eng.1','ita.1','swe.1')]
        calls=[]
        def fetch(url,headers=None):
            calls.append(url)
            return {'events':[]}
        with tempfile.TemporaryDirectory() as folder, patch('collector.MAX_REQUESTS',1):
            for _ in range(6):
                result=collect(folder,scopes,self.now,fetch,lookback_days=1)
                self.assertEqual(result['request_count'],1)
            self.assertEqual(len(set(calls)),6)
            self.assertTrue(all('/scoreboard?' in url for url in calls))
            collect(folder,scopes,self.now,fetch,lookback_days=1)
            self.assertEqual(calls[6],calls[0])

    def test_failed_scoreboard_does_not_starve_unqueried_competitions(self):
        scopes=[{'competition':comp,'cohort':comp,'stages':['regular']}
                for comp in ('eng.1','swe.1')]
        calls=[]
        def failure(url,headers=None):
            calls.append(url)
            raise TimeoutError('synthetic outage')
        with tempfile.TemporaryDirectory() as folder, patch('collector.MAX_REQUESTS',1):
            for _ in range(2):collect(folder,scopes,self.now,failure,lookback_days=0)
        self.assertNotEqual(calls[0],calls[1])

    def test_budget_and_date_window_changes_keep_bounded_rotation_state(self):
        scopes=[{'competition':comp,'cohort':comp,'stages':['regular']}
                for comp in ('eng.1','swe.1')]
        calls=[]
        def fetch(url,headers=None):
            calls.append(url)
            return {'events':[]}
        with tempfile.TemporaryDirectory() as folder:
            with patch('collector.MAX_REQUESTS',1):
                collect(folder,scopes,self.now,fetch,lookback_days=1)
            with patch('collector.MAX_REQUESTS',2):
                collect(folder,scopes,self.now,fetch,lookback_days=1)
            self.assertEqual(len(set(calls)),3)
            calls.clear()
            with patch('collector.MAX_REQUESTS',2):
                collect(folder,scopes,self.now+timedelta(days=1),fetch,lookback_days=0)
            self.assertEqual(len(set(calls)),2)
            self.assertTrue(all('dates=20261031' in url for url in calls))
            state=json.loads((Path(folder)/'scoreboard-attempts.json').read_text())
            self.assertEqual(set(state['attempts']),set(calls))
            self.assertEqual(state['sequence'],5)

    def test_exhausted_wallclock_starts_no_requests(self):
        ticks=[0]
        def clock():
            if ticks: return ticks.pop()
            return 500
        with tempfile.TemporaryDirectory() as folder, patch('collector.time.monotonic',side_effect=clock):
            result=collect(folder,[self.scope],self.now,lambda *a,**k:self.fail('no network'),lookback_days=0)
            self.assertEqual(result['request_count'],0)
            self.assertEqual(result['budget_status'],'TIME_LIMIT_REACHED')
            self.assertFalse(result['coverage_complete'])
            self.assertFalse((Path(folder)/'scoreboard-attempts.json').exists())

    def test_wallclock_cutoff_preserves_already_verified_rows(self):
        calls=[]
        def fetch(url,headers=None):
            calls.append(url)
            return self.summary if '/summary?' in url else {'events':[self.event]}
        # First scoreboard + summary finish; next date and market requests stop.
        with tempfile.TemporaryDirectory() as folder, patch('collector.time.monotonic',side_effect=lambda:500 if len(calls)>=2 else 0):
            result=collect(folder,[self.scope],self.now,fetch,lookback_days=1)
            self.assertEqual(result['request_count'],2)
            self.assertEqual(result['new_result_observations'],1)
            self.assertEqual(result['budget_status'],'TIME_LIMIT_REACHED')
            self.assertEqual(len((Path(folder)/'normalized-results.jsonl').read_text().splitlines()),1)


if __name__=='__main__':unittest.main()
