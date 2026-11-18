from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

try:
    from data_sources import research_supplement as monitor
except ImportError:
    from runtime.data_sources import research_supplement as monitor


KICK = datetime(2026,11,18,22,30,tzinfo=timezone.utc)


class CoverageTests(unittest.TestCase):
    def test_window_survives_kickoff_and_does_not_backfill_from_later_matches(self):
        seen = KICK-timedelta(minutes=54)
        confirmed = {'status':'ok','observed_at':seen.isoformat(),'facts':{'both_elevens':True}}
        early = monitor.lineup_window(KICK,KICK-timedelta(hours=2),{})
        self.assertEqual(early['state'],'pending')
        window = monitor.lineup_window(KICK,seen,confirmed,early)
        self.assertEqual(window['state'],'confirmed')
        self.assertEqual(monitor.lineup_window(KICK,KICK+timedelta(minutes=20),{},window),window)
        other = dict(confirmed,observed_at=(KICK+timedelta(seconds=1)).isoformat())
        self.assertEqual(monitor.lineup_window(KICK,KICK+timedelta(seconds=3),other)['state'],'window_not_sampled')

    def test_missing_xi_window_distinguishes_http_reason_from_empty_lineup(self):
        stamp = KICK-timedelta(minutes=40)
        window = monitor.lineup_window(KICK,stamp,{'status':'missing','attempted':True,'observed_at':stamp.isoformat(),'reason':'HTTP 403'})
        self.assertEqual(window['state'],'provider_unavailable')
        self.assertEqual(monitor.lineup_window(KICK,KICK,{},window)['last_failure']['reason'],'HTTP 403')
        observed={'status':'ok','observed_at':stamp.isoformat(),'facts':{'both_elevens':False}}
        self.assertEqual(monitor.lineup_window(KICK,KICK,observed)['state'],'queried_not_published')
        self.assertEqual(monitor.lineup_window(KICK,stamp,{})['state'],'awaiting_observation')

    def test_bsd_historical_join_requires_timing_order_and_ft_and_preserves_estimates(self):
        fixture={'match_id':'espn:1','home':'espn:9318','away':'espn:3454','kickoff_at':KICK.isoformat(),'home_goals':0,'away_goals':2}
        event=dict(id=55,league_id=9,stage='regular-season',status='finished',event_date=KICK.isoformat(),
                   home_team='Chapecoense',away_team='Vasco da Gama',home_score=0,away_score=2)
        self.assertEqual(monitor.bsd_event([event],fixture),55)
        with self.assertRaises(ValueError):
            monitor.bsd_event([dict(event,home_team='Vasco da Gama')],fixture)
        with self.assertRaises(ValueError):
            monitor.bsd_event([dict(event,status='live')],fixture)
        stats={s:{'expected_goals':0.44,'xg':{'actual':0.49,'estimated':False}} for s in monitor.SIDES}
        value=monitor.xg_values({'event_id':55,'stats':stats,'xg_estimated':True},55)
        self.assertEqual(value['teams']['home']['xg_actual'],0.49)
        self.assertEqual(value['teams']['home']['expected_goals'],0.44)
        self.assertFalse(monitor.has_real_xg({'status':'ok','facts':value}))

    def test_response_that_arrives_at_or_after_kickoff_cannot_count_as_prematch(self):
        before = {'status':'ok','observed_at':(KICK-timedelta(microseconds=1)).isoformat(),'facts':{'shots':3}}
        self.assertEqual(monitor.prematch_observation(before,KICK),before)
        for stamp in (KICK,KICK+timedelta(seconds=4)):
            late = dict(before,observed_at=stamp.isoformat(),source_sha256='raw-reference')
            missing = monitor.prematch_observation(late,KICK)
            self.assertEqual(missing['status'],'missing')
            self.assertEqual(missing['source_sha256'],'raw-reference')
            self.assertNotIn('facts',missing)

    def test_non_api_file_date_and_teams_source_match(self):
        text = b'  Wed Nov 18\n           A Club    v B Club \n   20:00   Home Team v Away Team   0-0 (0-0)\n'
        self.assertEqual(monitor.open_rows(text),[['2026-11-18','A Club','B Club'],['2026-11-18','Home Team','Away Team']])

    def test_cache_reuses_real_time_and_a_block_stops_provider_calls(self):
        with tempfile.TemporaryDirectory() as d, patch.object(monitor.time,'sleep'):
            s=monitor.Source(Path(d),'public')
            calls=[]
            def ok():
                calls.append(True)
                return b'a',monitor.sha(b'a')
            r=s.get('https://test.example/one',ok,bytes.decode)
            self.assertEqual(r,s.get('https://test.example/one',ok,bytes.decode))
            self.assertEqual(len(calls),1)
            def reject():
                raise HTTPError('https://test.example',403,'denied',None,None)
            missing=s.get('https://test.example/locked',reject,bytes.decode)
            self.assertEqual(missing['reason'],'403')
            self.assertFalse(s.get('https://test.example/two',ok,bytes.decode)['attempted'])
            self.assertEqual(len(calls),1)

    def test_retries_transient_connection_once(self):
        with tempfile.TemporaryDirectory() as d, patch.object(monitor.time,'sleep'):
            s=monitor.Source(Path(d),'public')
            def fail():
                raise URLError('temporarily unavailable')
            missing=s.get('https://test.example',fail,str)
            self.assertEqual(len(s.calls),2)
            self.assertEqual(missing['status'],'missing')

    def test_espn_lineup_requires_two_explicit_elevens_and_no_live_event(self):
        f={'match_id':'espn:123','home':'espn:9','away':'espn:7','kickoff_at':KICK.isoformat()}
        teams=[{'team':{'id':f[s][5:]},'homeAway':s} for s in monitor.SIDES]
        entry={'date':KICK.isoformat(),'status':{'type':{'state':'pre','completed':False}},'competitors':teams}
        doc={'header':{'id':'123','league':{'slug':'bra.1'},'competitions':[entry]},
             'rosters':[dict(t,roster=[{'starter':True,'athlete':{'id':t['team']['id']+str(i)}} for i in range(11)]) for t in teams]}
        self.assertTrue(monitor.espn_summary(json.dumps(doc).encode(),f,False)['both_elevens'])
        doc['rosters'][0]['roster'][1] = doc['rosters'][0]['roster'][0]
        self.assertFalse(monitor.espn_summary(json.dumps(doc).encode(),f,False)['both_elevens'])
        entry['status']['type']['state']='in'
        with self.assertRaises(ValueError):
            monitor.espn_summary(json.dumps(doc).encode(),f,False)


if __name__=='__main__':
    unittest.main()
