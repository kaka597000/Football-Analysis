"""Original-source and dataset integrity checks for isolated AUS load acceptance."""
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/validate_retained_aus.py'
SPEC = importlib.util.spec_from_file_location('retained_aus_validation', SCRIPT)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class RetainedAusValidationTests(unittest.TestCase):
    def seed(self):
        return dict(cohort='aus.1', competition='aus.1', gender='male', age='senior',
                    stage='regular', season='2026/27', home='espn:5326', away='espn:22344',
                    neutral=None, total_quote={'line': 2.5}, home_goals=3,
                    sources=[{'url': 'https://example.org/old-source'}])

    def assets(self, root):
        source = root / 'source'; (source / 'optimization').mkdir(parents=True)
        (source / 'aus_expert').mkdir()
        (source / 'optimization/schema.py').write_text('# original schema\n')
        (source / 'aus_expert/runner.py').write_text('# original runner\n')
        data = root / 'data'; data.mkdir(); (data / 'research.sqlite').write_bytes(b'synthetic database bytes')
        (data / 'manifest.json').write_text(json.dumps({'files': {'research.sqlite': module.sha(data / 'research.sqlite')}}))
        files = {'schema.py': module.sha(source / 'optimization/schema.py')}
        code = hashlib.sha256(json.dumps(files, sort_keys=True, ensure_ascii=False,
                                         separators=(',', ':')).encode()).hexdigest()
        artifact = root / 'test.json'
        artifact.write_text(json.dumps({'dataset': str(data), 'dataset_manifest_hash': module.sha(data / 'manifest.json'),
                                        'optimization_code_hash': code, 'runner_sha256': module.sha(source / 'aus_expert/runner.py')}))
        current = root / 'current.json'; current.write_text('{}')
        registry = root / 'registry.json'
        registry.write_text(json.dumps({'status': 'ACTIVE_RESEARCH', 'models': {'aus.1': {
            'current_engine': 'generic_poisson', 'test_engine': 'aus_expert',
            'artifact_path': str(current), 'artifact_sha256': module.sha(current),
            'test_artifact_path': str(artifact), 'test_artifact_sha256': module.sha(artifact)}}}))
        manifest = root / 'retained.json'
        manifest.write_text(json.dumps({'source_root': str(source), 'source_files': {
            str(p.relative_to(source)): module.sha(p) for p in source.rglob('*.py')}}))
        return registry, manifest, source, data, artifact

    def test_exact_original_dependencies_pass(self):
        with tempfile.TemporaryDirectory() as folder:
            registry, manifest, source, _, _ = self.assets(Path(folder))
            self.assertEqual(module.checked_assets(registry, manifest)[1], source)

    def test_artifact_mutation_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            registry, manifest, _, _, artifact = self.assets(Path(folder))
            artifact.write_text(artifact.read_text() + '\n')
            with self.assertRaisesRegex(ValueError, 'registered artifact changed'):
                module.checked_assets(registry, manifest)

    def test_retained_source_mutation_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            registry, manifest, source, _, _ = self.assets(Path(folder))
            (source / 'optimization/schema.py').write_text('# different schema\n')
            with self.assertRaisesRegex(ValueError, 'retained runtime source changed'):
                module.checked_assets(registry, manifest)

    def test_relisting_changed_source_cannot_bypass_original_artifact_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            registry, manifest, source, _, _ = self.assets(Path(folder))
            (source / 'aus_expert/runner.py').write_text('# different runner\n')
            value = json.loads(manifest.read_text())
            value['source_files']['aus_expert/runner.py'] = module.sha(source / 'aus_expert/runner.py')
            manifest.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError, 'not the artifact original dependency'):
                module.checked_assets(registry, manifest)

    def test_unchanged_manifest_does_not_hide_changed_database_member(self):
        with tempfile.TemporaryDirectory() as folder:
            registry, manifest, _, data, _ = self.assets(Path(folder))
            (data / 'research.sqlite').write_bytes(b'changed database')
            with self.assertRaisesRegex(ValueError, 'dataset member changed: research.sqlite'):
                module.checked_assets(registry, manifest)

    def test_synthetic_seed_retains_identity_but_not_outcomes_or_old_evidence(self):
        now = datetime(2026, 11, 12, 15, tzinfo=timezone.utc)
        fixture = module.sandbox_fixture(self.seed(), now)
        self.assertEqual(fixture['match_id'], 'SYNTHETIC-RETAINED-AUS-LOAD')
        self.assertEqual(fixture['home'], 'espn:5326')
        self.assertIsNone(fixture['neutral'])
        self.assertEqual(fixture['date'], '2026-11-13')
        self.assertNotIn('home_goals', fixture)
        self.assertNotIn('total_quote', fixture)
        self.assertNotIn('sources', fixture)

    def test_other_scope_or_provider_seed_is_rejected(self):
        for change in ({'stage': 'finals'}, {'cohort': 'kor.1'}, {'gender': 'women'}, {'home': 'name:team'}):
            with self.assertRaises(ValueError):
                module.sandbox_fixture(dict(self.seed(), **change), datetime.now(timezone.utc))


if __name__ == '__main__':
    unittest.main()
