"""Read-only score-path diagnostics, with no forced increase in forecast goals.

A joint mode below a mean is a distribution property, not evidence of bias.
A manual score's probability is its mass in the referenced model, never a
calibrated probability for the independent comprehensive judgement.
"""
import re
from datetime import datetime
import json
from pathlib import Path
import numpy as np
try:
    from .team_goal_analysis import summarize_prediction
except ImportError:
    from team_goal_analysis import summarize_prediction

VERSION = 'WCPP-SCORE-DISTRIBUTION-AUDIT-0.1'


def parse_score(value):
    if isinstance(value, str) and re.fullmatch(r'\d+-\d+', value):
        return tuple(map(int, value.split('-')))
    if (isinstance(value, (list, tuple)) and len(value) == 2
            and all(type(x) is int and x >= 0 for x in value)):
        return tuple(value)
    raise ValueError('nonnegative integer home-away score required')


def _score_view(matrix, score):
    h, a = parse_score(score)
    if h >= matrix.shape[0] or a >= matrix.shape[1]:
        return {'score': f'{h}-{a}', 'status': 'OUTSIDE_MATRIX_SUPPORT'}
    p = float(matrix[h, a]); peak = float(matrix.max())
    return {'score': f'{h}-{a}', 'status': 'IN_MATRIX', 'model_probability': p,
            'probability_rank': int(np.sum(matrix > p)) + 1,
            'ties_at_probability': int(np.sum(matrix == p)),
            'relative_probability_to_joint_mode': p / peak,
            'probability_scope': 'REFERENCED_MODEL_NOT_COMPREHENSIVE_CALIBRATION'}


def _best(matrix, totals, predicate):
    mask = predicate(totals)
    if not mask.any() or float(matrix[mask].max()) <= 0:
        return None
    flat = int(np.argmax(np.where(mask, matrix, -1)))
    return _score_view(matrix, (flat // matrix.shape[1], flat % matrix.shape[1]))


def audit_prediction(prediction, comprehensive_primary=None):
    summary = summarize_prediction(prediction)
    if summary['status'] != 'AVAILABLE':
        return {'version': VERSION, 'status': 'UNAVAILABLE',
                'reason': summary['reason'], 'numeric_output_modified': False}
    matrix = np.asarray(prediction['matrix'], dtype=float)
    totals = np.add.outer(np.arange(matrix.shape[0]), np.arange(matrix.shape[1]))
    probabilities = np.bincount(totals.ravel(), weights=matrix.ravel(),
                                minlength=sum(matrix.shape) - 1)
    flat = int(np.argmax(matrix)); joint = (flat // matrix.shape[1], flat % matrix.shape[1])
    mean = summary['home']['mean'] + summary['away']['mean']
    total_mode = int(np.argmax(probabilities))
    flags = []
    if sum(joint) < mean:
        flags.append('JOINT_MODE_BELOW_MEAN_IS_NOT_BY_ITSELF_A_CALIBRATION_ERROR')
    if sum(joint) <= 2 and float(probabilities[3:].sum()) > .5:
        flags.append('LOW_JOINT_MODE_WITH_MAJORITY_MASS_AT_THREE_PLUS')
    result = {'version': VERSION, 'status': 'AVAILABLE',
              'matrix_sha256': summary['source_matrix_sha256'],
              'numeric_output_modified': False, 'primary_reordered': False,
              'total_probabilities': probabilities.tolist(), 'total_mean': mean,
              'total_mode': total_mode, 'joint_mode': f'{joint[0]}-{joint[1]}',
              'joint_mode_total': sum(joint),
              'joint_mode_total_minus_mean': sum(joint) - mean,
              'p_total_le_2': float(probabilities[:3].sum()),
              'p_total_ge_3': float(probabilities[3:].sum()),
              'p_total_ge_4': float(probabilities[4:].sum()),
              'p_home_zero': summary['home']['p0'], 'p_away_zero': summary['away']['p0'],
              'comparison_candidates_not_recommendations': {
                  'joint_mode': _score_view(matrix, joint),
                  'best_at_total_mode': _best(matrix, totals, lambda x: x == total_mode),
                  'best_three_plus': _best(matrix, totals, lambda x: x >= 3),
                  'best_four_plus': _best(matrix, totals, lambda x: x >= 4)},
              'flags': flags, 'comprehensive_selection': {'status': 'NOT_PROVIDED'}}
    train_end = prediction.get('training_data_cutoff')
    fixture = prediction.get('input', {})
    result['history_freshness'] = {
        'training_match_cutoff': train_end,
        'days_from_training_to_fixture': (datetime.fromisoformat(fixture['kickoff_at'][:10])
             - datetime.fromisoformat(train_end[:10])).days if train_end and fixture.get('kickoff_at') else None,
        'latest_available_matches_included': 'NOT_VERIFIED_BY_THIS_DIAGNOSTIC',
        'notice': 'Age alone does not establish missing matches; check verified current schedules.'}
    if comprehensive_primary is not None:
        selected = parse_score(comprehensive_primary); view = _score_view(matrix, selected)
        view.update(total_minus_mean=sum(selected)-mean,
                    home_change_from_joint_mode=selected[0]-joint[0],
                    away_change_from_joint_mode=selected[1]-joint[1],
                    total_change_from_joint_mode=sum(selected)-sum(joint),
                    zero_sides=[side for side, value in zip(('home','away'),selected) if value == 0])
        for side, index in [('home',0),('away',1)]:
            if selected[index] == 0:
                marginal = summary[side]['marginal']
                view[side+'_zero_comparison'] = {
                    'p0': marginal[0], 'p1': marginal[1] if len(marginal)>1 else 0.,
                    'p_ge_2': summary[side]['p_ge_2']}
        result['comprehensive_selection'] = view
    return result


def attach_pair(bundle):
    audits = {k: audit_prediction(bundle[k]) for k in ('current_model','test_model')}
    for k, value in audits.items():
        bundle[k]['score_distribution_audit'] = value
    return bundle


def comprehensive_audit(payload, pair):
    """Check documented comparisons; completeness is not validation of judgement."""
    primary = payload.get('primary')
    if primary is None:
        return {'version': VERSION, 'status': 'ABSTAIN', 'top3_ready': False,
                'missing_comparisons': [], 'reason': 'NO_COMPREHENSIVE_PRIMARY'}
    score = parse_score(primary)
    audits = {k: audit_prediction(pair[k], primary) for k in ('current_model','test_model')}
    available = [a for a in audits.values() if a['status']=='AVAILABLE']
    if not available:
        return {'version': VERSION, 'status':'UNAVAILABLE', 'top3_ready': False,
                'models':audits,'missing_comparisons':['NO_REFERENCED_SCORE_MATRIX']}
    details = payload.get('selection_evidence', {})
    if not isinstance(details, dict) or not isinstance(details.get('zero_vs_one', {}), dict):
        raise ValueError('selection_evidence and zero_vs_one must be objects')
    compared = {parse_score(x) for x in details.get('compared_scores', [])}
    missing = []
    if score not in compared or len(compared) < 2:
        missing.append('AT_LEAST_TWO_EXPLICIT_SCORE_CANDIDATES_INCLUDING_PRIMARY')
    for side,index in [('home',0),('away',1)]:
        if score[index] == 0:
            entry = details.get('zero_vs_one', {}).get(side, {})
            if not isinstance(entry,dict) or not str(entry.get('reason','')).strip() or not str(entry.get('counterevidence','')).strip():
                missing.append(side.upper()+'_ZERO_VS_ONE_SUPPORT_AND_COUNTEREVIDENCE')
    if any('LOW_JOINT_MODE_WITH_MAJORITY_MASS_AT_THREE_PLUS' in a['flags'] for a in available):
        if not any(sum(s)>=3 for s in compared) or not str(details.get('three_plus_path','')).strip():
            missing.append('THREE_PLUS_CANDIDATE_AND_PATH_WHEN_MAJORITY_MASS_IS_HIGHER')
    if payload.get('total_direction') in ['大','小'] and not str(details.get('total_reason','')).strip():
        missing.append('ACTUAL_LINE_DIRECTION_REASON_INDEPENDENT_OF_POINT_SCORE')
    if any(a['comprehensive_selection']['status']!='IN_MATRIX' for a in available):
        missing.append('PRIMARY_OUTSIDE_REFERENCED_MATRIX_SUPPORT')
    return {'version': VERSION, 'status':'NEEDS_COMPARISON' if missing else 'COMPARISONS_DOCUMENTED',
            'top3_ready':not missing,'models':audits,'missing_comparisons':missing,
            'completeness_is_not_accuracy_evidence':True,
            'numeric_output_modified':False,'primary_reordered':False}


def _aware(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('timezone required in referenced freeze')
    return result


def _reference(reference, *, expected_track, match_id, cutoff, verify_record):
    if not isinstance(reference, dict) or not reference.get('path') or not reference.get('sha256'):
        raise ValueError('complete issued freeze reference required')
    record = verify_record(json.loads(Path(reference['path']).read_text()))
    if (record['record_sha256'] != reference['sha256'] or record['track'] != expected_track
            or record['stage'] != 'prematch' or record['match_id'] != match_id):
        raise ValueError('issued reference identity, track or checksum mismatch')
    if _aware(record['recorded_at']) > cutoff or _aware(record['fixture']['cutoff_at']) > cutoff:
        raise ValueError('referenced analysis was unavailable at selection cutoff')
    return record


def audit_issued(payload, fixture, *, stage, track, verify_record):
    """Derive new-record audit metadata, preserving old payloads and their hashes.

    Only new TOP3 selections require documented comparisons. Whole-batch analyses,
    including missing evidence and abstentions, are still frozen honestly.
    """
    if stage != 'prematch' or track not in ('comprehensive', 'top3-selection'):
        return None
    cutoff = _aware(fixture['cutoff_at'])
    if track == 'comprehensive':
        reference = payload.get('current_test_reference')
        if reference is None:
            return {'version': VERSION, 'status': 'UNAVAILABLE', 'top3_ready': False,
                    'reason': 'NO_VERIFIED_CURRENT_TEST_FREEZE_REFERENCE'}
        record = _reference(reference, expected_track='current-test-pair',
                            match_id=fixture['match_id'], cutoff=cutoff, verify_record=verify_record)
        if _aware(record['fixture']['kickoff_at']) != _aware(fixture['kickoff_at']):
            raise ValueError('paired reference kickoff mismatch')
        return comprehensive_audit(payload, record['payload'])
    items = payload.get('top3')
    if not isinstance(items, list) or len(items) > 3:
        raise ValueError('TOP3 requires a list of at most three issued selections')
    seen = set()
    for rank, item in enumerate(items, 1):
        match_id = item['match_id']
        if item.get('rank') != rank or match_id in seen:
            raise ValueError('TOP3 ranks must be consecutive with unique matches')
        seen.add(match_id)
        record = _reference(item.get('comprehensive_reference'), expected_track='comprehensive',
                            match_id=match_id, cutoff=cutoff, verify_record=verify_record)
        if not record.get('selection_audit', {}).get('top3_ready'):
            raise ValueError('TOP3 requires resolved score/zero-goal/total comparisons; freeze the full analysis first')
        for key in ('primary', 'wdl', 'total_line', 'total_direction'):
            if item.get(key) != record['payload'].get(key):
                raise ValueError('TOP3 recommendation differs from referenced comprehensive freeze: '+key)
        if _aware(fixture['kickoff_at']) > _aware(record['fixture']['kickoff_at']):
            raise ValueError('batch kickoff must not follow selected match kickoff')
    return {'version': VERSION, 'status': 'COMPARISONS_DOCUMENTED', 'n': len(items),
            'completeness_is_not_accuracy_evidence': True, 'numeric_output_modified': False}
