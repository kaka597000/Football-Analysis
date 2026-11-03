"""Regressions for the four concrete PR #5 review findings."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

ROOT=Path(__file__).resolve().parent

class ReviewContracts(unittest.TestCase):
    def test_chinese_freeze_resume_and_append_under_cp1252_default(self):
        from publication import publish
        from research.analysis_freeze import save,verify
        now=datetime.now(timezone.utc)
        fixture={'match_id':'synthetic-中文','cutoff_at':(now-timedelta(minutes=1)).isoformat(),
                 'kickoff_at':(now+timedelta(days=1)).isoformat()}
        payload={'model_label':'当前模型','reason':'中文预测，比分与大小球独立判断'}
        original=Path.read_text
        def cp1252_default(path, encoding=None, errors=None, **kwargs):
            return original(path,encoding=encoding or 'cp1252',errors=errors,**kwargs)
        with tempfile.TemporaryDirectory() as d,patch.object(Path,'read_text',cp1252_default):
            root=Path(d)/'冻结';out=Path(d)/'结果.json'
            with patch('publication.deliver',side_effect=OSError('synthetic interruption')):
                with self.assertRaises(OSError):publish(payload,fixture,out=out,freeze_root=root)
            result=publish(None,None,out=out,freeze_root=root,resume=True)
            self.assertEqual(result['model_label'],'当前模型')
            again=publish(None,None,out=out,freeze_root=root,resume=True)
            self.assertEqual(result['freeze_receipt'],again['freeze_receipt'])
            save(dict(payload,reason='中文追加'),fixture,stage='prematch',track='current-test-pair',root=root)
            records=sorted(root.glob('*/*.json'))
            self.assertEqual(len(records),2)
            first=verify(json.loads(records[0].read_text(encoding='utf-8')))
            second=verify(json.loads(records[1].read_text(encoding='utf-8')))
            self.assertEqual(second['previous_sha256'],first['record_sha256'])

    def test_standalone_adapters_fit_booster_without_pythonpath(self):
        # Match the script-directory search path used by direct training CLIs.
        code='''import sys,json
sys.path.insert(0,sys.argv[1])
m=__import__(sys.argv[2])
import numpy as np
items=[{'features':np.zeros((2,len(m.FEATURES))),'rates':(1.4,1.),'row':{'home_goals':2,'away_goals':0}} for _ in range(32)]
booster=m.fit_booster(items)
assert booster.num_feature()==len(m.FEATURES)
assert len(m.code_hash())==64
from asof_contract import audit_feature_availability
assert audit_feature_availability([])['status']=='NOT_CERTIFIED'
print('DIRECT_ADAPTER_FIT_OK')
'''
        env=dict(os.environ);env.pop('PYTHONPATH',None)
        for directory,module in [('v6','ml_model'),('continental','ucl_ml')]:
            with self.subTest(module=module),tempfile.TemporaryDirectory() as d:
                result=subprocess.run([sys.executable,'-c',code,str(ROOT/directory),module],
                                      cwd=d,env=env,capture_output=True,text=True,timeout=60)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertIn('DIRECT_ADAPTER_FIT_OK',result.stdout)
        with tempfile.TemporaryDirectory() as d:
            result=subprocess.run([sys.executable,str(ROOT/'v6/cli.py'),'--help'],
                                  cwd=d,env=env,capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr)

    def test_generic_route_pair_fuses_with_actual_generation_time(self):
        from prediction_engine import predict_models
        from fuse_models import fuse
        now=datetime.now(timezone.utc);kickoff=now+timedelta(days=1)
        fixture={'match_id':'SYNTHETIC_GENERIC','cohort':'aus.1',
                 'competition':'aus.1','gender':'male','age':'senior',
                 'stage':'regular','phase':'PreMatch','identity_verified':True,
                 'date':kickoff.date().isoformat(),'home':'A','away':'B','neutral':False,
                 'cutoff_at':(now-timedelta(minutes=1)).isoformat(),'kickoff_at':kickoff.isoformat()}
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);manifest=root/'dataset.json';manifest.write_text('{}',encoding='utf-8')
            route={'current_engine':'generic_poisson','test_engine':'generic_poisson'}
            for prefix,role in [('', 'current'),('test_','test')]:
                artifact={'version':'SYNTHETIC_'+role,'role':role,'engine_contract':'WCPP-GENERIC-POISSON-ROUTE-1',
                          'model_kind':'constant','core':{'constant':[1.4,1.], 'fit_n':100,'fit_end':'2025-01-01'},
                          'created_at':(now-timedelta(days=2)).isoformat(),
                          'forecast_scope':{'cohort':'aus.1','competition':'aus.1','stages':['regular']},
                          'dataset_manifest':str(manifest),'dataset_manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest()}
                path=root/(role+'.json');path.write_text(json.dumps(artifact),encoding='utf-8')
                route[prefix+'artifact_path']=str(path);route[prefix+'artifact_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
            registry=root/'registry.json';registry.write_text(json.dumps({'version':'SYNTHETIC','status':'ACTIVE_RESEARCH','models':{'aus.1':route}}),encoding='utf-8')
            state=predict_models(fixture,registry)
            pair={'match_id':fixture['match_id'],'current_model':state['current'],'test_model':state['experimental'],
                  'execution_audit':{role:{'engine':'generic_poisson','artifact_sha256':route[prefix+'artifact_sha256']}
                                     for prefix,role in [('', 'current'),('test_','test')]}}
            result=fuse(pair)
            for key in ('current_model','test_model'):
                generated=datetime.fromisoformat(pair[key]['generated_at'])
                self.assertLessEqual(now,generated);self.assertLessEqual(generated,datetime.now(timezone.utc))
            self.assertAlmostEqual(sum(map(sum,result['matrix'])),1.)
            self.assertFalse(result['active_models_modified'])

    def test_audit_uses_dispatch_fields_and_defaults(self):
        spec=importlib.util.spec_from_file_location('review_v2_audit',ROOT/'v2_test/audit.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        for current in ('generic_poisson','v4_main_compat'):
            route={'current_engine':current,'engine':'WRONG_LEGACY_FIELD',
                   'test_artifact_path':'test.json','test_engine':'aus_expert'}
            self.assertEqual(module.route_engine(route,'current'),current)
            self.assertEqual(module.route_engine(route,'test'),'aus_expert')
        self.assertEqual(module.route_engine({},'current'),'legacy_weighted_anchor')
        self.assertEqual(module.route_engine({},'test'),'v5.weighted_model.predict')
        self.assertEqual(module.route_engine({'test_artifact_path':'x','test_version':'WCPP-ML-UCL-0.1'},'test'),'continental.ucl_ml.predict')
        self.assertEqual(module.route_engine({'test_artifact_path':'x'},'test'),'v6.ml_model.predict')

if __name__=='__main__':unittest.main()
