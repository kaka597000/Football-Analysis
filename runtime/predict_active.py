"""The default WCPP forecast entry point; reads the persisted active-model map."""
import argparse
import hashlib
import json
import os
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


def run(fixture, registry_path=None):
    path = Path(registry_path or os.environ.get('WCPP_ACTIVE_MODEL',
                    '/workspace/WCPP/runtime/active-model.json'))
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
    sys.path.insert(0,str(ROOT/'v6'))
    from five_role_review import build_packets
    if experimental.get('matrix') is not None:
        experimental['five_role_review'] = build_packets(fixture,experimental)
    else:
        experimental['five_role_review'] = {
            'version': 'WCPP-FIVE-REVIEW-0.1', 'status': 'ABSTAIN_NO_TEST_MATRIX',
            'probability_fusion_applied': False, 'numerical_prediction_unchanged': True}
    sys.path.insert(0, str(ROOT / 'upset'))
    from classifier import attach as upset_attach
    try:
        upset = upset_attach(fixture)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        upset = {'version': 'WCPP-UPSET-0.1', 'match_id': fixture['match_id'],
                 'status': 'ABSTAIN_INVALID_CLASSIFIER_INPUT_OR_ARTIFACT',
                 'reason': type(exc).__name__, 'main_score_reordered': False,
                 'formal_kpi_eligible': False}
    sys.path.insert(0, str(ROOT))
    try:
        from upset_v2.predict import run as candidate_run
        candidate = candidate_run(fixture)
    except (ValueError, KeyError, TypeError, OSError, ImportError, RuntimeError) as exc:
        candidate = {'version': 'WCPP-UPSET-0.2', 'match_id': fixture['match_id'],
                     'status': 'ABSTAIN_INVALID_CANDIDATE_INPUT_OR_ARTIFACT',
                     'reason': type(exc).__name__, 'main_score_reordered': False,
                     'formal_kpi_eligible': False}
    output = {'match_id': fixture['match_id'], 'activation_registry': str(path),
            'activation_version': registry['version'], 'current_model': current,
            'test_model': experimental, 'upset_classifier': upset,
            'upset_candidate': candidate,
            'route_key': fixture['cohort'],
            'matrix_support_alignment': support_alignment,
            'selection_policy': 'INDEPENDENT_PAIRED_RESEARCH_NO_BEST_OF_TWO',
            'formal_kpi_eligible': False}
    output['execution_audit'] = {
        'schema': 'WCPP-PREDICTION-EXECUTION-AUDIT-1',
        'fixture_sha256': object_sha256(fixture),
        'entrypoint': str(Path(__file__).resolve()),
        'entrypoint_sha256': file_sha256(__file__),
        'registry': str(path.resolve()),
        'registry_sha256': file_sha256(path),
        'current': {
            'engine': current_engine_name,
            'engine_sha256': file_sha256(current_engine_path),
            'artifact': str(artifact_path.resolve()),
            'artifact_sha256': route['artifact_sha256'],
            'reported_version': current.get('version'),
        },
        'test': {
            'engine': test_engine,
            'engine_sha256': file_sha256(test_engine_path),
            'artifact': str(test_artifact_path.resolve()) if test_artifact_path else str(artifact_path.resolve()),
            'artifact_sha256': test_artifact_hash or route['artifact_sha256'],
            'reported_version': experimental.get('version'),
        },
        'prediction_values_modified': False,
        'matrix_support_alignment': support_alignment,
        'selection_policy': 'INDEPENDENT_PAIRED_RESEARCH_NO_BEST_OF_TWO',
    }
    # The programmatic entry and CLI expose the same unchanged-matrix diagnostics.
    from research.team_goal_analysis import attach_pair as attach_goals
    from research.score_distribution_audit import attach_pair as attach_audit
    attach_goals(output)
    attach_audit(output)
    return output


def main():
    p = argparse.ArgumentParser()
    p.add_argument('fixture'); p.add_argument('--out', required=True)
    p.add_argument('--registry')
    p.add_argument('--freeze-root', default='/workspace/WCPP/forecasts/issued-analyses')
    p.add_argument('--stage', choices=['prematch', 'live'], default='prematch')
    a = p.parse_args()
    fixture = json.loads(Path(a.fixture).read_text())
    # Reject an existing output before producing any new freeze revision.
    if Path(a.out).exists():
        raise FileExistsError(a.out)
    output = run(fixture, a.registry)
    from research.team_goal_analysis import attach_pair
    if any('team_goal_analysis' not in output[k] for k in ('current_model','test_model')):
        attach_pair(output)
    from research.score_distribution_audit import attach_pair as attach_audit
    if any('score_distribution_audit' not in output[k] for k in ('current_model','test_model')):
        attach_audit(output)
    from research.analysis_freeze import save
    # One atomic paired snapshot preserves both independent model outputs.
    output['freeze_receipt'] = save(output, fixture, stage=a.stage,
                                   track='current-test-pair', root=a.freeze_root)
    with Path(a.out).open('x') as f:
        json.dump(output, f, ensure_ascii=False, indent=2, allow_nan=False)
    print(a.out)


if __name__ == '__main__': main()
