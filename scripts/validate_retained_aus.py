"""Read-only AUS Current/Test load acceptance with original Test dependencies.

Reuses the retained source manifest, leaves active routing and artifacts unchanged,
and generates a synthetic future fixture only. This is not a prematch delivery.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def checked_assets(registry_path, retained_manifest):
    registry = json.loads(Path(registry_path).read_text())
    if registry.get('status') != 'ACTIVE_RESEARCH':
        raise ValueError('active research registry required')
    route = registry['models']['aus.1']
    if route.get('current_engine') != 'generic_poisson' or route.get('test_engine') != 'aus_expert':
        raise ValueError('explicit AUS Current/Test engines required')
    for prefix in ('', 'test_'):
        if sha(route[prefix + 'artifact_path']) != route[prefix + 'artifact_sha256']:
            raise ValueError('registered artifact changed')
    retained = json.loads(Path(retained_manifest).read_text())
    source = Path(retained['source_root']).resolve()
    files = {str(p.relative_to(source)): sha(p) for p in source.rglob('*.py')}
    if files != retained['source_files']:
        raise ValueError('retained runtime source changed')
    artifact = json.loads(Path(route['test_artifact_path']).read_text())
    optimization = {p.name: sha(p) for p in sorted((source / 'optimization').glob('*.py'))
                    if not p.name.startswith('test_')}
    code_hash = hashlib.sha256(json.dumps(optimization, sort_keys=True, ensure_ascii=False,
                                         separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    if code_hash != artifact['optimization_code_hash'] or sha(source / 'aus_expert/runner.py') != artifact['runner_sha256']:
        raise ValueError('retained source is not the artifact original dependency')
    if sha(Path(artifact['dataset']) / 'manifest.json') != artifact['dataset_manifest_hash']:
        raise ValueError('retained dataset manifest changed')
    dataset = Path(artifact['dataset'])
    for name, checksum in json.loads((dataset / 'manifest.json').read_text())['files'].items():
        if sha(dataset / name) != checksum:
            raise ValueError('retained dataset member changed: ' + name)
    return route, source, artifact


def sandbox_fixture(seed, now):
    if (seed.get('cohort') != 'aus.1' or seed.get('competition') != 'aus.1'
            or seed.get('gender') not in ('male', 'men') or seed.get('age') != 'senior'
            or seed.get('stage') not in ('regular', 'regular-season')):
        raise ValueError('AUS senior male regular-season seed required')
    if any(not str(seed.get(side, '')).startswith('espn:') for side in ('home', 'away')):
        raise ValueError('existing ESPN team IDs required')
    kickoff = now + timedelta(days=1)
    # Copy only inference inputs; historical prices, sources and outcomes cannot
    # masquerade as evidence for this synthetic future test.
    result = {key: seed[key] for key in ('cohort', 'competition', 'gender', 'age', 'stage',
                                        'season', 'home', 'away', 'neutral')}
    result.update(match_id='SYNTHETIC-RETAINED-AUS-LOAD', phase='PreMatch',
                  date=kickoff.date().isoformat(), cutoff_at=now.isoformat(), kickoff_at=kickoff.isoformat())
    return result


CHILD = r'''
import hashlib, importlib.util, json, sys
from pathlib import Path
role, source, runtime, artifact = sys.argv[1:]
source, runtime = Path(source), Path(runtime)
fixture = json.load(sys.stdin)
if role == 'test':
    # Old runner has a hard-coded optimization path. Resolve its four imported
    # dependencies to the checked original source before executing it unchanged.
    sys.path.insert(0, str(source / 'optimization'))
    import schema, models, features, dataset
    for module in (schema, models, features, dataset):
        if not Path(module.__file__).resolve().is_relative_to(source):
            raise ValueError('original dependency resolution failed')
    spec = importlib.util.spec_from_file_location('retained_aus_runner', source / 'aus_expert/runner.py')
    runner = importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)
    prediction = runner.calculate(artifact, fixture)
    imported = {str(Path(m.__file__).resolve()): hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest()
                for m in tuple(sys.modules.values()) if getattr(m, '__file__', None)
                and str(m.__file__).endswith('.py') and Path(m.__file__).resolve().is_relative_to(source)}
    imported[str(source / 'aus_expert/runner.py')] = hashlib.sha256((source / 'aus_expert/runner.py').read_bytes()).hexdigest()
else:
    sys.path.insert(0, str(runtime))
    from active_route_engines import predict_generic_poisson
    prediction = predict_generic_poisson(artifact, fixture)
    imported = {str(Path(m.__file__).resolve()): hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest()
                for m in tuple(sys.modules.values()) if getattr(m, '__file__', None)
                and str(m.__file__).endswith('.py') and Path(m.__file__).resolve().is_relative_to(runtime)}
import numpy as np, scipy, lightgbm
matrix = np.asarray(prediction.get('matrix'), dtype=float)
if matrix.ndim != 2 or not np.isfinite(matrix).all() or (matrix < 0).any() or abs(matrix.sum() - 1) > 1e-9:
    raise ValueError('no valid prediction matrix')
print(json.dumps({'prediction': prediction, 'imported_source_sha256': imported,
                  'packages': {'python': sys.version, 'numpy': np.__version__, 'scipy': scipy.__version__, 'lightgbm': lightgbm.__version__}},
                 ensure_ascii=False, allow_nan=False))
'''


def validate(registry_path, retained_manifest, seed_path):
    registry_path, retained_manifest, seed_path = map(Path, (registry_path, retained_manifest, seed_path))
    route, source, artifact = checked_assets(registry_path, retained_manifest)
    before = {str(p): sha(p) for p in (registry_path, retained_manifest, seed_path,
                                      Path(route['artifact_path']), Path(route['test_artifact_path']))}
    fixture = sandbox_fixture(json.loads(seed_path.read_text()), datetime.now(timezone.utc))
    predictions = {}
    for role, key in [('current', 'artifact_path'), ('test', 'test_artifact_path')]:
        result = subprocess.run([sys.executable, '-I', '-c', CHILD, role, str(source),
                                 str(ROOT / 'runtime'), route[key]], input=json.dumps(fixture),
                                capture_output=True, text=True, timeout=90, check=False)
        if result.returncode:
            raise ValueError(role + ' isolated load failed: ' + result.stderr[-1500:])
        predictions[role] = json.loads(result.stdout)
    checked_assets(registry_path, retained_manifest)
    if any(sha(p) != checksum for p, checksum in before.items()):
        raise ValueError('inputs changed during isolated validation')
    return {'schema': 'WCPP-RETAINED-AUS-LOAD-1', 'status': 'PAIRED_SYNTHETIC_LOAD_VALIDATED',
            'created_at': datetime.now(timezone.utc).isoformat(), 'fixture': fixture,
            'validator_source_sha256': sha(__file__),
            'original_seed': str(seed_path), 'input_files_sha256': before,
            'retained_source_root': str(source), 'optimization_code_hash': artifact['optimization_code_hash'],
            'original_runner_sha256': artifact['runner_sha256'], 'models': predictions,
            'actual_prematch_acceptance': False, 'active_route_modified': False,
            'historical_freezes_modified': False, 'clock_checks_disabled': False,
            'forecast_published': False, 'accuracy_claimed': False,
            'deployment_status': 'ISOLATED_LOAD_ONLY; active dispatcher not switched'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('registry', 'retained-manifest', 'seed', 'out'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    result = validate(args.registry, args.retained_manifest, args.seed)
    with Path(args.out).open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({k: v for k, v in result.items() if k not in ('models', 'fixture')}))
