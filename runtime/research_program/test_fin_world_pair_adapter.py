"""Synthetic contract tests only; none are evidence of real model readiness."""
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from research_program import fin_world_pair_adapter as fw


class FinWorldTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.now = datetime.now(timezone.utc)

    def tearDown(self): self.tmp.cleanup()

    def source(self, n=1, competition='fifa.world', phase='Group Stage'):
        date = datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(days=n)
        raw = {'header': {'id': str(n), 'league': {'slug': competition},
               'season': {'year': 2025, 'name': 'Synthetic, ' + phase},
               'competitions': [{'id': str(n), 'date': date.isoformat(), 'neutralSite': True,
                  'status': {'type': {'name': 'STATUS_FULL_TIME', 'completed': True}},
                  'competitors': [{'homeAway': side, 'team': {'id': str(i)},
                       'score': str((n+i)%3), 'linescores': [{'value': 0}, {'value': (n+i)%3}]}
                      for i,side in enumerate(('home','away'),1)]}]},
               'format': {'regulation': {'periods': 2}}}
        p = self.root/(str(n)+'.json'); p.write_text(json.dumps(raw))
        return {'raw_path': str(p), 'sha256': fw.sha(p), 'competition': competition,
                'url': f'https://site.api.espn.com/apis/site/v2/sports/soccer/{competition}/summary?event={n}',
                'captured_at': (self.now-timedelta(minutes=2)).isoformat()}

    def mutate(self, meta, f):
        p = Path(meta['raw_path']); raw=json.loads(p.read_text()); f(raw)
        p.write_text(json.dumps(raw));meta['sha256']=fw.sha(p);return meta

    def test_native_group_phase_and_real_half_sum(self):
        row=fw.source_row(self.source());self.assertEqual(row['stage'],'group-stage')
        self.assertEqual(row['home'],'espn:1');self.assertIsNone(row['first_known_at'])

    def test_missing_finland_stage_rejected(self):
        with self.assertRaises(ValueError): fw.source_row(self.source(competition='fin.1',phase='Veikkausliiga'))
        row=fw.source_row(self.source(competition='fin.1',phase='Championship Round'))
        self.assertEqual(row['stage'],'championship-round')

    def test_world_qualification_rejected(self):
        with self.assertRaises(ValueError):fw.source_row(self.source(phase='Qualification'))

    def test_source_hash_and_league_tampering(self):
        m=self.source();Path(m['raw_path']).write_text('{}')
        with self.assertRaises(ValueError):fw.source_row(m)
        m=self.mutate(self.source(),lambda x:x['header']['league'].update(slug='fifa.friendly'))
        with self.assertRaises(ValueError):fw.source_row(m)

    def test_neutral_required_and_no_extra_time_total(self):
        m=self.mutate(self.source(),lambda x:x['header']['competitions'][0].pop('neutralSite'))
        with self.assertRaises(ValueError):fw.source_row(m)
        m=self.mutate(self.source(),lambda x:x['header']['competitions'][0]['competitors'][0].update(score='7'))
        with self.assertRaises(ValueError):fw.source_row(m)

    def test_no_threshold_relaxation_or_mutated_rows(self):
        row=fw.source_row(self.source())
        with self.assertRaises(ValueError):fw.train_pair([row],self.root/'no.json')
        row['home_goals']+=1
        with self.assertRaises(ValueError):fw.train_pair([row],self.root/'no.json')
        self.assertFalse((self.root/'no.json').exists())

    def make_pair(self):
        rows=[fw.source_row(self.source(n)) for n in range(1,161)]
        art=fw.train_pair(rows,self.root/'pair.json',now=self.now-timedelta(seconds=10))
        idx={'schema':fw.INDEX_SCHEMA,'competition':'fifa.world',
             'stages':{'group-stage':art},'created_at':datetime.now(timezone.utc).isoformat()}
        p=self.root/'index.json';p.write_text(json.dumps(idx))
        route={'index_path':str(p),'index_sha256':fw.sha(p),'status':'READY_RESEARCH_PAIR'}
        target=self.now+timedelta(days=1)
        fixture={'cohort':'fifa.world','competition':'fifa.world','stage':'group-stage',
                 'match_id':'synthetic:future','date':target.date().isoformat(),
                 'kickoff_at':target.isoformat(),'cutoff_at':datetime.now(timezone.utc).isoformat(),
                 'phase':'PreMatch','home':'espn:1','away':'espn:2','neutral':True,
                 'score_basis':'regulation_90','season':str(target.year)}
        return art,route,fixture

    def test_complete_pipeline_and_immutable_output(self):
        art,route,fixture=self.make_pair();a=json.loads(Path(art['path']).read_text())
        self.assertEqual(a['evaluation']['fit_n'],140)
        self.assertEqual(a['evaluation']['holdout_n'],20)
        self.assertFalse(a['automatic_promotion'])
        pair=fw.predict_pair(fixture,route)
        for key in ('current_model','test_model'):
            self.assertAlmostEqual(sum(map(sum,pair[key]['matrix'])),1)
            self.assertFalse(pair[key]['formal_kpi_eligible'])
        self.assertTrue(pair['research_only'])
        with self.assertRaises(FileExistsError):
            rows=[fw.source_row(self.source(n)) for n in range(1,161)]
            fw.train_pair(rows,Path(art['path']))

    def test_route_scope_unknown_team_and_clock_fail_closed(self):
        art,route,fixture=self.make_pair()
        for change in ({'stage':'final'},{'home':'espn:999'},{'cohort':'fifa.friendly'},
                       {'phase':'Live'},{'date':'2024-01-01'},{'home_goals':1},
                       {'home-score':1},{'Score90':'1-0'},{'actual_result':'home'}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                fw.predict_pair(dict(fixture,**change),route)
        Path(art['path']).write_text('{}')
        with self.assertRaises(ValueError):fw.predict_pair(fixture,route)

    def test_engine_identity_and_roles_are_bound(self):
        art,route,fixture=self.make_pair()
        p=Path(art['path']); original=json.loads(p.read_text());idxpath=Path(route['index_path'])
        for changed in ({'engine_sha256':{}},{'roles':{'current':'test','test':'current'}},
                        {'automatic_promotion':True}):
            value=copy.deepcopy(original);value.update(changed);p.write_text(json.dumps(value))
            index=json.loads(idxpath.read_text());index['stages']['group-stage']['sha256']=fw.sha(p)
            idxpath.write_text(json.dumps(index));route['index_sha256']=fw.sha(idxpath)
            with self.subTest(changed=changed),self.assertRaises(ValueError):
                fw.predict_pair(fixture,route)

    def test_phase_conflict_and_false_venue_rejected(self):
        meta=self.source();meta['phase']='final'
        with self.assertRaises(ValueError):fw.source_row(meta)
        meta=self.mutate(self.source(),lambda x:x['header']['competitions'][0].update(neutralSite='false'))
        with self.assertRaises(ValueError):fw.source_row(meta)

    def test_index_retains_blocked_stage_without_empty_model(self):
        value=fw.build_index([self.source()],self.root/'blocked','fifa.world')
        self.assertEqual(value['stages'],{})
        self.assertEqual(value['blocked_stages']['group-stage']['verified_rows'],1)
        self.assertEqual(value['status'],'BLOCKED_NO_VERIFIED_PAIR')
        self.assertFalse(list((self.root/'blocked').glob('*pair.json')))

    def test_cutoff_must_follow_index_and_final_fit(self):
        art,route,fixture=self.make_pair()
        index=json.loads(Path(route['index_path']).read_text())
        early=(datetime.fromisoformat(index['created_at'])-timedelta(seconds=1)).isoformat()
        with self.assertRaises(ValueError):fw.predict_pair(dict(fixture,cutoff_at=early),route)
        self.assertGreater(datetime.fromisoformat(json.loads(Path(art['path']).read_text())['created_at']),self.now)

    def test_mid_inference_mutation_rejected(self):
        art,route,fixture=self.make_pair();original=fw._matrix
        def mutate(*args,**kwargs):
            result=original(*args,**kwargs)
            Path(route['index_path']).write_text('{}')
            return result
        with patch.object(fw,'_matrix',side_effect=mutate),self.assertRaises(ValueError):
            fw.predict_pair(fixture,route)

    def test_removed_finland_cannot_build_or_predict(self):
        destination=self.root/'removed-finland'
        with self.assertRaises(ValueError):fw.build_index([],destination,'fin.1')
        self.assertFalse(destination.exists())
        with self.assertRaises(ValueError):
            fw.predict_pair({'competition':'fin.1','cohort':'fin.1'}, {})
        row=fw.source_row(self.source(competition='fin.1',phase='Regular Season'))
        with self.assertRaises(ValueError):fw.train_pair([row],self.root/'fin-pair.json')
        self.assertFalse((self.root/'fin-pair.json').exists())

    def test_fit_mixed_phases_rejected(self):
        rows=[fw.source_row(self.source(n,phase='Group Stage' if n<100 else 'Final')) for n in range(1,161)]
        with self.assertRaises(ValueError):fw.train_pair(rows,self.root/'mixed.json')


if __name__=='__main__':unittest.main()
