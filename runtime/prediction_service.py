"""Compose validated numerical predictions and research diagnostics."""
import sys
from pathlib import Path
from prediction_engine import ROOT, file_sha256, object_sha256, source_identity, predict_models

def run(fixture, registry_path=None):
    state = predict_models(fixture, registry_path)
    path = state['path']
    registry = state['registry']
    route = state['route']
    artifact_path = state['artifact_path']
    current_engine_name = state['current_engine_name']
    current_engine_path = state['current_engine_path']
    current = state['current']
    test_engine = state['test_engine']
    test_engine_path = state['test_engine_path']
    test_artifact_path = state['test_artifact_path']
    test_artifact_hash = state['test_artifact_hash']
    experimental = state['experimental']
    support_alignment = state['support_alignment']
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
        'source_identity': source_identity(),
        'fixture_sha256': object_sha256(fixture),
        'entrypoint': str(ROOT / 'predict_active.py'),
        'entrypoint_sha256': file_sha256(ROOT / 'predict_active.py'),
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
    from prediction_diagnostics import attach
    return attach(output, fixture, artifact_path, test_artifact_path)
