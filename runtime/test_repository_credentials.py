"""Configured credential aliases must be covered by the pre-commit scan."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from data_sources.clients import PROVIDERS, QUARANTINED_CREDENTIAL_NAMES

SPEC = importlib.util.spec_from_file_location(
    'check_repository', Path(__file__).resolve().parents[1] / 'scripts/check_repository.py')
SCAN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCAN)


class RepositoryCredentialTests(unittest.TestCase):
    def test_provider_bindings_and_non_client_secrets_are_scanned(self):
        bindings = set(QUARANTINED_CREDENTIAL_NAMES)
        for provider in PROVIDERS.values():
            bindings.update(provider.get('aliases', []))
            if provider.get('env'):
                bindings.add(provider['env'])
        bindings.update(('WCPP_ODDS_API_KEY', 'WCPP_INFERSPORTS_KEY',
                         'infer_yapan_zhishu', 'WCPP_SMTP_PASSWORD'))
        value = 'SYNTHETIC_' + 'BOUND_CREDENTIAL_ONLY'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'sample.txt').write_text('unrelated_label = "' + value + '"\n')
            with patch.object(SCAN, 'ROOT', root), patch.object(
                    SCAN.subprocess, 'check_output', return_value=b'sample.txt\0'):
                for binding in sorted(bindings):
                    with self.subTest(binding=binding), patch.dict(
                            os.environ, {binding: value}, clear=True):
                        output = io.StringIO()
                        with contextlib.redirect_stdout(output):
                            rejected = SCAN.main()
                        self.assertTrue(rejected)
                        self.assertIn('configured_credential_value', output.getvalue())
                        self.assertNotIn(value, output.getvalue())

    def test_binding_names_without_values_are_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'sample.txt').write_text('bsd\nAPIFOOTBALL\nWCPP_SMTP_PASSWORD\n')
            with patch.object(SCAN, 'ROOT', root), patch.object(
                    SCAN.subprocess, 'check_output', return_value=b'sample.txt\0'), patch.dict(
                        os.environ, {}, clear=True), contextlib.redirect_stdout(io.StringIO()):
                self.assertFalse(SCAN.main())


if __name__ == '__main__':
    unittest.main()
