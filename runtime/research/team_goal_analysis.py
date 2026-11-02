"""Model-implied team goals from an unchanged joint score matrix.

This is descriptive output, not provider xG or empirical calibration. The
equal-tail integer interval is not a hard scoring boundary or a single-match
guarantee. No model weights, score ranking, or Primary evaluation are changed.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np

VERSION = 'WCPP-TEAM-GOALS-0.1'
CALIBRATION = 'MODEL_IMPLIED_NOT_EMPIRICALLY_CALIBRATED'
NORMALIZATION_TOLERANCE = 1e-9
QUANTILE_TIE_TOLERANCE = 4 * math.ulp(1.)


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _coverage(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('coverage must be a finite number strictly between 0 and 1')
    if not math.isfinite(value) or not 0 < value < 1:
        raise ValueError('coverage must be a finite number strictly between 0 and 1')
    return float(value)


def _quantile(probabilities, q):
    # Smallest integer with CDF >= q; relative tolerance resolves float ties.
    for index in range(len(probabilities)):
        cumulative = math.fsum(probabilities[:index + 1])
        if cumulative >= q or math.isclose(cumulative, q,
                                           rel_tol=QUANTILE_TIE_TOLERANCE, abs_tol=0):
            return index
    return len(probabilities) - 1


def _team(probabilities, coverage):
    tail = (1 - coverage) / 2
    lower = _quantile(probabilities, tail)
    upper = _quantile(probabilities, 1 - tail)
    return {
        'mean': math.fsum(i * p for i, p in enumerate(probabilities)),
        'central_interval': {
            'lower': lower, 'upper': upper, 'nominal_coverage': coverage,
            'width': upper - lower,
            'lower_quantile': tail, 'upper_quantile': 1 - tail,
            'matrix_coverage': math.fsum(probabilities[lower:upper + 1]),
            'method': 'EQUAL_TAIL_INTEGER_INVERSE_CDF_INCLUSIVE',
        },
        'p0': probabilities[0],
        'p_ge_2': math.fsum(probabilities[2:]),
        'p_ge_3': math.fsum(probabilities[3:]),
        'marginal': probabilities,
    }


def summarize_matrix(matrix, coverage=.8):
    """Rows index home goals (axis 0); columns index away goals (axis 1).

    Reject invalid/non-normalized matrices, without rescaling. Normalization
    tolerates absolute floating-point error <= 1e-9; values stay unchanged.
    """
    coverage = _coverage(coverage)
    try:
        original = np.asarray(matrix)
    except (TypeError, ValueError) as exc:
        raise ValueError('rectangular numeric 2D score matrix required') from exc
    if original.ndim != 2 or not all(original.shape) or original.dtype.kind not in 'fiu':
        raise ValueError('nonempty numeric 2D score matrix required')
    values = original.astype(float)
    if not np.isfinite(values).all() or (values < 0).any():
        raise ValueError('finite nonnegative score probabilities required')
    try:
        mass = math.fsum(values.ravel())
    except OverflowError as exc:
        raise ValueError('score matrix probability mass overflow') from exc
    if not math.isclose(mass, 1, rel_tol=0, abs_tol=NORMALIZATION_TOLERANCE):
        raise ValueError('score matrix must already sum to 1; no normalization applied')
    home = [math.fsum(row) for row in values]
    away = [math.fsum(column) for column in values.T]
    return {
        'version': VERSION, 'status': 'AVAILABLE', 'calibration_status': CALIBRATION,
        'source_matrix_sha256': _digest(original.tolist()),
        'matrix_shape': list(values.shape), 'matrix_mass': mass,
        'matrix_axes': {'home_goals': 0, 'away_goals': 1},
        'quantile_tie_relative_tolerance': QUANTILE_TIE_TOLERANCE,
        'interpretation': 'Model-implied means and intervals; not provider xG, '
                          'empirical historical coverage, hard boundaries, or a match guarantee.',
        'home': _team(home, coverage), 'away': _team(away, coverage),
    }


def summarize_prediction(prediction, coverage=.8):
    """ABSTAIN or absent matrix stays unavailable; never invent a distribution."""
    _coverage(coverage)
    if not isinstance(prediction, dict):
        raise ValueError('prediction object required')
    status = str(prediction.get('status', ''))
    reason = ('MODEL_ABSTAIN' if status.upper().startswith('ABSTAIN') else
              'MISSING_SCORE_MATRIX' if prediction.get('matrix') is None else None)
    if reason:
        return {'version': VERSION, 'status': 'UNAVAILABLE', 'reason': reason,
                'model_status': prediction.get('status'), 'calibration_status': CALIBRATION}
    result = summarize_matrix(prediction['matrix'], coverage)
    if prediction.get('matrix_hash') is not None:
        if prediction['matrix_hash'] != result['source_matrix_sha256']:
            raise ValueError('prediction matrix_hash does not match source matrix')
    return result


def attach_pair(bundle, coverage=.8):
    """Append independently derived summaries to an unfrozen current/test pair."""
    summaries = {key: summarize_prediction(bundle[key], coverage)
                 for key in ('current_model', 'test_model')}
    for key, summary in summaries.items():
        bundle[key]['team_goal_analysis'] = summary
    return bundle


def review(summary, *, home_goals, away_goals):
    """Review actual 90-minute team goals separately; no Top1 metric is changed.

    Caller is responsible for verified regulation-90 results and pairing the
    summary with the correct match/model/version. Error is forecast minus actual.
    """
    for actual in (home_goals, away_goals):
        if type(actual) is not int or actual < 0:
            raise ValueError('actual goals must be nonnegative integers')
    if summary.get('status') == 'UNAVAILABLE':
        return {'version': VERSION, 'status': 'UNAVAILABLE', 'reason': summary['reason']}
    if summary.get('version') != VERSION or summary.get('status') != 'AVAILABLE':
        raise ValueError('available team goal summary required')
    result = {'version': VERSION, 'status': 'REVIEWED',
              'source_matrix_sha256': summary['source_matrix_sha256']}
    for side, actual in (('home', home_goals), ('away', away_goals)):
        forecast = summary[side]
        error = forecast['mean'] - actual
        interval = forecast['central_interval']
        result[side] = {'actual_goals': actual, 'mean_error': error,
                        'absolute_error': abs(error), 'squared_error': error ** 2,
                        'interval_hit': interval['lower'] <= actual <= interval['upper'],
                        'interval_width': interval['upper'] - interval['lower'],
                        'nominal_coverage': interval['nominal_coverage']}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', help='prediction, current/test pair, or issued freeze JSON')
    parser.add_argument('--out', required=True, help='new sidecar JSON; input is never modified')
    parser.add_argument('--coverage', type=float, default=.8)
    args = parser.parse_args()
    source = Path(args.input)
    target = Path(args.out)
    if source.resolve() == target.resolve() or target.exists():
        raise FileExistsError(target)
    raw = source.read_bytes()
    data = json.loads(raw)
    payload = data.get('payload', data)
    if 'record_sha256' in data:
        # Reading an old issued freeze is allowed; verify without revising it.
        if __package__:
            from .analysis_freeze import verify
        else:
            from analysis_freeze import verify
        verify(data)
    if 'current_model' in payload or 'test_model' in payload:
        summaries = {key: summarize_prediction(payload[key], args.coverage)
                     for key in ('current_model', 'test_model')}
    else:
        summaries = summarize_prediction(payload, args.coverage)
    sidecar = {'version': VERSION, 'source_file': str(source),
               'source_file_sha256': hashlib.sha256(raw).hexdigest(),
               'scope': 'DERIVED_SIDECAR_NOT_AN_ORIGINAL_PREMATCH_FREEZE',
               'team_goal_analysis': summaries}
    with target.open('x') as handle:
        json.dump(sidecar, handle, ensure_ascii=False, indent=2, allow_nan=False)
    print(target)


if __name__ == '__main__':
    main()
