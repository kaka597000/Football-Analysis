"""Explicit research-pair routing; never mutates the active model registry.

The overlay is opt-in. Missing research routes fail closed rather than borrowing
another competition's model or presenting an empty prediction as a working pair.
"""
import hashlib
import importlib
import json
from pathlib import Path

SCHEMA = 'WCPP-RESEARCH-PAIR-REGISTRY-1'
ENGINES = {
    'nations_cup': 'nations_cup_pair_adapters',
    'europe': 'europe_pair_adapter',
    'fin_world': 'research_program.fin_world_pair_adapter',
}


def read_registry(path):
    raw = Path(path).read_bytes()
    registry = json.loads(raw)
    if (registry.get('schema') != SCHEMA or not isinstance(registry.get('routes'), dict)
            or registry.get('automatic_promotion') is not False):
        raise ValueError('explicit non-promoting research pair registry required')
    return registry, hashlib.sha256(raw).hexdigest()


def predict_pair(fixture, registry_path):
    from competition_scope import require
    require(fixture)
    registry, checksum = read_registry(registry_path)
    competition = fixture.get('competition') or fixture['cohort'].split(':', 1)[0]
    route = registry['routes'].get(competition)
    if not isinstance(route, dict) or route.get('status') != 'READY_RESEARCH_PAIR':
        raise ValueError('verified research pair is not available for this competition')
    engine = route.get('engine')
    if engine not in ENGINES:
        raise ValueError('unregistered research pair engine')
    result = importlib.import_module(ENGINES[engine]).predict_pair(fixture, route)
    for role in ('current_model', 'test_model'):
        prediction = result.get(role)
        if not isinstance(prediction, dict) or prediction.get('matrix') is None:
            raise ValueError('both real numerical predictions are required')
        import numpy as np
        matrix = np.asarray(prediction['matrix'], dtype=float)
        if (matrix.ndim != 2 or min(matrix.shape) < 2 or not np.isfinite(matrix).all()
                or (matrix < 0).any() or abs(float(matrix.sum()) - 1) > 1e-7):
            raise ValueError('valid normalized score distribution required')
    if read_registry(registry_path)[1] != checksum:
        raise ValueError('research registry changed during inference')
    return {**result, 'research_registry_sha256': checksum,
            'research_registry_schema': SCHEMA, 'automatic_promotion': False,
            'active_model_modified': False,
            'selection_policy': 'INDEPENDENT_RESEARCH_PAIR_NOT_CHAMPION_PROMOTION'}


def predict_any_pair(fixture, *, research_registry_path, active_registry_path):
    """Opt-in current-scope router, preserving every existing active route."""
    from competition_scope import require
    require(fixture)
    active_raw = Path(active_registry_path).read_bytes()
    active = json.loads(active_raw)
    if fixture.get('cohort') in active.get('models', {}):
        from prediction_service import run
        result = run(fixture, active_registry_path)
        if any(result.get(role, {}).get('matrix') is None for role in ('current_model','test_model')):
            raise ValueError('existing route did not produce both predictions')
        if Path(active_registry_path).read_bytes() != active_raw:
            raise ValueError('active registry changed during inference')
        return {**result, 'route_origin': 'EXISTING_ACTIVE_RESEARCH',
                'active_model_modified': False, 'automatic_promotion': False}
    result = predict_pair(fixture, research_registry_path)
    if Path(active_registry_path).read_bytes() != active_raw:
        raise ValueError('active registry changed during research inference')
    return {**result, 'route_origin': 'EXPLICIT_RESEARCH_OVERLAY'}


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture',required=True)
    parser.add_argument('--active-registry',required=True)
    parser.add_argument('--research-registry',required=True)
    parser.add_argument('--out',required=True,help='New diagnostic output; this command does not issue a forecast')
    args=parser.parse_args()
    destination=Path(args.out)
    if destination.exists(): raise FileExistsError(destination)
    fixture=json.loads(Path(args.fixture).read_text())
    result=predict_any_pair(fixture,research_registry_path=args.research_registry,
                            active_registry_path=args.active_registry)
    result['publication_status']='UNPUBLISHED_DIAGNOSTIC_REQUIRES_SEPARATE_PREMATCH_FREEZE'
    with destination.open('x',encoding='utf-8') as handle:
        json.dump(result,handle,ensure_ascii=False,indent=2,allow_nan=False)


if __name__=='__main__':main()
