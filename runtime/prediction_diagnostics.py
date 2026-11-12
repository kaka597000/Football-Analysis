"""Attach evidence and diagnostics without changing numerical predictions."""
import json

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
    from research.match_analysis import build as build_match_analysis
    output['match_analysis'] = build_match_analysis(fixture, output,
        current_artifact=json.loads(artifact_path.read_text()),
        test_artifact=json.loads(test_artifact_path.read_text()) if (test_artifact_path
            and output['test_model'].get('status') != 'ABSTAIN_TEST_EXECUTION_FAILED') else {})
    return output
