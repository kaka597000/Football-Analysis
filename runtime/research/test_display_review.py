import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from research import display_review as d
from research.analysis_freeze import save, digest
from automation.collector import collect

class FrozenClock(datetime):
    @classmethod
    def now(cls,tz=None):return cls(2026,1,1,12,tzinfo=timezone.utc).astimezone(tz) if tz else cls(2026,1,1,12)

class SharedDisplayTests(unittest.TestCase):
    def fixture(self,league='bra.1',provider='espn'):
        return {'match_id':provider+':101','cohort':league,'stage':'regular','home':provider+':1','away':provider+':2','kickoff_at':'2026-01-02T12:00:00Z','cutoff_at':'2026-01-01T11:00:00Z','provider_competition':'PL','provider_stage':'REGULAR_SEASON'}
    def freeze(self,root,f,first='1-0',alts=('1-1','2-0'),track='display:comprehensive',abstain=False):
        payload,text=d.attach_display({'synthetic_test_only':True},None if abstain else first,() if abstain else alts,abstain=abstain)
        with patch('research.analysis_freeze.datetime',FrozenClock):ref=save(payload,f,stage='prematch',track=track,root=root)
        return json.loads(Path(ref['path']).read_text())
    def raw(self,f,h,a,provider='espn'):
        if provider=='football-data.org':return {'id':101,'homeTeam':{'id':1},'awayTeam':{'id':2},'competition':{'code':'PL'},'stage':'REGULAR_SEASON','utcDate':f['kickoff_at'],'status':'FINISHED','score':{'duration':'REGULAR','fullTime':{'home':h,'away':a}}}
        c={'date':f['kickoff_at'],'status':{'type':{'completed':True,'name':'STATUS_FULL_TIME'},'period':2},'competitors':[{'homeAway':s,'team':{'id':str(i)},'score':str(g),'linescores':[{'value':g},{'value':0}]} for s,i,g in [('home',1,h),('away',2,a)]]}
        return {'header':{'id':'101','league':{'slug':f['cohort']},'season':{'year':2026,'name':'Regular Season'},'competitions':[c]},'format':{'regulation':{'periods':2}}}
    def meta(self,root,f,raw,provider='espn'):
        p=Path(root)/'result.json';p.write_text(json.dumps(raw));return {'path':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'captured_at':'2026-01-03T12:00:00Z','source_url':('https://api.football-data.org/v4/matches/101' if provider=='football-data.org' else f"https://site.api.espn.com/apis/site/v2/sports/soccer/{f['cohort']}/summary?event=101")}
    def test_primary_hit_is_also_top3_and_alternate_only(self):
        for h,a,top1,top3 in [(1,0,True,True),(1,1,False,True),(4,0,False,False)]:
            with self.subTest(score=(h,a)),tempfile.TemporaryDirectory() as root:
                f=self.fixture();r=self.freeze(root,f);meta=self.meta(root,f,self.raw(f,h,a));v=d.settle(r,f,'display:comprehensive','espn',meta,root)
                self.assertEqual((v['top1_hit'],v['top3_hit']),(top1,top3))
    def test_empty_incomplete_duplicate_and_abstention(self):
        for first,alts in [(None,()),('1-0',('1-1',)),('1-0',('1-0','2-0')),('1-0',('x','2-0'))]:
            with self.assertRaises(ValueError):d.attach_display({},first,alts)
        with tempfile.TemporaryDirectory() as root:
            f=self.fixture();r=self.freeze(root,f,abstain=True);v=d.settle(r,f,'display:comprehensive');s=d.summarize([v]);self.assertEqual(s['abstain_n'],1);self.assertIsNone(s['top3']['accuracy'])
    def test_no_backfill_no_cross_track_no_matrix_borrowing(self):
        with tempfile.TemporaryDirectory() as root:
            f=self.fixture();r=self.freeze(root,f)
            with self.assertRaises(ValueError):d.settle(r,f,'other-track')
            with self.assertRaises(ValueError):d.settle(r,dict(f,provider_competition='OTHER'), 'display:comprehensive')
            for field,value in [('recorded_at','2026-01-03T00:00:00Z'),('revision',2)]:
                bad=copy.deepcopy(r);bad[field]=value;bad['record_sha256']=digest({k:v for k,v in bad.items() if k!='record_sha256'})
                with self.assertRaises(ValueError):d.settle(bad,f,'display:comprehensive')
            old=copy.deepcopy(r);old['payload']={'primary':'1-0','compared_scores':['1-0','1-1','2-0'],'matrix_top3':['1-0','1-1','2-0']};old['record_sha256']=digest({k:v for k,v in old.items() if k!='record_sha256'})
            self.assertFalse(d.settle(old,f,'display:comprehensive')['top3_issued'])
    def test_incomplete_legacy_display_separates_denominators(self):
        with tempfile.TemporaryDirectory() as root:
            f=self.fixture();r=self.freeze(root,f);r['payload']['displayed_scores']['alternatives']=['1-1'];r['record_sha256']=digest({k:v for k,v in r.items() if k!='record_sha256'})
            m=self.meta(root,f,self.raw(f,1,0));s=d.summarize([d.settle(r,f,'display:comprehensive','espn',m,root)])
            self.assertEqual(s['top1']['settled_n'],1);self.assertEqual(s['top3']['settled_n'],0)
    def test_three_competitions_two_providers_shared_settlement(self):
        for league,provider,ns in [('bra.1','espn','espn'),('usa.1','espn','espn'),('eng.1','football-data.org','fdorg')]:
            with self.subTest(league=league),tempfile.TemporaryDirectory() as root:
                f=self.fixture(league,ns);r=self.freeze(root,f);m=self.meta(root,f,self.raw(f,1,1,provider),provider);s=d.summarize([d.settle(r,f,'display:comprehensive',provider,m,root)])
                self.assertEqual(s['top1']['hits'],0);self.assertEqual(s['top3']['hits'],1)
    def test_shared_end_to_end_collection_no_markets_and_idempotent(self):
        with tempfile.TemporaryDirectory() as root:
            f=self.fixture('usa.1');r=self.freeze(root,f);raw=self.raw(f,1,1);event={**raw['header'],'date':f['kickoff_at']};calls=[]
            def fetch(url,headers=None):
                calls.append(url)
                self.assertIn('site.api.espn.com',url)
                return raw if '/summary?' in url else {'leagues':[{'slug':f['cohort']}],'events':[event]}
            scopes=[{'competition':'usa.1','cohort':'usa.1','stages':['regular']}]
            first=collect(root,scopes,now=datetime(2026,1,3,tzinfo=timezone.utc),lookback_days=0,fetcher=fetch,include_market=False)
            self.assertEqual(len(first['new_rows']),1);meta=first['new_rows'][0]['raw_reference'];self.assertIn('scoreboard_reference',meta);s=d.summarize([d.settle(r,f,'display:comprehensive','espn',meta,root)])
            self.assertEqual(s['top3']['hits'],1)
            before=(Path(root)/'normalized-results.jsonl').read_bytes();second=collect(root,scopes,now=datetime(2026,1,3,tzinfo=timezone.utc),lookback_days=0,fetcher=fetch,include_market=False)
            self.assertEqual(len(second['new_rows']),0);self.assertEqual(before,(Path(root)/'normalized-results.jsonl').read_bytes());self.assertFalse(first['market_requested'])
    def test_results_only_never_enters_training(self):
        from automation.run import execute
        with self.assertRaises(ValueError):execute('/tmp/not-used','train',results_only=True)

    def test_wrong_source_extra_time_late_data_and_tampering(self):
        with tempfile.TemporaryDirectory() as root:
            f=self.fixture('eng.1','fdorg');raw=self.raw(f,1,0,'football-data.org');m=self.meta(root,f,raw,'football-data.org');raw['score']['duration']='EXTRA_TIME';m=self.meta(root,f,raw,'football-data.org')
            with self.assertRaises(ValueError):d.certify_retained('football-data.org',f,m,root)
            m['sha256']='0'*64
            with self.assertRaises(ValueError):d.certify_retained('football-data.org',f,m,root)
            f=self.fixture('usa.1');raw=self.raw(f,1,0);raw['header']['season']['name']='Playoffs';m=self.meta(root,f,raw)
            with self.assertRaises(ValueError):d.certify_retained('espn',f,m,root)
            f=self.fixture();r=self.freeze(root,f);r['payload']['displayed_scores']['first']='9-9'
            with self.assertRaises(ValueError):d.settle(r,f,'display:comprehensive')

    def test_finished_extra_time_and_shootout_use_explicit_regular_time(self):
        for duration in ('EXTRA_TIME','PENALTY_SHOOTOUT'):
            for keys in [('home','away'),('homeTeam','awayTeam')]:
                with self.subTest(duration=duration,keys=keys),tempfile.TemporaryDirectory() as root:
                    f=self.fixture('eng.1','fdorg');raw=self.raw(f,7,6,'football-data.org')
                    raw['score'].update(duration=duration,regularTime=dict(zip(keys,[1,1])),extraTime={'home':1,'away':0},penalties={'home':5,'away':5})
                    m=self.meta(root,f,raw,'football-data.org');r=d.certify_retained('football-data.org',f,m,root)
                    self.assertEqual((r['home_goals'],r['away_goals']),(1,1))

    def test_regular_time_missing_invalid_conflicting_or_unfinished_rejected(self):
        cases=[None,{}, {'home':None,'away':1},{'home':True,'away':1},{'home':-1,'away':1},{'home':1,'away':1,'homeTeam':2,'awayTeam':1}]
        for value in cases:
            with self.subTest(value=value),tempfile.TemporaryDirectory() as root:
                f=self.fixture('eng.1','fdorg');raw=self.raw(f,7,6,'football-data.org');raw['score'].update(duration='PENALTY_SHOOTOUT',regularTime=value);m=self.meta(root,f,raw,'football-data.org')
                with self.assertRaises(ValueError):d.certify_retained('football-data.org',f,m,root)
        with tempfile.TemporaryDirectory() as root:
            f=self.fixture('eng.1','fdorg');raw=self.raw(f,1,1,'football-data.org');raw['score']['regularTime']={'home':None,'away':None};m=self.meta(root,f,raw,'football-data.org')
            self.assertEqual(d.certify_retained('football-data.org',f,m,root)['home_goals'],1)
            raw['score']['regularTime']={'home':1,'away':0};m=self.meta(root,f,raw,'football-data.org')
            with self.assertRaises(ValueError):d.certify_retained('football-data.org',f,m,root)
            raw['score'].update(duration='EXTRA_TIME',regularTime={'home':1,'away':1});raw['status']='IN_PLAY';m=self.meta(root,f,raw,'football-data.org')
            with self.assertRaises(ValueError):d.certify_retained('football-data.org',f,m,root)
            raw['status']='FINISHED';raw['homeTeam']['id']=999;m=self.meta(root,f,raw,'football-data.org')
            with self.assertRaises(ValueError):d.certify_retained('football-data.org',f,m,root)

    def board_meta(self,root,f,raw,stage):
        event=copy.deepcopy(raw['header']);event['date']=f['kickoff_at'];event['season']['slug']=stage
        board={'leagues':[{'slug':f['cohort']}],'events':[event]}
        p=Path(root)/'scoreboard.json';p.write_text(json.dumps(board))
        meta={'path':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'captured_at':'2026-01-03T11:00:00Z','source_url':f"https://site.api.espn.com/apis/site/v2/sports/soccer/{f['cohort']}/scoreboard?dates=20260102"}
        return board,meta

    def test_paired_native_stage_survives_missing_summary_slug(self):
        for league,stage in [('uefa.europa','league-phase'),('uefa.europa.conf','league-phase'),('fifa.world','group-stage')]:
            with self.subTest(league=league),tempfile.TemporaryDirectory() as root:
                f=dict(self.fixture(league),stage=stage);raw=self.raw(f,1,1);m=self.meta(root,f,raw)
                with self.assertRaises(ValueError):d.certify_retained('espn',f,m,root)
                board,bm=self.board_meta(root,f,raw,stage);m['scoreboard_reference']=bm
                self.assertEqual(d.certify_retained('espn',f,m,root)['home_goals'],1)

    def test_paired_stage_missing_conflict_wrong_fixture_and_root_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            f=dict(self.fixture('uefa.europa'),stage='league-phase');raw=self.raw(f,1,1);m=self.meta(root,f,raw);board,bm=self.board_meta(root,f,raw,'league-phase');m['scoreboard_reference']=bm
            def check_bad(changed):
                p=Path(root)/'scoreboard.json';p.write_text(json.dumps(changed));bm['sha256']=hashlib.sha256(p.read_bytes()).hexdigest()
                with self.assertRaises(ValueError):d.certify_retained('espn',f,m,root)
            bad=copy.deepcopy(board);del bad['events'][0]['season']['slug'];check_bad(bad)
            bad=copy.deepcopy(board);bad['events'][0]['season']['slug']='final';check_bad(bad)
            bad=copy.deepcopy(board);bad['leagues'][0]['slug']='fifa.world';check_bad(bad)
            bad=copy.deepcopy(board);bad['events'][0]['id']='999';check_bad(bad)
            bad=copy.deepcopy(board);bad['events'][0]['competitions'][0]['competitors'][0]['team']['id']='999';check_bad(bad)
            bad=copy.deepcopy(board);bad['events'][0]['date']='2026-01-02T14:00:00Z';check_bad(bad)
            bad=copy.deepcopy(board);bad['events']*=2;check_bad(bad)
            board,bm=self.board_meta(root,f,raw,'league-phase');m['scoreboard_reference']=bm;raw['header']['season']['slug']='final';m.update(self.meta(root,f,raw))
            with self.assertRaises(ValueError):d.certify_retained('espn',f,m,root)
            bm['path']='../outside.json'
            with self.assertRaisesRegex(ValueError,'outside retained root'):d.certify_retained('espn',f,m,root)

if __name__=='__main__':unittest.main()
