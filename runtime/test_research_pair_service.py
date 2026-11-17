import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import research_pair_service as service

class ResearchPairServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'registry.json'
        self.fixture={'cohort':'uefa.europa','competition':'uefa.europa','stage':'league-phase'}
        self.registry={'schema':service.SCHEMA,'automatic_promotion':False,'routes':{'uefa.europa':{'engine':'europe','status':'READY_RESEARCH_PAIR'}}}
        self.save()
    def save(self): self.path.write_text(json.dumps(self.registry))
    def test_missing_route_is_not_a_pair(self):
        self.registry['routes']={};self.save()
        with self.assertRaises(ValueError):service.predict_pair(self.fixture,self.path)
    def test_promotion_registry_rejected(self):
        self.registry['automatic_promotion']=True;self.save()
        with self.assertRaises(ValueError):service.read_registry(self.path)
    def test_no_arbitrary_import(self):
        self.registry['routes']['uefa.europa']['engine']='os';self.save()
        with self.assertRaises(ValueError):service.predict_pair(self.fixture,self.path)
    def test_valid_real_pair_contract(self):
        with patch('research_pair_service.importlib.import_module') as module:
            module.return_value.predict_pair.return_value={k:{'matrix':[[.4,.1],[.2,.3]]} for k in ('current_model','test_model')}
            value=service.predict_pair(self.fixture,self.path)
        self.assertFalse(value['automatic_promotion']);self.assertFalse(value['active_model_modified'])
    def test_empty_or_invalid_matrix_cannot_claim_ready(self):
        for m in (None,[[1,1],[1,1]],[[float('nan'),0],[0,0]],[[1.1,-.1],[0,0]]):
            with self.subTest(matrix=m),patch('research_pair_service.importlib.import_module') as module:
                module.return_value.predict_pair.return_value={'current_model':{'matrix':m},'test_model':{'matrix':[[.4,.1],[.2,.3]]}}
                with self.assertRaises(ValueError):service.predict_pair(self.fixture,self.path)
    def test_existing_route_is_preserved_and_not_overlaid(self):
        active=Path(self.tmp.name)/'active.json'
        active.write_text(json.dumps({'models':{'uefa.europa':{}}}))
        with patch.dict('sys.modules',{'prediction_service':__import__('types').SimpleNamespace(run=lambda *a: {k:{'matrix':[[.4,.1],[.2,.3]]} for k in ('current_model','test_model')})}):
            with patch('research_pair_service.predict_pair',side_effect=AssertionError('overlay must not replace existing route')):
                result=service.predict_any_pair(self.fixture,research_registry_path=self.path,active_registry_path=active)
        self.assertEqual(result['route_origin'],'EXISTING_ACTIVE_RESEARCH')

    def test_registry_race_rejected(self):
        with patch('research_pair_service.importlib.import_module') as module:
            def mutate(*args):
                self.registry['note']='changed';self.save()
                return {k:{'matrix':[[.4,.1],[.2,.3]]} for k in ('current_model','test_model')}
            module.return_value.predict_pair.side_effect=mutate
            with self.assertRaises(ValueError):service.predict_pair(self.fixture,self.path)

if __name__=='__main__':unittest.main()
