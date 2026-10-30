import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import sys
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from runtime.automation.run import execute


class RunTests(unittest.TestCase):
    def state(self, folder):
        root=Path(folder);(root/'seed').mkdir();(root/'config').mkdir()
        (root/'seed/training.json.gz').write_bytes(b'SYNTHETIC_SEED_ONLY')
        (root/'seed/manifest.json').write_text(json.dumps({'seed_sha256':hashlib.sha256(b'SYNTHETIC_SEED_ONLY').hexdigest()}))
        (root/'config/scopes.json').write_text('[]')
        return root

    def test_cloud_collection_budget_reaches_collector_and_audit(self):
        with tempfile.TemporaryDirectory() as folder:
            root=self.state(folder)
            with patch('runtime.automation.collector.collect',return_value={'successful_requests':1}) as collect:
                result=execute(root,'collect',max_collection_seconds=150)
            self.assertEqual(collect.call_args.kwargs['max_seconds'],150)
            self.assertEqual(result['policy']['max_collection_seconds'],150)
            self.assertEqual(result['status'],'COMPLETED')
            self.assertEqual(len(list((root/'runs').glob('*.json'))),1)

    def test_no_response_is_not_success_and_error_body_is_not_saved(self):
        with tempfile.TemporaryDirectory() as folder:
            root=self.state(folder)
            with patch('runtime.automation.collector.collect',return_value={'successful_requests':0}):
                self.assertEqual(execute(root,'collect')['status'],'FAILED_NO_USABLE_RESPONSE')
        with tempfile.TemporaryDirectory() as folder:
            root=self.state(folder)
            with patch('runtime.automation.collector.collect',side_effect=RuntimeError('SENSITIVE_RESPONSE_BODY')):
                result=execute(root,'collect')
            self.assertEqual(result['status'],'FAILED')
            self.assertNotIn('SENSITIVE_RESPONSE_BODY',json.dumps(result))

    def test_bad_seed_stops_before_network(self):
        with tempfile.TemporaryDirectory() as folder:
            root=self.state(folder);(root/'seed/training.json.gz').write_bytes(b'CHANGED')
            with patch('runtime.automation.collector.collect') as collect:
                with self.assertRaisesRegex(ValueError,'seed identity'):
                    execute(root,'collect')
            collect.assert_not_called()

    def test_invalid_budget_stops_before_side_effects(self):
        for value in (True,0,29,301,150.0):
            with self.assertRaises(ValueError):execute('/does-not-exist','collect',max_collection_seconds=value)


if __name__=='__main__':unittest.main()
