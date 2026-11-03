"""Numerical routing and integrity checks; no publication or evidence execution."""
import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'v5'))
from weighted_model import predict, legacy


def file_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def object_sha256(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(',', ':'), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def source_identity():
    """Attach the real checkout identity; never invent a version outside Git."""
    try:
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT.parent,
                                         stderr=subprocess.DEVNULL, text=True, timeout=2).strip()
        dirty = bool(subprocess.check_output(['git', 'status', '--porcelain'],
                     cwd=ROOT.parent, stderr=subprocess.DEVNULL, timeout=2))
        return {'commit': commit, 'worktree_dirty': dirty, 'status': 'GIT_IDENTIFIED'}
    except (OSError, subprocess.SubprocessError):
        return {'commit': None, 'worktree_dirty': None, 'status': 'GIT_UNAVAILABLE'}


def predict_models(fixture, registry_path=None):
    from runtime_config import registry_path as resolve_registry
    path = resolve_registry(registry_path)
    registry = json.loads(path.read_text())
    if registry.get('status') != 'ACTIVE_RESEARCH':
        raise ValueError('active research registry unavailable')
    route = registry['models'].get(fixture['cohort'])
    if route is None:
        raise ValueError('no validated identity/history model for this cohort')
    artifact_path = Path(route['artifact_path'])
    if hashlib.sha256(artifact_path.read_bytes()).hexdigest() != route['artifact_sha256']:
        raise ValueError('active artifact checksum mismatch')
    current_engine_name = route.get('current_engine', 'legacy_weighted_anchor')
    current_engine_path = ROOT / 'v5' / 'weighted_model.py'
    if current_engine_name == 'legacy_weighted_anchor':
        artifact = json.loads(artifact_path.read_text())
        current = legacy.predict(artifact['anchor'], fixture)
        current['training_data_cutoff'] = artifact['anchor'].get('train_end')
    else:
        from active_route_engines import (predict_v4_compat,
            predict_generic_poisson)
        current_engine_path = ROOT / 'active_route_engines.py'
        if current_engine_name == 'v4_main_compat':
            current = predict_v4_compat(artifact_path, fixture)
        elif current_engine_name == 'generic_poisson':
            current = predict_generic_poisson(artifact_path, fixture)
        else:
            raise ValueError('unknown current route engine: ' + current_engine_name)
    current['model_label'] = '当前模型'
    test_engine = 'v5.weighted_model.predict'
    test_engine_path = ROOT / 'v5' / 'weighted_model.py'
    test_artifact_path = None
    test_artifact_hash = None
    if route.get('test_artifact_path'):
        ml_path = Path(route['test_artifact_path'])
        if hashlib.sha256(ml_path.read_bytes()).hexdigest() != route['test_artifact_sha256']:
            raise ValueError('ML artifact checksum mismatch')
        test_artifact_path = ml_path
        test_artifact_hash = route['test_artifact_sha256']
        explicit_test_engine = route.get('test_engine')
        if explicit_test_engine:
            from active_route_engines import (predict_aus_expert,
                predict_generic_poisson, predict_systematic_candidate)
            test_engine_path = ROOT / 'active_route_engines.py'
            if explicit_test_engine == 'systematic_candidate':
                test_engine = 'active_route_engines.predict_systematic_candidate'
                experimental = predict_systematic_candidate(ml_path, fixture)
            elif explicit_test_engine == 'aus_expert':
                test_engine = 'active_route_engines.predict_aus_expert'
                experimental = predict_aus_expert(ml_path, fixture)
            elif explicit_test_engine == 'generic_poisson':
                test_engine = 'active_route_engines.predict_generic_poisson'
                experimental = predict_generic_poisson(ml_path, fixture)
            else:
                raise ValueError('unknown test route engine: ' + explicit_test_engine)
        elif route.get('test_version') == 'WCPP-ML-UCL-0.1':
            sys.path.insert(0,str(ROOT/'continental'))
            from ucl_ml import predict as ml_predict
            test_engine = 'continental.ucl_ml.predict'
            test_engine_path = ROOT / 'continental' / 'ucl_ml.py'
            ml_artifact = json.loads(ml_path.read_text())
            experimental = ml_predict(ml_artifact,current,fixture)
            experimental['training_data_cutoff'] = ml_artifact.get('train_end')
        else:
            sys.path.insert(0,str(ROOT/'v6'))
            from ml_model import predict as ml_predict
            test_engine = 'v6.ml_model.predict'
            test_engine_path = ROOT / 'v6' / 'ml_model.py'
            ml_artifact = json.loads(ml_path.read_text())
            experimental = ml_predict(ml_artifact,current,fixture)
            experimental['training_data_cutoff'] = ml_artifact.get('train_end')
    else:
        if current_engine_name != 'legacy_weighted_anchor':
            raise ValueError('explicit current engine requires an independent test artifact')
        experimental = predict(artifact, fixture, variant='test')
        experimental['training_data_cutoff'] = artifact['anchor'].get('train_end')
    experimental['model_label'] = 'test模型'
    from active_route_engines import align_matrix_support
    support_alignment = align_matrix_support(current, experimental)
    return {
        'path': path,
        'registry': registry,
        'route': route,
        'artifact_path': artifact_path,
        'current_engine_name': current_engine_name,
        'current_engine_path': current_engine_path,
        'current': current,
        'test_engine': test_engine,
        'test_engine_path': test_engine_path,
        'test_artifact_path': test_artifact_path,
        'test_artifact_hash': test_artifact_hash,
        'experimental': experimental,
        'support_alignment': support_alignment,
    }
