import copy
import json
from pathlib import Path
import tempfile
import unittest
from module_governance import validate, check_data

ROOT = Path(__file__).resolve().parents[1]


class GovernanceTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((ROOT / 'configs/module-portfolio.json').read_text())

    def test_repository_covered(self):
        self.assertEqual(validate(ROOT, self.manifest), [])

    def test_candidate_sprawl_and_duplicate_phase_rejected(self):
        row = self.manifest['consolidation']['phase_portfolios'][0]
        row['integration_candidates'].append('another-model')
        self.assertTrue(any('budget exceeded' in e for e in validate(ROOT, self.manifest)))
        self.manifest['consolidation']['phase_portfolios'].append(copy.deepcopy(row))
        self.assertTrue(any('duplicate phase' in e for e in validate(ROOT, self.manifest)))

    def test_unclassified_source_rejected(self):
        self.manifest['modules'] = [g for g in self.manifest['modules'] if g['path'] != 'runtime/module_governance.py']
        self.assertIn('unclassified module: runtime/module_governance.py', validate(ROOT, self.manifest))

    def test_frozen_experiment_cannot_be_retired_early(self):
        self.manifest['consolidation']['frozen_exceptions'][0]['status'] = 'retired'
        self.assertTrue(any('frozen validation' in e for e in validate(ROOT, self.manifest)))

    def test_missing_protected_file_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'config').mkdir()
            (root / 'config/source-contract.json').write_text(json.dumps({
                'protected_sha256': {'plan.json': '0' * 64}, 'workflow_sha256': '0' * 64}))
            self.assertIn('protected file changed: plan.json', check_data(root))

    def test_historical_routes_cannot_be_labelled_active(self):
        row = next(r for r in self.manifest['registry_snapshot']['routes'] if not r['analysis_eligible'])
        row['status'] = 'active'
        self.assertTrue(any('route scope' in e for e in validate(ROOT, self.manifest)))

    def test_retired_installer_exits_without_activation(self):
        import subprocess
        import sys
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/setup_unattended.py')], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('No changes were made', result.stderr)

    def test_managed_workflow_cannot_bypass_manifest(self):
        import hashlib
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'config').mkdir()
            (root / '.github/workflows').mkdir(parents=True)
            unattended = root / '.github/workflows/unattended.yml'
            unattended.write_text('unchanged')
            check = root / '.github/workflows/replay.yml'
            check.write_text('repository: kaka597000/Football-Analysis\nref: main\npath: source\n'
                             'runtime/source_versions.py --github-output "$GITHUB_OUTPUT"')
            contract = {'protected_sha256': {}, 'workflow_sha256': hashlib.sha256(unattended.read_bytes()).hexdigest(),
                        'managed_workflows': {'replay.yml': 'three-arm-code'}}
            (root / 'config/source-contract.json').write_text(json.dumps(contract))
            self.assertEqual(check_data(root), ['workflow bypasses version contract: replay.yml'])
