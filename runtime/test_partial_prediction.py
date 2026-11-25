"""Real adapters with synthetic artifacts: partial delivery, never performance."""
import copy
from datetime import datetime,timedelta,timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from prediction_engine import predict_models
from prediction_service import run
from active_route_engines import predict_generic_poisson
from publication import publish
from research.analysis_freeze import verify

ROOT=Path(__file__).resolve().parent


class PartialPredictionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);now=datetime.now(timezone.utc);kickoff=now+timedelta(days=1)
        self.fixture={'match_id':'SYNTHETIC_中文_PARTIAL','cohort':'aus.1','competition':'aus.1',
                      'gender':'male','age':'senior','stage':'regular','phase':'PreMatch','identity_verified':True,
                      'date':kickoff.date().isoformat(),'home':'A','away':'B','neutral':False,
                      'cutoff_at':(now-timedelta(minutes=1)).isoformat(),'kickoff_at':kickoff.isoformat()}
        manifest=self.root/'dataset.json';manifest.write_text('{}',encoding='utf-8')
        route={'current_engine':'generic_poisson','test_engine':'generic_poisson','test_version':'SYNTHETIC_TEST'}
        for prefix,role in [('', 'current'),('test_','test')]:
            artifact={'version':'SYNTHETIC_'+role,'role':role,'engine_contract':'WCPP-GENERIC-POISSON-ROUTE-1',
                      'model_kind':'constant','core':{'constant':[1.4,1.], 'fit_n':100,'fit_end':'2025-01-01'},
                      'created_at':(now-timedelta(days=2)).isoformat(),
                      'forecast_scope':{'cohort':'aus.1','competition':'aus.1','stages':['regular']},
                      'dataset_manifest':str(manifest),'dataset_manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest()}
            p=self.root/(role+'.json');p.write_text(json.dumps(artifact),encoding='utf-8')
            route[prefix+'artifact_path']=str(p);route[prefix+'artifact_sha256']=hashlib.sha256(p.read_bytes()).hexdigest()
        self.route=route;self.registry=self.root/'registry.json';self.write_registry()

    def write_registry(self):
        self.registry.write_text(json.dumps({'version':'SYNTHETIC','status':'ACTIVE_RESEARCH','models':{'aus.1':self.route}}),encoding='utf-8')

    def break_test(self):
        (self.root/'test.json').write_text('CORRUPTED',encoding='utf-8')

    def test_default_still_fails_on_bad_test_artifact(self):
        self.break_test()
        with self.assertRaisesRegex(ValueError,'checksum'):
            predict_models(self.fixture,self.registry)

    def test_opt_in_preserves_current_exactly_and_no_fake_test(self):
        original=copy.deepcopy(self.fixture);before=(self.root/'current.json').read_bytes()
        expected=predict_generic_poisson(self.root/'current.json',self.fixture)
        self.break_test();result=run(self.fixture,self.registry,retain_current_on_test_failure=True)
        np.testing.assert_array_equal(expected['matrix'],result['current_model']['matrix'])
        self.assertEqual(expected['matrix_hash'],result['current_model']['matrix_hash'])
        self.assertEqual(result['prediction_status'],'PARTIAL_CURRENT_ONLY')
        self.assertIsNone(result['test_model']['matrix']);self.assertIsNone(result['test_model']['primary'])
        self.assertEqual(self.fixture,original);self.assertEqual((self.root/'current.json').read_bytes(),before)
        audit=result['execution_audit']['test']
        self.assertFalse(audit['engine_attempted']);self.assertIsNone(audit['engine'])
        self.assertFalse(audit['execution_failure']['artifact_checksum_verified'])

    def test_partial_freeze_resume_uses_same_first_record(self):
        self.break_test();output=run(self.fixture,self.registry,retain_current_on_test_failure=True)
        dest=self.root/'结果.json';freezes=self.root/'freeze'
        with patch('publication.deliver',side_effect=OSError('synthetic interrupted delivery')):
            with self.assertRaises(OSError):publish(output,self.fixture,out=dest,freeze_root=freezes)
        first=publish(None,None,out=dest,freeze_root=freezes,resume=True)
        again=publish(None,None,out=dest,freeze_root=freezes,resume=True)
        self.assertEqual(first['freeze_receipt'],again['freeze_receipt'])
        paths=list(freezes.glob('*/*.json'));self.assertEqual(len(paths),1)
        record=verify(json.loads(paths[0].read_text(encoding='utf-8')))
        self.assertEqual(record['evaluation_slot'],'FIRST_PREMATCH')
        self.assertEqual(record['payload']['test_model']['status'],'ABSTAIN_TEST_EXECUTION_FAILED')
        self.assertEqual(record['payload']['current_model']['matrix_hash'],output['current_model']['matrix_hash'])

    def test_missing_test_file_preserves_current(self):
        (self.root/'test.json').unlink()
        result=run(self.fixture,self.registry,retain_current_on_test_failure=True)
        self.assertIsNone(result['test_model']['matrix'])
        self.assertEqual(result['test_model']['execution_failure']['error_type'],'FileNotFoundError')

    def test_dependency_failure_has_no_provider_text(self):
        with patch('active_route_engines.predict_generic_poisson',side_effect=[
                predict_generic_poisson(self.root/'current.json',self.fixture),ValueError('api_key=SECRET')]):
            result=predict_models(self.fixture,self.registry,retain_current_on_test_failure=True)
        self.assertNotIn('SECRET',json.dumps(result['experimental']))
        self.assertTrue(result['test_failure']['artifact_checksum_verified'])

    def test_current_failure_and_wrong_scope_still_block(self):
        (self.root/'current.json').write_text('BAD',encoding='utf-8')
        with self.assertRaises(ValueError):predict_models(self.fixture,self.registry,retain_current_on_test_failure=True)
        with self.assertRaises(ValueError):predict_models(dict(self.fixture,cohort='wrong'),self.registry,retain_current_on_test_failure=True)

    def test_both_successful_outputs_keep_numerical_equivalence(self):
        a=predict_models(self.fixture,self.registry)
        b=predict_models(self.fixture,self.registry,retain_current_on_test_failure=True)
        for role in ('current','experimental'):
            self.assertEqual(a[role]['matrix'],b[role]['matrix'])
            self.assertEqual(a[role]['primary'],b[role]['primary'])
        self.assertIsNone(b['test_failure'])

    def test_programming_errors_are_not_silently_converted(self):
        with patch('active_route_engines.predict_generic_poisson',side_effect=[
                predict_generic_poisson(self.root/'current.json',self.fixture),AssertionError('BUG')]):
            with self.assertRaises(AssertionError):predict_models(self.fixture,self.registry,retain_current_on_test_failure=True)


    def run_form_cli(self, flags, environment=None):
        fixture = self.root / 'fixture.json'
        fixture.write_text(json.dumps(self.fixture), encoding='utf-8')
        out = self.root / 'out.json'
        args = [sys.executable, str(ROOT / 'predict_active.py'), str(fixture),
                '--registry', str(self.registry), '--out', str(out),
                '--freeze-root', str(self.root / 'freezes')]
        result = subprocess.run(args + flags, capture_output=True, text=True, timeout=40,
                                env=dict(os.environ, OPENBLAS_NUM_THREADS='1', **(environment or {})))
        return result, out

    def test_cli_missing_ml_with_history_retains_current(self):
        # Use the real Test loader with a deliberately absent optional dependency.
        del self.route['test_engine']
        self.write_registry()
        # The generic Current adapter also imports LightGBM. Simulate its absence
        # specifically for the native Test loader in this child process.
        (self.root / 'sitecustomize.py').write_text(
            "import builtins\n"
            "original = builtins.__import__\n"
            "def load(name, globals=None, locals=None, fromlist=(), level=0):\n"
            "    if name == 'lightgbm' and (globals or {}).get('__name__') in ('ml_model', 'v6.ml_model'):\n"
            "        raise ImportError('missing optional Test dependency')\n"
            "    return original(name, globals, locals, fromlist, level)\n"
            "builtins.__import__ = load\n", encoding='utf-8')
        self.fixture['recent_league_form'] = {'match_id': self.fixture['match_id'], 'cohort': 'aus.1'}
        result, out = self.run_form_cli(flags=['--retain-current-on-test-failure'],
                                        environment={'PYTHONPATH': str(self.root)})
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(out.read_text(encoding='utf-8'))
        self.assertEqual(payload['prediction_status'], 'PARTIAL_CURRENT_ONLY')
        self.assertEqual(payload['test_model']['execution_failure']['error_type'], 'ImportError')
        self.assertEqual(payload['score_form_candidate']['status'], 'UNSUPPORTED_TEST_ROUTE')
        expected = predict_generic_poisson(self.root / 'current.json', self.fixture)
        self.assertEqual(payload['current_model']['matrix_hash'], expected['matrix_hash'])
        records = list((self.root / 'freezes').glob('*/*.json'))
        self.assertEqual(len(records), 1)
        frozen = verify(json.loads(records[0].read_text(encoding='utf-8')))['payload']
        self.assertEqual(frozen['current_model']['matrix_hash'], expected['matrix_hash'])
        self.assertIsNone(frozen['test_model']['matrix'])

    def test_cli_form_artifact_accepts_both_structured_sides(self):
        for side in ('home', 'away'):
            self.fixture[side + '_form'] = {'team_id': self.fixture[side], 'matches': []}
        artifact = self.root / 'form.json'
        artifact.write_text('{}', encoding='utf-8')
        result, out = self.run_form_cli(flags=['--score-form-artifact', str(artifact)])
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(out.read_text(encoding='utf-8'))
        self.assertEqual(payload['score_form_candidate']['status'], 'UNSUPPORTED_TEST_ROUTE')
        self.assertIsNotNone(payload['current_model']['matrix'])
        self.assertIsNotNone(payload['test_model']['matrix'])

    def test_cli_form_artifact_rejects_incomplete_sides_before_freeze(self):
        self.fixture['home_form'] = {'team_id': self.fixture['home'], 'matches': []}
        self.fixture['away_form'] = {'team_id': self.fixture['away']}
        artifact = self.root / 'form.json'
        artifact.write_text('{}', encoding='utf-8')
        result, out = self.run_form_cli(flags=['--score-form-artifact', str(artifact)])
        self.assertEqual(result.returncode, 2)
        self.assertIn('requires a recent league form snapshot', result.stderr)
        self.assertFalse(out.exists())
        self.assertFalse((self.root / 'freezes').exists())

    def test_cli_opt_in_writes_partial_output_and_report(self):
        self.break_test();fp=self.root/'fixture.json';fp.write_text(json.dumps(self.fixture),encoding='utf-8')
        output=self.root/'cli.json';report=self.root/'report.md'
        result=subprocess.run([sys.executable,str(ROOT/'predict_active.py'),str(fp),
                '--registry',str(self.registry),'--out',str(output),'--report',str(report),
                '--freeze-root',str(self.root/'freezes'),'--retain-current-on-test-failure'],
                capture_output=True,text=True,timeout=40,env=dict(os.environ,OPENBLAS_NUM_THREADS='1'))
        self.assertEqual(result.returncode,0,result.stderr)
        payload=json.loads(output.read_text(encoding='utf-8'))
        self.assertIsNotNone(payload['current_model']['matrix']);self.assertIsNone(payload['test_model']['matrix'])
        self.assertTrue(report.exists());self.assertIn('freeze_receipt',payload)


if __name__=='__main__':unittest.main()
