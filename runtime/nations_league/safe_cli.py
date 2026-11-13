"""Validated entry point around immutable Nations League research engines.

Historical engines and candidate hashes stay unchanged. Every publishable pair
is computed before the first freeze; this is business preflight, not a filesystem
transaction across freeze records.
"""
import argparse
import json
from pathlib import Path

from . import database, model


GUARD_VERSION = 'WCPP-NATIONS-EXECUTION-GUARD-1'
TARGET_LABELS = frozenset('''actual actual_score actual_result result results outcome
target target_score target_result score scores scoreline goals final_score final_result score90
score_90 result_90 regulation_score full_time_score half_time_score ft_score ht_score h90 a90
home_goals away_goals home_score away_score home_90 away_90 home_et away_et
home_pen away_pen home_penalties away_penalties home_total away_total
home_score_90 away_score_90 home_goals_90 away_goals_90 home_regulation_goals away_regulation_goals
homegoals awaygoals homescore awayscore'''.split())


def execution_guard():
    from research_program import nations_phase
    from v2 import model as numerical_core

    return {'version': GUARD_VERSION, 'historical_core_unchanged': True,
            'business_preflight_before_freezing': True,
            'cross_file_transaction': False,
            'sources': {name: {'path': str(Path(module.__file__).resolve()),
                               'sha256': model.sha(module.__file__)}
                        for name, module in (
                            ('legacy_engine', model), ('database', database),
                            ('phase_verifier', nations_phase), ('numerical_core', numerical_core))},
            'validator': {'path': str(Path(__file__).resolve()), 'sha256': model.sha(__file__)}}


def validate_future(fixture):
    if not isinstance(fixture, dict):
        raise ValueError('fixture object required')
    normalized = {name.replace('_', '') for name in TARGET_LABELS}
    labels = {str(k) for k in fixture
              if str(k).lower().replace('-', '').replace('_', '') in normalized}
    if labels:
        raise ValueError('target labels prohibited: ' + ','.join(sorted(labels)))
    event_id = str(fixture.get('source_event_id', ''))
    if not event_id.isdigit() or fixture.get('match_id') != 'espn:' + event_id:
        raise ValueError('match_id must identify the verified source event')
    if fixture.get('entity', 'national') != 'national':
        raise ValueError('national team identity required')
    model.validate_future(fixture)


def predict(path, fixture, expected_role=None):
    validate_future(fixture)
    artifact = model.read(path)
    if (artifact.get('schema') != 'WCPP-NATIONS-POISSON-1'
            or artifact.get('competition') != 'uefa.nations'
            or artifact.get('league_level') != fixture['league_level']
            or artifact.get('stage') != fixture['stage']
            or artifact.get('role') not in ('current', 'test')
            or (expected_role is not None and artifact['role'] != expected_role)):
        raise ValueError('exact candidate role/competition/league/stage required')
    if (Path(artifact['engine_path']).resolve() != Path(model.__file__).resolve()
            or artifact['engine_sha256'] != model.sha(model.__file__)):
        raise ValueError('candidate must bind the executed historical engine')
    if artifact['core'].get('train_end') != artifact.get('train_end'):
        raise ValueError('candidate training boundary contradicts fitted core')
    result = model.predict(path, fixture)
    result['execution_guard'] = execution_guard()
    return result


def freeze_pairs(index_path, fixtures, out, freeze_root):
    from research.analysis_freeze import encoded, reject_credentials

    index = model.read(index_path)
    if index.get('schema') != 'WCPP-NATIONS-PAIRED-CANDIDATES-1':
        raise ValueError('paired Nations candidate index required')
    if not isinstance(fixtures, list) or not fixtures:
        raise ValueError('nonempty complete future population required')
    for fixture in fixtures:
        validate_future(fixture)
    ids = [f['match_id'] for f in fixtures]
    if len(set(ids)) != len(ids):
        raise ValueError('unique complete future population required')
    out = Path(out)
    if out.exists():
        raise FileExistsError(out)
    index_hash = model.sha(index_path)
    guard = execution_guard()
    prepared = []
    for original in fixtures:
        fixture = {**original, 'cutoff_at': model.now()}
        route = index['routes'][fixture['league_level']]
        pair = {}
        for role in ('current', 'test'):
            ref = route[role]
            if model.sha(ref['path']) != ref['sha256']:
                raise ValueError('candidate index checksum mismatch')
            pair[role] = predict(ref['path'], fixture, expected_role=role)
        prepared.append((fixture, pair))
    if model.sha(index_path) != index_hash or execution_guard() != guard:
        raise ValueError('execution dependencies changed during batch preflight')
    # Recheck source identity/time for all records after the full computation.
    for fixture, _ in prepared:
        validate_future(fixture)
        for role in ('current', 'test'):
            ref = index['routes'][fixture['league_level']][role]
            if model.sha(ref['path']) != ref['sha256']:
                raise ValueError('candidate changed during batch preflight')
    protocol = {'schema': 'WCPP-NATIONS-FORWARD-PLAN-2', 'created_at': model.now(),
                'index_path': str(Path(index_path).resolve()), 'index_sha256': index_hash,
                'routes': index['routes'], 'fixture_population': [f for f, _ in prepared],
                'execution_guard': guard, 'minimum_completed_per_level': 30,
                'alpha': .05, 'primary_coverage': 1.,
                'selection_rule': 'all supplied future IDs; no post-result model selection',
                'source_and_actual_market_audits_required': True, 'automatic_activation': False}
    shell = {'match_id': 'nations-forward-plan:' + index_hash, 'cohort': 'uefa.nations',
             'cutoff_at': model.now(),
             'kickoff_at': min((f['kickoff_at'] for f, _ in prepared), key=model.aware)}
    publication = {'protocol': protocol, 'prepared': [
        {'fixture': fixture, 'payload': pair} for fixture, pair in prepared]}
    reject_credentials(publication)
    encoded(publication)
    out.mkdir(parents=True, exist_ok=False)
    receipt = model.save(protocol, shell, stage='review', track='research-preregistration', root=freeze_root)
    model.write(out / 'protocol.json', protocol)
    model.write(out / 'plan-receipt.json', receipt)
    receipts = []
    for fixture, pair in prepared:
        pair.update(plan_sha256=receipt['sha256'], formal_kpi_eligible=False,
                    active_route=False, execution_guard=guard)
        record = model.save(pair, fixture, stage='prematch', track='nations-paired-candidates',
                            root=freeze_root,
                            idempotency_key='nations-pair:' + receipt['sha256'] + ':' + fixture['match_id'])
        model.write(out / (fixture['match_id'].replace(':', '-') + '.json'), pair)
        receipts.append(record)
    model.write(out / 'receipts.json', receipts)
    return {'frozen': len(receipts), 'plan': receipt, 'automatic_activation': False,
            'execution_guard': guard}


def train(db, out):
    guard = execution_guard()
    result = model.train(db, out)
    model.write(Path(out) / 'execution-guard.json', guard)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    train_parser = sub.add_parser('train')
    for name in ('db', 'out'):
        train_parser.add_argument('--' + name, required=True)
    predict_parser = sub.add_parser('predict')
    for name in ('artifact', 'fixture', 'out'):
        predict_parser.add_argument('--' + name, required=True)
    freeze_parser = sub.add_parser('freeze-pairs')
    for name in ('index', 'fixtures', 'out'):
        freeze_parser.add_argument('--' + name, required=True)
    freeze_parser.add_argument('--freeze-root', default='/workspace/WCPP/forecasts/issued-analyses')
    args = parser.parse_args()
    if args.command == 'train':
        result = train(args.db, args.out)
        print(json.dumps({'trained_routes': len(result['routes']), 'activation_allowed': False}))
    elif args.command == 'predict':
        model.write(args.out, predict(args.artifact, model.read(args.fixture)))
        print(args.out)
    else:
        print(json.dumps(freeze_pairs(args.index, model.read(args.fixtures), args.out, args.freeze_root)))


if __name__ == '__main__':
    main()
