"""Strict future entry points around the unchanged, hash-pinned Cup engines."""
import argparse
import json
import os
from pathlib import Path
import tempfile

from . import database, evidence, model, quarterfinals, safe_evidence

GUARD_VERSION = 'WCPP-JCUP-EXECUTION-GUARD-1'


def read(path):
    return json.loads(Path(path).read_text())


def execution_guard(artifact_path=None, artifact=None):
    from v2 import model as numerical_core

    guard = {'version': GUARD_VERSION,
             'source_sha256': {Path(p).name: evidence.sha(p)
                               for p in (__file__, safe_evidence.__file__)},
             'legacy_source_sha256': {Path(m.__file__).name: evidence.sha(m.__file__)
                                     for m in (database, evidence, model, quarterfinals)},
             'shared_source_sha256': {'v2.model': evidence.sha(numerical_core.__file__)},
             'numeric_engine_changed': False}
    if artifact is not None:
        deps = artifact.get('dependencies') or {
            key: {'path': artifact[key + '_path'], 'sha256': artifact[key + '_sha256']}
            for key in ('protocol', 'dataset', 'engine')}
        guard.update(artifact_sha256=evidence.sha(artifact_path), legacy_dependencies=deps)
    return guard


def verified_rows(db):
    rows = database.rows(db)
    for row in rows:
        safe_evidence.verify_row(row)
    return rows


def build(manifest, out):
    out = Path(out)
    if out.exists():
        raise FileExistsError(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.jcup-build-', dir=out.parent) as folder:
        candidate = Path(folder) / 'validated.sqlite'
        database.build(manifest, candidate)
        verified_rows(candidate)
        os.link(candidate, out)  # Exclusive publication on the same filesystem.
    result = database.stats(out)
    result['execution_guard'] = execution_guard()
    return result


def train(db, out, rules=None, protocol=None):
    if Path(out).exists():
        raise FileExistsError(out)
    history = verified_rows(db)
    if rules is not None:
        quarterfinals.prepare(db, rules, protocol)
    else:
        from competition_scope import training_rows
        for cohort in {row['cohort'] for row in history}:
            training_rows([row for row in history if row['cohort'] == cohort])
    guard = execution_guard()
    guard.update(database_sha256=evidence.sha(db), strict_verified_rows=len(history))
    result = (quarterfinals.train(db, rules, protocol, out) if rules is not None
              else model.train(db, out))
    model.write(Path(out) / 'execution-guard.json', guard)
    return result


def load_artifact(path):
    artifact = read(path)
    if artifact.get('role') not in ('current', 'test'):
        raise ValueError('explicit candidate role required')
    if artifact.get('schema') == quarterfinals.SCHEMA:
        deps = artifact['dependencies']
        engines = {'engine': quarterfinals, 'core_engine': model,
                   'evidence_engine': evidence, 'database_engine': database}
        if not {'database', 'certificate', 'protocol', 'dataset', *engines} <= deps.keys():
            raise ValueError('complete QF candidate dependencies required')
    elif artifact.get('schema') == 'WCPP-JCUP-POISSON-1':
        deps = {k: {'path': artifact[k + '_path'], 'sha256': artifact[k + '_sha256']}
                for k in ('protocol', 'dataset', 'engine')}
        engines = {'engine': model}
    else:
        raise ValueError('supported Cup artifact schema required')
    for key, dep in deps.items():
        if evidence.sha(dep['path']) != dep['sha256']:
            raise ValueError('candidate dependency changed: ' + key)
    for key, engine in engines.items():
        if evidence.sha(engine.__file__) != deps[key]['sha256']:
            raise ValueError('executing engine differs from candidate dependency: ' + key)
    return artifact


def validate_quarterfinal(artifact, fixture):
    safe_evidence.validate_target(fixture)
    cert = quarterfinals.certificate(artifact['dependencies']['certificate']['path'])
    quarterfinals.target_identity(fixture, cert)
    if (artifact['leg'] != fixture['leg'] or artifact['core']['fit_end'] >= fixture['date']
            or evidence.aware(artifact['built_at']) > evidence.aware(fixture['cutoff_at'])):
        raise ValueError('candidate leg/training/availability mismatch')
    if fixture.get('first_leg_record'):
        safe_evidence.verify_row(fixture['first_leg_record'])


def predict(path, fixture):
    safe_evidence.validate_target(fixture)
    artifact = load_artifact(path)
    if artifact['schema'] == quarterfinals.SCHEMA:
        validate_quarterfinal(artifact, fixture)
        result = quarterfinals.predict(path, fixture)
    else:
        result = model.predict(path, fixture)
        result['unseen_teams_using_fitted_prior'] = [fixture[side] for side in ('home', 'away')
            if artifact['role'] == 'test' and fixture[side] not in artifact['core']['teams']]
    result['execution_guard'] = execution_guard(path, artifact)
    return result


def validate_pair(artifacts, fixture):
    pair = artifacts[str(fixture['leg'])]
    if set(pair) != {'current', 'test'}:
        raise ValueError('exact independent candidate pair required')
    for role, ref in pair.items():
        if evidence.sha(ref['path']) != ref['sha256']:
            raise ValueError('candidate index hash mismatch')
        artifact = load_artifact(ref['path'])
        if artifact['schema'] != quarterfinals.SCHEMA or artifact['role'] != role:
            raise ValueError('candidate index role/schema mismatch')
        validate_quarterfinal(artifact, fixture)
    return pair


def freeze_pairs(index_path, fixtures_path, out, freeze_root=None):
    """Validate the entire supplied batch and compute both roles before any save."""
    from research.analysis_freeze import encoded, reject_credentials, save

    out = Path(out)
    if out.exists():
        raise FileExistsError(out)
    guard = execution_guard()
    input_hashes = {str(path): evidence.sha(path) for path in (index_path, fixtures_path)}
    index, fixtures = read(index_path), read(fixtures_path)
    if index.get('schema') != quarterfinals.SCHEMA:
        raise ValueError('QF candidate index schema required')
    if not fixtures or len({f['match_id'] for f in fixtures}) != len(fixtures):
        raise ValueError('unique nonempty prospective universe required')
    artifacts = {leg: entry['artifacts'] for leg, entry in index['routes'].items()}
    prepared, deferred = [], []
    for fixture in fixtures:
        pair = validate_pair(artifacts, fixture)
        if fixture['leg'] == 2 and not fixture.get('first_leg_record'):
            deferred.append({'match_id': fixture['match_id'],
                'reason': 'FIRST_LEG_NOT_YET_PLAYED_OR_VERIFIED', 'prediction_issued': False})
        else:
            prepared.append((fixture, {role: predict(ref['path'], fixture) for role, ref in pair.items()}))
    for fixture in fixtures:
        validate_pair(artifacts, fixture)
    if guard != execution_guard() or any(evidence.sha(p) != checksum for p, checksum in input_hashes.items()):
        raise ValueError('batch inputs or execution sources changed during preflight')
    guard.update(index_sha256=input_hashes[str(index_path)], fixtures_sha256=input_hashes[str(fixtures_path)])
    scope = {'fixture_ids': [f['match_id'] for f in fixtures], 'artifacts': artifacts,
             'track': 'jleague-cup-quarterfinal-candidates', 'active_registry_modified': False,
             'selection': 'complete supplied universe; leg2 deferred until first result exists',
             'execution_guard': guard}
    batch = {'match_id': 'jcup-qf-scope-' + database.key(scope)[:16],
             'cutoff_at': max(fixtures, key=lambda f: evidence.aware(f['cutoff_at']))['cutoff_at'],
             'kickoff_at': min(fixtures, key=lambda f: evidence.aware(f['kickoff_at']))['kickoff_at']}
    if evidence.aware(batch['cutoff_at']) >= evidence.aware(batch['kickoff_at']):
        raise ValueError('batch scope cutoff must precede every kickoff')
    preflight = {'fixtures': fixtures, 'results': [result for _, result in prepared],
                 'scope': scope, 'batch': batch}
    encoded(preflight)
    reject_credentials(preflight)
    out.mkdir(parents=True, exist_ok=False)
    kwargs = {'root': freeze_root} if freeze_root else {}
    scope['freeze_receipt'] = save(scope, batch, stage='prematch', track='research-scope', **kwargs)
    model.write(out / 'scope.json', scope)
    receipts = []
    for fixture, result in prepared:
        result.update(scope_freeze_reference=scope['freeze_receipt'], active_registry_modified=False,
                      comprehensive_primary_ready=False, execution_guard=guard)
        receipt = save(result, fixture, stage='prematch', track='jleague-cup-quarterfinal-candidates', **kwargs)
        model.write(out / (database.key(fixture['match_id'])[:16] + '.json'),
                    {'fixture': fixture, 'payload': result, 'freeze_receipt': receipt})
        receipts.append(receipt)
    result = {'scope_freeze': scope['freeze_receipt'], 'receipts': receipts, 'deferred': deferred,
              'execution_guard': guard}
    model.write(out / 'receipts.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    specs = {'build': ('manifest', 'db'), 'stats': ('db',), 'train': ('db', 'out'),
             'train-quarterfinals': ('db', 'rules', 'protocol', 'out'),
             'predict': ('artifact', 'fixture', 'out'), 'freeze-pairs': ('index', 'fixtures', 'out')}
    for name, fields in specs.items():
        command = commands.add_parser(name)
        for field in fields:
            command.add_argument('--' + field, required=True)
        if name == 'freeze-pairs':
            command.add_argument('--freeze-root')
    args = parser.parse_args()
    if args.command == 'build':
        result = build(read(args.manifest), args.db)
    elif args.command == 'stats':
        result = database.stats(args.db)
    elif args.command in ('train', 'train-quarterfinals'):
        result = train(args.db, args.out, getattr(args, 'rules', None), getattr(args, 'protocol', None))
    elif args.command == 'freeze-pairs':
        result = freeze_pairs(args.index, args.fixtures, args.out, args.freeze_root)
    else:
        result = predict(args.artifact, read(args.fixture))
        model.write(args.out, result)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    main()
