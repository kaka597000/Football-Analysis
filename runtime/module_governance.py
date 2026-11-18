"""Check source coverage and bounded phase portfolios; never modify registries."""
import argparse
import hashlib
import json
from pathlib import Path

STATES = {'active', 'validation', 'retired', 'archived'}


def validate(root, manifest):
    errors = []
    groups = manifest['modules']
    paths = [g['path'] for g in groups]
    if len(set(paths)) != len(paths):
        errors.append('duplicate module paths')
    for group in groups:
        if group['status'] not in STATES or not (root / group['path']).exists():
            errors.append('invalid module: ' + group['path'])
    for path in (root / 'runtime').rglob('*.py'):
        if path.name.startswith('test_') or '__pycache__' in path.parts:
            continue
        relative = path.relative_to(root).as_posix()
        if not any(relative == p or relative.startswith(p + '/') for p in paths):
            errors.append('unclassified module: ' + relative)
    policy = manifest['consolidation']
    if policy['automatic_promotion'] is not False or policy['historical_development_is_acceptance'] is not False:
        errors.append('promotion or historical acceptance enabled')
    phases = set()
    for row in policy['phase_portfolios']:
        key = (row['competition'], row['stage'])
        if key in phases:
            errors.append('duplicate phase portfolio: ' + str(key))
        phases.add(key)
        candidates = row['integration_candidates']
        if len(candidates) != len(set(candidates)) or len(candidates) > policy['max_integration_candidates_per_phase']:
            errors.append('candidate budget exceeded: ' + str(key))
    for row in policy['frozen_exceptions']:
        if row['status'] != 'validation' or row['action'] != 'complete_original_protocol_before_retirement':
            errors.append('frozen validation changed: ' + row['id'])
    return errors


def check_data(data):
    contract = json.loads((data / 'config/source-contract.json').read_text())
    hashes = dict(contract['protected_sha256'])
    hashes['.github/workflows/unattended.yml'] = contract['workflow_sha256']
    errors = []
    for path, expected in hashes.items():
        file = data / path
        if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest() != expected:
            errors.append('protected file changed: ' + path)
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / 'configs/module-portfolio.json').read_text())
    errors = validate(root, manifest)
    if args.data:
        errors += check_data(args.data)
    print(json.dumps({'errors': errors, 'module_groups': len(manifest['modules']),
                      'registry_routes': len(manifest['registry_snapshot']['routes']),
                      'data_checked': args.data is not None}, indent=2))
    return bool(errors)


if __name__ == '__main__':
    raise SystemExit(main())
