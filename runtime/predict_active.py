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
    artifact = json.loads(artifact_path.read_text())
    current = legacy.predict(artifact['anchor'], fixture)
    current['model_label'] = '当前模型'
    current['training_data_cutoff'] = artifact['anchor'].get('train_end')
    if route.get('test_artifact_path'):
        ml_path = Path(route['test_artifact_path'])
        if hashlib.sha256(ml_path.read_bytes()).hexdigest() != route['test_artifact_sha256']:
            raise ValueError('ML artifact checksum mismatch')
        if route.get('test_version') == 'WCPP-ML-UCL-0.1':
            sys.path.insert(0,str(ROOT/'continental'))
            from ucl_ml import predict as ml_predict
        else:
            sys.path.insert(0,str(ROOT/'v6'))
            from ml_model import predict as ml_predict
        ml_artifact = json.loads(ml_path.read_text())
        experimental = ml_predict(ml_artifact,current,fixture)
        experimental['training_data_cutoff'] = ml_artifact.get('train_end')
    else:
        experimental = predict(artifact, fixture, variant='test')
        experimental['training_data_cutoff'] = artifact['anchor'].get('train_end')
    sys.path.insert(0,str(ROOT/'v6'))
    from five_role_review import build_packets
    experimental['five_role_review'] = build_packets(fixture,experimental)
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
            'selection_policy': 'INDEPENDENT_PAIRED_RESEARCH_NO_BEST_OF_TWO',
            'formal_kpi_eligible': False}
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
