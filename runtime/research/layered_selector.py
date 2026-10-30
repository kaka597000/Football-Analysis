"""Two selectors on an unchanged matrix; no new evidence or trained model."""
import hashlib
import json
from pathlib import Path
import numpy as np

POLICY_PATH = Path(__file__).with_name('layered_policy.json')
POLICY = json.loads(POLICY_PATH.read_text())


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def select(matrix, *, overflow=0):
    if isinstance(overflow, bool) or not np.isfinite(overflow) or overflow != 0:
        raise ValueError('unresolved overflow cannot identify exact scores')
    m = np.asarray(matrix, dtype=float)
    if m.ndim != 2 or min(m.shape) < 1 or not np.isfinite(m).all() or (m < 0).any():
        raise ValueError('finite nonnegative score matrix required')
    if abs(float(m.sum())-1) > POLICY['mass_tolerance']:
        raise ValueError('matrix must already be normalized; no silent repair')
    h, a = np.indices(m.shape); total = h+a
    masks = [h>a, h==a, h<a]
    directions = [float(m[x].sum()) for x in masks]
    direction = int(np.argmax(directions))
    bands = [(total >= lo) & ((total <= hi) if hi is not None else True)
             for lo, hi in POLICY['total_bands']]
    joint_bands = [float(m[masks[direction] & band].sum()) for band in bands]
    band = int(np.argmax(joint_bands))
    allowed = masks[direction] & bands[band]
    if not allowed.any() or joint_bands[band] <= 0:
        raise ValueError('selected cell must have positive mass')
    joint = tuple(map(int, np.unravel_index(m.argmax(), m.shape)))
    layered = tuple(map(int, np.unravel_index(np.where(allowed, m, -1).argmax(), m.shape)))
    flags = []
    if max(directions) < POLICY['direction_warning_below']:
        flags.append('LOW_CONFIDENCE_DIRECTION')
    mean = float((m*total).sum())
    if abs(sum(layered)-mean) > POLICY['mean_mismatch_warning_above']:
        flags.append('MEAN_MISMATCH')
    return {'version': POLICY['version'], 'status': 'EXPERIMENTAL',
            'policy_sha256': digest(POLICY), 'matrix_sha256': digest(m.tolist()),
            'matrix_axes': ['home_goals', 'away_goals'],
            'joint_top1': list(joint), 'joint_probability': float(m[joint]),
            'layered_pick': list(layered), 'layered_probability': float(m[layered]),
            'direction': POLICY['direction_tie_order'][direction],
            'direction_probabilities': dict(zip(POLICY['direction_tie_order'], directions)),
            'selected_band': POLICY['total_bands'][band],
            'conditional_band_probabilities': [p/directions[direction] for p in joint_bands],
            'expected_total': mean, 'flags': flags,
            'numeric_distribution_changed': False, 'default_primary_changed': False}


def compare_pair(pair):
    result = {'version': POLICY['version'], 'policy_sha256': digest(POLICY),
              'status': 'EXPERIMENTAL', 'primary_override': False, 'models': {}}
    for key in ('current_model', 'test_model'):
        model = pair.get(key, {})
        if model.get('matrix') is None:
            row = {'status': 'ABSTAIN_NO_MATRIX'}
        elif str(model.get('status', '')).startswith('ABSTAIN'):
            row = {'status': 'ABSTAIN_SOURCE_MODEL'}
        else:
            try:
                row = select(model['matrix'], overflow=model.get('overflow', 0))
            except (ValueError, TypeError, OverflowError):
                row = {'status': 'ABSTAIN_INVALID_OR_UNRESOLVED_MATRIX'}
        row['original_primary'] = model.get('primary')
        row['source_model_version'] = model.get('version')
        result['models'][key] = row
    return result
