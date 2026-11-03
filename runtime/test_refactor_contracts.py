"""Behavioral contracts for publication recovery and historical model compatibility."""
import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import numpy as np
import publication
from research.analysis_freeze import save, verify
from runtime_config import registry_path
from model_source_identity import source_hash

class PublicationTests(unittest.TestCase):
    def fixture(self):
        now=datetime.now(timezone.utc)
        return {'match_id':'synthetic-recovery','kickoff_at':(now+timedelta(days=1)).isoformat(),
                'cutoff_at':(now-timedelta(minutes=1)).isoformat()}

    def test_interrupted_delivery_reuses_original_freeze_and_payload(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/'out.json';root=Path(d)/'freezes';fixture=self.fixture()
            with patch('publication.deliver',side_effect=OSError('synthetic disk failure')):
                with self.assertRaises(OSError):publication.publish({'value':1},fixture,out=out,freeze_root=root)
            records=list(root.glob('*/*.json'));self.assertEqual(len(records),1)
            result=publication.publish(None,None,out=out,freeze_root=root,resume=True)
            self.assertEqual(len(list(root.glob('*/*.json'))),1)
            self.assertEqual(result['value'],1)
            self.assertEqual(json.loads(out.read_text()),result)
            self.assertEqual(verify(json.loads(records[0].read_text()))['payload'],{'value':1})
            publication.publish(None,None,out=out,freeze_root=root,resume=True)
            self.assertEqual(len(list(root.glob('*/*.json'))),1)

    def test_parallel_freezes_keep_one_valid_append_chain(self):
        import subprocess,sys,os
        with tempfile.TemporaryDirectory() as d:
            f=self.fixture()
            script="import json,sys;from research.analysis_freeze import save;save({'synthetic':True},json.loads(sys.argv[1]),stage='prematch',track='concurrent',root=sys.argv[2])"
            env=dict(os.environ,PYTHONPATH=str(Path(__file__).parent))
            workers=[subprocess.Popen([sys.executable,'-c',script,json.dumps(f),d],env=env) for _ in range(3)]
            for worker in workers:self.assertEqual(worker.wait(timeout=60),0)
            records=sorted(Path(d).glob('*/*.json'))
            self.assertEqual(len(records),3)
            previous=None
            for path in records:
                record=verify(json.loads(path.read_text()))
                self.assertEqual(record['previous_sha256'],previous)
                previous=record['record_sha256']

    def test_conflicting_idempotency_payload_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            f=self.fixture();save({'value':1},f,stage='prematch',track='synthetic',root=d,idempotency_key='id')
            with self.assertRaises(ValueError):save({'value':2},f,stage='prematch',track='synthetic',root=d,idempotency_key='id')

    def test_explicit_registry_wins_over_environment(self):
        with patch.dict('os.environ',{'WCPP_RUNTIME_ROOT':'/tmp/synthetic-root','WCPP_ACTIVE_MODEL':'/tmp/env.json'}):
            self.assertEqual(registry_path('/tmp/explicit.json'),Path('/tmp/explicit.json'))
            self.assertEqual(registry_path(),Path('/tmp/env.json'))

class AsOfTests(unittest.TestCase):
    def test_collection_timestamp_cannot_replace_first_known(self):
        from asof_contract import require_historical_replay
        rows=[{'feature_available_by':'2026-01-01T00:00:00Z',
               'xg_evidence':{'query_time':'2026-01-01T00:00:00Z'}}]
        with self.assertRaises(ValueError):require_historical_replay(rows)
        rows[0]['xg_evidence']['first_known_at']='2026-01-02T00:00:00Z'
        with self.assertRaises(ValueError):require_historical_replay(rows)
        rows[0]['xg_evidence']['first_known_at']='2025-12-31T00:00:00Z'
        self.assertEqual(require_historical_replay(rows)['late_blocks'],0)

class SharedCoreTests(unittest.TestCase):
    def test_both_historical_source_hashes_remain_explicitly_compatible(self):
        root=Path(__file__).parent;m=json.loads((root/'ml-source-compatibility.json').read_text())
        for name,entry in m['adapters'].items():self.assertEqual(source_hash(root/name),entry['legacy_sha256'])
        with patch('model_source_identity.sha',return_value='changed'):
            self.assertNotEqual(source_hash(root/'v6/ml_model.py'),m['adapters']['v6/ml_model.py']['legacy_sha256'])

    def test_shared_matrix_matches_original_math_and_clamps(self):
        from ml_core import ml_matrix
        from prediction_engine import legacy
        class Booster:
            def predict(self,*a,**kw):return np.array([100.,-100.])
        matrix,delta=ml_matrix(Booster(),np.zeros((2,1)),[2.,1.],-.05,{'residual_cap':.4},legacy)
        expected=legacy.matrix(float(2*np.exp(.4)),float(np.exp(-.4)),'dixon_coles',-.05)
        np.testing.assert_array_equal(matrix,expected)
        np.testing.assert_array_equal(delta,[.4,-.4])

if __name__=='__main__':unittest.main()
