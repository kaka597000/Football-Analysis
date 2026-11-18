"""Synthetic result transport tests; never use the production freeze store."""
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError

from runtime.automation import nations_repair_cycle as cycle
from runtime.nations_league import repair_forward as forward
from runtime.research.analysis_freeze import digest


class CycleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.bundle=self.root/'bundle';self.bundle.mkdir();(self.bundle/'freezes').mkdir()
        self.state=self.root/'state';self.now=datetime.now(timezone.utc)
        self.fixtures=[{'match_id':'espn:'+str(i),'cohort':'uefa.nations:A:league-phase',
            'stage':'league-phase','league_level':'A','home':'espn:11','away':'espn:12',
            'kickoff_at':(self.now-timedelta(days=2)).isoformat(),
            'cutoff_at':(self.now-timedelta(days=3)).isoformat()} for i in (1,2)]

    def tearDown(self):self.temp.cleanup()

    def bundle_records(self):
        ref={'sha256':'1'*64}
        plan={'payload':{'tracks':list(forward.TRACKS),'fixtures_identity':self.fixtures,
            'eligible_match_ids':[f['match_id'] for f in self.fixtures],
            'old_routes':{'A':{k:ref for k in ('current','test')}},
            'routes':{'A':{k:ref for k in ('fixed_current','fixed_test','challenger')}}}}
        plan['record_sha256']=digest(plan);(self.bundle/'plan.json').write_text(json.dumps(plan))
        for f in self.fixtures:
            m=[[0. for _ in range(51)] for _ in range(51)];m[2][1]=1.
            predictions={k:{'input':f,'matrix':m,'matrix_hash':digest(m),'primary':'2-1',
                'generated_at':f['cutoff_at'],'version':'SYNTHETIC_'+k,'artifact_sha256':ref['sha256'],
                'wdl_reference':'主胜','total':None} for k in forward.TRACKS}
            r={'match_id':f['match_id'],'fixture':f,'recorded_at':f['cutoff_at'],
               'stage':'prematch','track':forward.TRACK,'revision':1,
               'payload':{'plan_sha256':plan['record_sha256'],'predictions':predictions}}
            r['record_sha256']=digest(r)
            (self.bundle/'freezes'/(f['match_id'].replace(':','-')+'.json')).write_text(json.dumps(r))

    def raw(self,f,*,completed=True):
        mid=f['match_id'].removeprefix('espn:')
        c={'id':mid,'date':f['kickoff_at'],'neutralSite':False,
           'status':{'type':{'name':'STATUS_FULL_TIME' if completed else 'STATUS_IN_PROGRESS',
                             'completed':completed,'state':'post' if completed else 'in','detail':'FT' if completed else '60'}},
           'competitors':[{'homeAway':side,'team':{'id':f[side].removeprefix('espn:')},
                           'score':str(goal),'linescores':[{'period':1,'value':1},{'period':2,'value':goal-1}]}
                          for side,goal in [('home',2),('away',1)]]}
        return json.dumps({'header':{'id':mid,'date':f['kickoff_at'],'league':{'slug':'uefa.nations'},
            'season':{'year':2026,'name':'2026 UEFA Nations League, League A'},'competitions':[c]}}).encode()

    def test_future_never_requests_results(self):
        for f in self.fixtures:f['kickoff_at']=(self.now+timedelta(days=1)).isoformat()
        self.bundle_records()
        def forbidden(url):self.fail('future fixture requested')
        r=cycle.cycle(self.bundle,self.state,fetcher=forbidden)
        self.assertEqual((r['population'],r['settled'],r['result_requests']),(2,0,0))

    def test_pending_retry_is_fair_and_completed_evidence_survives_relocation(self):
        self.bundle_records()
        calls=[]
        def fetch(url):
            mid=url.rsplit('=',1)[1];calls.append(mid)
            return self.raw(next(f for f in self.fixtures if f['match_id']=='espn:'+mid),completed=mid=='2')
        r=cycle.cycle(self.bundle,self.state,fetcher=fetch,max_requests=1)
        self.assertEqual((r['settled'],r['pending']),(0,['espn:1','espn:2']))
        r=cycle.cycle(self.bundle,self.state,fetcher=fetch,max_requests=1)
        self.assertEqual(calls,['1','2']);self.assertEqual(r['settled'],1)
        self.assertEqual(r['comparisons']['old_test_vs_fixed_test']['finished'],1)
        self.assertEqual(r['probability_by_level_and_track']['A']['fixed_test']['n'],1)
        moved=self.root/'relocated';self.state.rename(moved)
        r=cycle.cycle(self.bundle,moved,fetcher=fetch,max_requests=0)
        self.assertEqual(r['settled'],1);self.assertEqual(r['pending'],['espn:1'])

    def test_mismatched_kickoff_or_raw_periods_cannot_settle(self):
        self.bundle_records();f=self.fixtures[0]
        bad=copy.deepcopy(f);bad['kickoff_at']=(self.now-timedelta(days=4)).isoformat()
        path=self.root/'raw.json';path.write_bytes(self.raw(bad))
        with self.assertRaises(ValueError):cycle.result_from_raw(f,path,cycle.ENDPOINT+'1',self.now.isoformat())
        raw=json.loads(self.raw(f));raw['header']['competitions'][0]['competitors'][0]['linescores']=[]
        path.write_text(json.dumps(raw))
        with self.assertRaises(ValueError):cycle.result_from_raw(f,path,cycle.ENDPOINT+'1',self.now.isoformat())

    def test_completed_review_is_reused_only_for_the_same_verified_evidence(self):
        self.bundle_records()
        by_id = {f['match_id'].split(':')[1]: f for f in self.fixtures}
        def fetch(url):
            return self.raw(by_id[url.rsplit('=', 1)[1]])
        first = cycle.cycle(self.bundle, self.state, fetcher=fetch)
        self.assertEqual((first['settled'], first['result_requests'], first['review_publication']),
                         (2, 2, 'RECORDED'))
        def stored_files():
            return {p.relative_to(self.state): p.read_bytes()
                    for folder in ('runs', 'freezes')
                    for p in (self.state / folder).rglob('*.json')}
        before = stored_files()
        self.assertEqual(len(before), 2)
        again = cycle.cycle(self.bundle, self.state, max_requests=0)
        self.assertEqual(again['review_publication'], 'REUSED_COMPLETED_REVIEW')
        self.assertEqual(again['review_freeze_id'], first['review_freeze_id'])
        self.assertEqual(again['result_requests'], 0)
        self.assertEqual(stored_files(), before)

        result_path = self.state / 'results.json'
        results = json.loads(result_path.read_text())
        raw = self.state / results[0]['source_path']
        raw.write_bytes(raw.read_bytes() + b'\n')
        with self.assertRaises(ValueError):
            cycle.cycle(self.bundle, self.state, max_requests=0)
        self.assertEqual(stored_files(), before)
        results[0]['source_sha256'] = cycle.repair.sha(raw)
        result_path.write_text(json.dumps(results))
        revised = cycle.cycle(self.bundle, self.state, max_requests=0)
        self.assertNotEqual(revised['review_freeze_id'], first['review_freeze_id'])
        self.assertEqual(revised['review_publication'], 'RECORDED')
        after = stored_files()
        self.assertEqual(len(after), 4)
        self.assertTrue(all(after[p] == value for p, value in before.items()))

    def test_rate_limit_stops_batch_and_keeps_population(self):
        self.bundle_records()
        def limited(url):raise HTTPError(url,429,'synthetic',{},None)
        r=cycle.cycle(self.bundle,self.state,fetcher=limited)
        self.assertEqual(r['result_requests'],1);self.assertEqual(len(r['pending']),2)
        self.assertEqual(len(r['failures']),1)


if __name__=='__main__':unittest.main()
