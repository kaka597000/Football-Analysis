import sys,copy,unittest,tempfile,json,hashlib,os
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'v6'))
import predict_active,ml_model
from weighted_model import legacy
class DispatcherTests(unittest.TestCase):
 def setUp(self):
  self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
  root=Path(self.folder.name);anchor=root/'anchor.json';ml=root/'ml.json'
  anchor.write_text(json.dumps({'anchor':{'train_end':'2020-01-01'}}));ml.write_text('{}')
  reg=root/'registry.json';reg.write_text(json.dumps({'version':'SYNTHETIC-TEST-ONLY','status':'ACTIVE_RESEARCH','models':{'eng.1':{'artifact_path':str(anchor),'artifact_sha256':hashlib.sha256(anchor.read_bytes()).hexdigest(),'test_artifact_path':str(ml),'test_artifact_sha256':hashlib.sha256(ml.read_bytes()).hexdigest()}}}))
  env=patch.dict(os.environ,{'WCPP_ACTIVE_MODEL':str(reg)});env.start();self.addCleanup(env.stop)
 def test_sidecar_failure_does_not_change_either_main_output(self):
  fixture={'match_id':'synthetic-test-only','cohort':'eng.1','cutoff_at':'2026-10-13T00:00:00+00:00'}
  current={'input':fixture,'primary':'1-0','wdl':{'主胜':.6,'平局':.2,'客胜':.2},'matrix':[[.1,.2],[.5,.2]],'matrix_hash':legacy.digest([[.1,.2],[.5,.2]])}
  test={'input':fixture,'primary':'1-1','wdl':{'主胜':.4,'平局':.4,'客胜':.2},'matrix':[[.1,.2],[.3,.4]],'matrix_hash':legacy.digest([[.1,.2],[.3,.4]])}
  with patch.object(legacy,'predict',return_value=copy.deepcopy(current)),patch.object(ml_model,'predict',return_value=copy.deepcopy(test)),patch('classifier.attach',side_effect=ValueError('test invalid market')):
   result=predict_active.run(fixture)
  self.assertEqual(result['current_model']['matrix'],current['matrix']);self.assertEqual(result['test_model']['matrix'],test['matrix']);self.assertEqual(result['current_model']['primary'],'1-0');self.assertEqual(result['test_model']['primary'],'1-1');self.assertEqual(result['upset_classifier']['status'],'ABSTAIN_INVALID_CLASSIFIER_INPUT_OR_ARTIFACT')
  self.assertFalse(result['execution_audit']['prediction_values_modified'])
  self.assertEqual(result['execution_audit']['fixture_sha256'],predict_active.object_sha256(fixture))
  self.assertTrue(result['execution_audit']['entrypoint_sha256'])
  self.assertEqual(result['execution_audit']['source_identity']['status'],'GIT_IDENTIFIED')
  self.assertEqual(len(result['execution_audit']['source_identity']['commit']),40)
  self.assertEqual(result['current_test_disagreement']['status'],'READY')
  self.assertTrue(result['current_test_disagreement']['primary_disagreement'])
 def test_candidate_failure_does_not_change_either_main_output(self):
  fixture={'match_id':'synthetic-candidate-failure','cohort':'eng.1','cutoff_at':'2026-10-13T00:00:00+00:00'}
  current={'input':fixture,'primary':'1-0','matrix':[[.1,.2],[.5,.2]],'matrix_hash':legacy.digest([[.1,.2],[.5,.2]])}
  test={'input':fixture,'primary':'1-1','matrix':[[.1,.2],[.3,.4]],'matrix_hash':legacy.digest([[.1,.2],[.3,.4]])}
  from upset_v2 import predict as candidate
  with patch.object(legacy,'predict',return_value=copy.deepcopy(current)),patch.object(ml_model,'predict',return_value=copy.deepcopy(test)),patch('classifier.attach',return_value={'status':'research'}),patch.object(candidate,'run',side_effect=RuntimeError('synthetic candidate failure')):
   result=predict_active.run(fixture)
  self.assertEqual(result['current_model']['matrix'],current['matrix']);self.assertEqual(result['test_model']['matrix'],test['matrix']);self.assertEqual(result['upset_candidate']['status'],'ABSTAIN_INVALID_CANDIDATE_INPUT_OR_ARTIFACT')
if __name__=='__main__':unittest.main()
