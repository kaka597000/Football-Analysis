"""Synthetic contract tests, never prediction/freeze evidence."""
import copy
import json
import tempfile
import unittest
from unittest.mock import patch, Mock
from pathlib import Path
from datetime import datetime, timezone, timedelta
from europe_pair_adapter import predict_pair, sha, source_hashes
from continental.build_europe_pairs import verified_rows, build


class PairTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        (self.root/'verified-rows.json').write_text('[]')
        self.index={'competition':'uefa.europa','created_at':'2000-01-01T00:00:00+00:00','fit_end':'2000-01-01',
                    'teams':['espn:1','espn:2'],'source_rows_sha256':'test','source_files_sha256':source_hashes(),
                    'rows_file_sha256':sha(self.root/'verified-rows.json')}
        for role in ('current','test'):
            core={'constant':[1.4,1.1]} if role=='current' else {'teams':['espn:1','espn:2'],'coefficients':[0.1,0.1,0,0,0,0,0]}
            p=self.root/(role+'.json');p.write_text(json.dumps({'schema':'WCPP-EUROPE-PAIR-ARTIFACT-1','role':role,'version':'SYNTHETIC_TEST','competition':'uefa.europa','fit_end':'2000-01-01','core':core}))
            self.index[role]={'file':p.name,'sha256':sha(p)}
        self.save_index()
        now=datetime.now(timezone.utc);kickoff=now+timedelta(days=1)
        self.fixture={'match_id':'synthetic','cohort':'uefa.europa','competition':'uefa.europa','stage':'league-phase',
                      'phase':'PreMatch','gender':'men','age':'senior','score_basis':'regulation_90','neutral':False,
                      'home':'espn:1','away':'espn:2','date':kickoff.date().isoformat(),
                      'kickoff_at':kickoff.isoformat(),'cutoff_at':now.isoformat()}

    def save_index(self):
        p=self.root/'index.json';p.write_text(json.dumps(self.index));self.route={'index_path':str(p),'index_sha256':sha(p)}

    def test_pair_is_real_normalized_and_separate(self):
        r=predict_pair(self.fixture,self.route)
        for role in ('current_model','test_model'):
            self.assertAlmostEqual(sum(map(sum,r[role]['matrix'])),1)
            self.assertFalse(r[role]['automatic_promotion'])
        self.assertNotEqual(r['current_model']['matrix'],r['test_model']['matrix'])

    def test_reject_identity_scope_and_venue(self):
        for key,value in [('competition','uefa.europa.conf'),('cohort','eng.1'),('stage','final'),('gender','women'),('age','u21'),('score_basis','after_extra_time'),('home','espn:unknown'),('away','espn:1'),('neutral',None),('neutral',0)]:
            with self.subTest(key=key),self.assertRaises(ValueError):predict_pair(dict(self.fixture,**{key:value}),self.route)

    def test_reject_future_cutoff_and_postmatch(self):
        for cutoff in [self.fixture['kickoff_at'],'1999-01-01T00:00:00Z','2020-01-01T00:00:00']:
            with self.assertRaises(ValueError):predict_pair(dict(self.fixture,cutoff_at=cutoff),self.route)

    def test_prematch_phase_required(self):
        for phase in ('Live', 'PostMatch', None):
            with self.subTest(phase=phase), self.assertRaises(ValueError):
                predict_pair(dict(self.fixture,phase=phase),self.route)
        missing=dict(self.fixture);missing.pop('phase')
        with self.assertRaises(ValueError):predict_pair(missing,self.route)

    def test_inputs_changed_during_computation_rejected(self):
        import numpy as np
        for name in ('index.json','current.json','test.json','verified-rows.json'):
            with self.subTest(name=name):
                target=self.root/name;before=target.read_bytes();engine=Mock()
                engine.rates.return_value=np.asarray([[1.,1.]])
                def mutate(_):
                    target.write_bytes(before+b' ')
                    return np.asarray([[1.]])
                engine.matrix.side_effect=mutate
                try:
                    with patch('europe_pair_adapter._engine',return_value=engine),self.assertRaises(ValueError):
                        predict_pair(self.fixture,self.route)
                finally:target.write_bytes(before)

    def test_source_changed_during_computation_rejected(self):
        with patch('europe_pair_adapter.source_hashes',side_effect=[self.index['source_files_sha256'],{}]), \
             self.assertRaises(ValueError):
            predict_pair(self.fixture,self.route)

    def test_clock_expires_during_computation_rejected(self):
        now=datetime.now(timezone.utc);after=now+timedelta(days=2)
        with patch('europe_pair_adapter.datetime') as clock:
            clock.fromisoformat.side_effect=datetime.fromisoformat
            clock.now.side_effect=[now,after]
            with self.assertRaises(ValueError):predict_pair(self.fixture,self.route)

    def test_target_labels_rejected(self):
        with self.assertRaises(ValueError):
            predict_pair(dict(self.fixture,home_goals=1),self.route)

    def test_engine_source_identity(self):
        self.index['source_files_sha256']={};self.save_index()
        with self.assertRaises(ValueError):predict_pair(self.fixture,self.route)

    def test_role_swap_rejected(self):
        self.index['current'],self.index['test']=self.index['test'],self.index['current'];self.save_index()
        with self.assertRaises(ValueError):predict_pair(self.fixture,self.route)

    def test_artifact_hash(self):
        (self.root/'test.json').write_text('{}')
        with self.assertRaises(ValueError):predict_pair(self.fixture,self.route)

    def test_index_hash(self):
        (self.root/'index.json').write_text('{}')
        with self.assertRaises(ValueError):predict_pair(self.fixture,self.route)

    def test_rows_hash(self):
        (self.root/'verified-rows.json').write_text('[1]')
        with self.assertRaises(ValueError):predict_pair(self.fixture,self.route)

    def test_source_without_raw_does_not_train(self):
        rows=[{'competition':'uefa.europa','stage':'league-phase','score_basis':'regulation_90','neutral':False}]
        accepted,rejected=verified_rows(rows,self.root,'uefa.europa')
        self.assertEqual(accepted,[]);self.assertEqual(rejected,{'NO_RAW':1})

    def test_native_venue_is_required_not_normalized_guess(self):
        raw=self.root/'raw.json'
        row={'competition':'uefa.europa','stage':'league-phase','score_basis':'regulation_90',
             'neutral':False,'raw_reference':{'path':'raw.json'}}
        for native in ({}, {'neutralSite':None}, {'neutralSite':0}, {'neutralSite':True}):
            raw.write_text(json.dumps({'header':{'season':{},'competitions':[native]}}))
            with patch('continental.build_europe_pairs._verify_raw_result',return_value='VERIFIED_RAW_REGULATION_90'):
                accepted,rejected=verified_rows([row],self.root,'uefa.europa')
            self.assertEqual(accepted,[]);self.assertEqual(rejected,{'NATIVE_VENUE':1})

    def test_failed_fit_never_marks_ready(self):
        start=datetime(2020,1,1)
        rows=[{'match_id':str(i),'date':(start+timedelta(days=i)).date().isoformat(),
               'home':'espn:1','away':'espn:2','home_goals':1,'away_goals':0} for i in range(160)]
        engine=Mock();engine.fit_core.side_effect=ValueError('failed convergence')
        with patch('continental.build_europe_pairs.verified_rows',return_value=(rows,{})), \
             patch('continental.build_europe_pairs._engine',return_value=engine), \
             patch('continental.build_europe_pairs.metrics',return_value={}), \
             self.assertRaises(ValueError):
            build(rows,self.root,self.root/'failed','uefa.europa')
        self.assertFalse((self.root/'failed/index.json').exists())


if __name__=='__main__':unittest.main()
