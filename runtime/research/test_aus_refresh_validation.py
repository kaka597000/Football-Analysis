"""Portable safety checks use synthetic outcomes, never the research holdout."""
import copy
import gzip
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import aus_refresh_validation as aus


class AusRefreshTests(unittest.TestCase):
    def evidence(self, root):
        payload={'header':{'id':'1','league':{'slug':'aus.1'},
            'season':{'name':'2026-27 Australian A-League Men, Regular Season'},
            'competitions':[{'date':'2026-10-30T09:00:00Z','neutralSite':False,
                'status':{'type':{'completed':True,'name':'STATUS_FULL_TIME'}},
                'competitors':[{'homeAway':side,'team':{'id':team},'score':score,
                    'linescores':[{'displayValue':score},{'displayValue':'0'}]}
                    for side,team,score in [('home','11','1'),('away','22','0')]]}]}}
        path=Path(root)/'summary.json.gz';path.write_bytes(gzip.compress(json.dumps(payload).encode()))
        row={'match_id':'espn:1','cohort':'aus.1','competition':'aus.1','stage':'regular',
            'date':'2026-10-30','kickoff_at':'2026-10-30T17:00:00+08:00','home':'espn:11','away':'espn:22',
            'home_goals':1,'away_goals':0,'neutral':False,'status':'FT','score_basis':'regulation_90',
            'source_url':'https://site.api.espn.com/apis/site/v2/sports/soccer/aus.1/summary?event=1',
            'observed_at':'2026-10-30T12:00:00Z','score_evidence':{'source_path':str(path),
            'response_sha256':aus.file_sha(path),'source_key':'espn:1','query_time':'2026-10-30T12:00:00Z'}}
        return row,payload,path

    def test_gzip_evidence_and_utc_normalization(self):
        with tempfile.TemporaryDirectory() as root:
            row,_,_=self.evidence(root)
            checked=aus.validate_new(row,{'range':['2026-04-27','2026-11-09']},root)
            self.assertEqual(checked['date'],'2026-10-30')
            self.assertIsNone(checked['first_known_at'])

    def test_rejects_raw_finals_wrong_identity_and_tamper(self):
        with tempfile.TemporaryDirectory() as root:
            row,payload,path=self.evidence(root);protocol={'range':['2026-04-27','2026-11-09']}
            bad=copy.deepcopy(row);bad['match_id']='espn:2'
            with self.assertRaisesRegex(ValueError,'mapping'):aus.validate_new(bad,protocol,root)
            payload['header']['season']['name']='Australian A-League Men, Finals'
            path.write_bytes(gzip.compress(json.dumps(payload).encode()))
            with self.assertRaisesRegex(ValueError,'hash'):aus.validate_new(row,protocol,root)
            row['score_evidence']['response_sha256']=aus.file_sha(path)
            with self.assertRaisesRegex(ValueError,'regular'):aus.validate_new(row,protocol,root)

    def test_rejects_extra_time_or_unproven_halves(self):
        with tempfile.TemporaryDirectory() as root:
            row,payload,path=self.evidence(root)
            payload['header']['competitions'][0]['competitors'][0]['linescores'].append({'displayValue':'0'})
            path.write_bytes(gzip.compress(json.dumps(payload).encode()))
            row['score_evidence']['response_sha256']=aus.file_sha(path)
            with self.assertRaisesRegex(ValueError,'halves'):
                aus.validate_new(row,{'range':['2026-04-27','2026-11-09']},root)

    def test_split_whole_dates_no_label_dependency(self):
        rows=[{'match_id':str(i),'date':d,'home_goals':i} for i,d in enumerate(
            ['2026-11-03','2026-11-01','2026-11-02','2026-11-02','2026-11-04'])]
        train,holdout,boundary=aus.date_split(rows)
        self.assertEqual(boundary,'2026-11-03');self.assertEqual(len(train),3)
        self.assertEqual(len(holdout),2)
        for r in rows:r['home_goals']=999
        self.assertEqual([r['match_id'] for r in aus.date_split(rows)[1]],['0','4'])
        with self.assertRaisesRegex(ValueError,'unique dates'):aus.date_split(rows[:1])

    def test_predict_safe_fields_cold_start_and_fit_cutoff(self):
        seen=[]
        engine=SimpleNamespace(rates=lambda core,rows:seen.extend(rows) or [[1,1]],matrix=lambda mu:np.ones((2,2))/4,dc_matrix=lambda mu,rho:np.ones((2,2))/4)
        target={'match_id':'1','date':'2026-11-01','home':'h','away':'a','neutral':None,'home_goals':999,'away_goals':999}
        core={'teams':['h','a'],'fit_end':'2026-10-31'}
        aus.predict_matrix(engine,core,'test',target)
        self.assertIsNone(seen[0]['neutral']);self.assertNotIn('home_goals',seen[0])
        with self.assertRaisesRegex(ValueError,'fitted history'):
            aus.predict_matrix(engine,{**core,'fit_end':target['date']},'test',target)
        with self.assertRaisesRegex(ValueError,'COLD_START'):
            aus.predict_matrix(engine,{**core,'teams':['h']},'test',target)
        aus.predict_matrix(engine,{'constant':[1,1],'fit_end':core['fit_end']},'current',target)

    def test_score_axis_and_abstention_denominator(self):
        m=np.array([[.1,.1],[.7,.1]])
        pred=aus.score(m,{'home_goals':1,'away_goals':0})
        self.assertTrue(pred['primary_hit']);self.assertTrue(pred['wdl_hit'])
        np.testing.assert_allclose(pred['wdl_probabilities'],[.7,.2,.1])
        self.assertAlmostEqual(pred['wdl_brier'],.14)
        summary=aus.metrics([{'x':pred},{'x':{'status':'ABSTAIN','reason':'cold start'}}],'x')
        self.assertEqual(summary['scope'],2);self.assertEqual(summary['predicted'],1)
        self.assertEqual(summary['coverage'],.5);self.assertEqual(summary['primary_hits'],1)
        self.assertEqual(summary['actual_market_ou']['n'],0)

    def test_paired_ci_uses_dates_and_common_success(self):
        old=aus.score(np.array([[.8,.1],[.05,.05]]),{'home_goals':1,'away_goals':0})
        new=aus.score(np.array([[.1,.1],[.7,.1]]),{'home_goals':1,'away_goals':0})
        rows=[{'date':'2026-11-01','old':old,'new':new} for _ in range(2)]
        rows.append({'date':'2026-11-02','old':{'status':'ABSTAIN'},'new':new})
        result=aus.paired_bootstrap(rows,'old','new',draws=50)
        self.assertEqual(result['scope'],3);self.assertEqual(result['paired_n'],2)
        self.assertEqual(result['date_blocks'],1);self.assertEqual(result['net_primary_hits'],2)
        self.assertEqual(result['metrics']['primary_hit']['date_block_bootstrap_95'],[1,1])
        self.assertEqual(result,aus.paired_bootstrap(rows,'old','new',draws=50))

    def test_locked_protocol_and_protected_bytes(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'active.json';path.write_text('{}')
            p={'cohort':'aus.1','test_recipe':dict(kind='poisson',half_life=365,ridge=20,rho=0,booster=None),
               'protected_files':{str(path):aus.file_sha(path)}}
            p['protocol_sha256']=aus.digest(p);aus.verify_protocol(p)
            changed=copy.deepcopy(p);changed['test_recipe']['ridge']=5
            with self.assertRaisesRegex(ValueError,'protocol changed'):aus.verify_protocol(changed)
            path.write_text('{"changed":true}')
            with self.assertRaisesRegex(ValueError,'protected'):aus.verify_protocol(p)

    def test_run_locks_plan_and_keeps_final_fit_out_of_holdout(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);out=root/'results';fit_calls=[]
            old=[{'match_id':'old','date':'2026-04-26','home':'h','away':'a','neutral':None,'home_goals':1,'away_goals':0}]
            def fit(rows,half_life,ridge):
                ids=[r['match_id'] for r in rows];fit_calls.append(ids)
                if len(rows)==3:self.assertTrue((out/'evaluation-plan.json').is_file())
                if len(rows)==5:self.assertTrue((out/'holdout-predictions.json').is_file())
                return {'teams':['a','h'],'coefficients':[0],'fit_n':len(rows),'fit_end':rows[-1]['date']}
            engine=SimpleNamespace(fit_core=fit,rates=lambda core,rows:np.ones((len(rows),2)),
                matrix=lambda mu:np.ones((2,2))/4,dc_matrix=lambda mu,rho:np.ones((2,2))/4)
            current={'core':{'constant':[1.,0.],'fit_n':1,'fit_end':'2026-04-26'},'created_at':'2026-10-01T00:00:00Z'}
            test={'core':{'teams':['a','h'],'coefficients':[0],'fit_n':1,'fit_end':'2026-04-26'},
                'created_at':'2026-10-01T00:00:00Z','optimization_code_hash':'synthetic',
                'runner_sha256':aus.file_sha(Path(aus.__file__).resolve().parents[1]/'aus_expert'/'runner.py')}
            aus.write_new(root/'current.json',current);aus.write_new(root/'test.json',test)
            protocol={'cohort':'aus.1','protected_files':{},'current_artifact':str(root/'current.json'),
                'test_artifact':str(root/'test.json'),'test_recipe':dict(kind='poisson',half_life=365,ridge=20,rho=0,booster=None),
                'current_recipe':{'kind':'constant'},'bootstrap':{'draws':20,'seed':20261110}}
            protocol['protocol_sha256']=aus.digest(protocol);aus.write_new(root/'protocol.json',protocol)
            rows=[{**old[0],'match_id':str(i),'date':f'2026-11-0{i}',
                'kickoff_at':f'2026-11-0{i}T08:00:00Z','source_event_key':str(i)} for i in range(1,5)]
            aus.write_new(root/'rows.json',rows)
            with patch.object(aus,'original_engine',return_value=engine),patch.object(aus,'baseline_rows',return_value=old),patch.object(aus,'validate_new',side_effect=lambda row,*_:row):
                summary=aus.run(root/'protocol.json',root/'rows.json',root,out)
            self.assertEqual(fit_calls,[['old'],['old','1','2'],['old','1','2','3','4']])
            self.assertEqual(summary['holdout_n'],2)
            self.assertEqual([r['match_id'] for r in aus.read(out/'holdout-predictions.json')],['3','4'])
            self.assertEqual(aus.read(out/'evaluation-fits.json')['fit']['test']['fit_n'],3)
            self.assertEqual(aus.read(out/'final-test-artifact.json')['core']['fit_n'],5)


if __name__=='__main__':unittest.main()
