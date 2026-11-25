"""Attach evidence and diagnostics without changing numerical predictions."""
import json
from datetime import date

def attach_kpi_references(output, fixture, test_artifact):
    """Expose the new evaluation objective and the feature inputs actually used."""
    from research_program.future_validation import quote_audit
    from research_program.three_arm_model import score_picks
    from research.analysis_freeze import aware

    quote = quote_audit(fixture)
    total = {'eligible': quote['eligible'], 'reasons': quote['reasons'], 'models': {}}
    if quote['eligible']:
        for key in ('current_model', 'test_model'):
            prediction = output[key]
            matrix = prediction.get('matrix')
            if matrix is not None:
                pick = score_picks(matrix, line=quote['quote']['line'])['total']
                total['models'][key] = {
                    'strict_full_win_direction': pick['direction'],
                    'original_direction': (prediction.get('total') or {}).get('reference_direction'),
                    'line': pick['line'],
                }
    output['strict_total_reference'] = total
    native = output['test_model']
    features = test_artifact.get('feature_names')
    rows = (native.get('ml') or {}).get('feature_values')
    end = test_artifact.get('train_end')
    if not (features and rows and end):
        return
    names = ('own_recent_xg', 'opponent_recent_xg', 'own_same_cohort_gap', 'opponent_same_cohort_gap')
    if not all(name in features for name in names):
        return
    columns = {name: [row[features.index(name)] for row in rows] for name in names}
    output['test_input_freshness'] = {
        'training_end': end,
        'days_from_training_end_to_kickoff': (aware(fixture['kickoff_at']).date() - date.fromisoformat(end)).days,
        'training_xg_match_count': test_artifact.get('feature_xg_match_count'),
        'model_inputs': columns,
        'verified_recent_form_supplied': fixture.get('recent_league_form') is not None,
        'recent_form_numeric_scope': 'INDEPENDENT_CANDIDATE',
    }


def attach(output, fixture, artifact_path, test_artifact_path):
    # The programmatic entry and CLI expose the same unchanged-matrix diagnostics.
    from research.team_goal_analysis import attach_pair as attach_goals
    from research.score_distribution_audit import attach_pair as attach_audit
    attach_goals(output)
    attach_audit(output)
    from research.layered_selector import compare_pair
    output['selection_experiment'] = compare_pair(output)
    from research.market_movement import analyze as analyze_market
    output['market_movement'] = analyze_market(fixture)
    from research_program.market_baseline import predict as predict_market_baseline
    output['market_baseline'] = predict_market_baseline(fixture)
    from research.match_analysis import build as build_match_analysis
    test_artifact = (json.loads(test_artifact_path.read_text()) if test_artifact_path
                     and output['test_model'].get('status') != 'ABSTAIN_TEST_EXECUTION_FAILED' else {})
    attach_kpi_references(output, fixture, test_artifact)
    output['match_analysis'] = build_match_analysis(fixture, output,
        current_artifact=json.loads(artifact_path.read_text()), test_artifact=test_artifact)
    snapshot = fixture.get('recent_league_form')
    if snapshot is None and all(isinstance(fixture.get(s+'_form'), dict) and
                                'matches' in fixture[s+'_form'] and 'team_id' in fixture[s+'_form'] for s in ('home','away')):
        snapshot = {'match_id':fixture['match_id'], 'cohort':fixture['cohort'],
                    'home':fixture['home_form'], 'away':fixture['away_form']}
    if snapshot is not None:
        from research_program.live_score_form import attach_candidate
        attach_candidate(output, fixture, test_artifact, snapshot)
    return output
