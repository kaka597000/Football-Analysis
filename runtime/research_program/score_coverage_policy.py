"""Exact-score selection with independently testable backup coverage."""

import math
import re

import numpy as np

from research.analysis_freeze import aware
from .future_validation import quote_audit
from .three_arm_model import _matrix


VERSION = 'WCPP-SCORE-COVERAGE-1'
POLICIES = ('probability', 'diverse')


def _validated_pool(matrices):
    tracks = [_matrix(v) for v in matrices]
    if not tracks or len({m.shape for m in tracks}) != 1 or tracks[0].size < 3:
        raise ValueError('at least one PMF on a common support of three scores is required')
    return np.stack(tracks)


def _score_index(score, shape):
    if not isinstance(score, str) or not re.fullmatch(r'(0|[1-9]\d*)-(0|[1-9]\d*)', score):
        raise ValueError('canonical exact score required')
    home, away = (int(value) for value in score.split('-'))
    if home >= shape[0] or away >= shape[1]:
        raise ValueError('primary score outside probability support')
    return np.ravel_multi_index((home, away), shape)


def _path_evidence(matrices, fixture, minimum_path_mass):
    home, away = np.indices(matrices.shape[1:])
    goals = home + away
    line_audit = quote_audit(fixture or {})
    line = None
    issues = list(line_audit['reasons'])
    if line_audit['eligible']:
        try:
            if (fixture.get('score_basis', 'regulation_90') != 'regulation_90'
                    or aware(fixture['cutoff_at']) >= aware(fixture['kickoff_at'])):
                raise ValueError('quote requires a prematch cutoff')
            line = float(line_audit['quote']['line'])
        except (TypeError, KeyError, ValueError, AttributeError):
            issues.append('TOTAL_PATH_SCOPE_OR_CUTOFF_CONFLICT')
    # Without a verified line, every input must support a high-total path.
    paths = {
        'total_at_least_four': goals >= 4,
        'home_zero': home == 0,
        'away_zero': away == 0,
    }
    evidence = {}
    for name, mask in paths.items():
        model_mass = matrices[:, mask].sum(axis=1)
        line_supports_path = name == 'total_at_least_four' and line is not None and line >= 3
        supported = (model_mass.mean() >= minimum_path_mass and
                     (name != 'total_at_least_four' or line_supports_path
                      or model_mass.min() >= minimum_path_mass))
        evidence[name] = {
            'mask': mask.ravel(), 'supported': bool(supported),
            'probability': float(model_mass.mean()), 'minimum_model_probability': float(model_mass.min()),
            'supported_by_verified_total_line': line_supports_path,
        }
    return evidence, line, issues


def select_scores(matrices, *, primary=None, policy='probability', fixture=None,
                  minimum_path_mass=.30, mass_budget=.03):
    """Return a frozen-ready research selection from already validated prematch PMFs.

    Probability maximizes the equally pooled joint score mass. Diverse may
    replace the lower-ranked backup when a supported path is absent, with a
    bounded model-probability loss. The fitted PMFs and supplied first score
    remain inputs. These probabilities are diagnostics from the frozen models.
    """
    if policy not in POLICIES:
        raise ValueError('unknown score-coverage policy')
    if (not math.isfinite(minimum_path_mass) or not 0 < minimum_path_mass <= 1
            or not math.isfinite(mass_budget) or not 0 <= mass_budget < 1):
        raise ValueError('invalid path mass or probability budget')
    inputs = _validated_pool(matrices)
    pooled = inputs.mean(axis=0)
    probabilities = pooled.ravel()
    ranks = np.argsort(-probabilities, kind='stable')
    first = ranks[0] if primary is None else _score_index(primary, pooled.shape)
    preferred, backup = (int(v) for v in ranks[ranks != first][:2])
    reference = [int(first), preferred, backup]
    selected = reference.copy()
    paths, line, line_issues = _path_evidence(inputs, fixture, minimum_path_mass)

    # Evaluate the nearest allowed score for each unrepresented path. Where
    # paths overlap, retain coverage of every supported path already present.
    if policy == 'diverse':
        covered = {name for name, p in paths.items() if p['supported'] and p['mask'][reference].any()}
        candidates = {}
        for name, path in paths.items():
            if not path['supported'] or name in covered:
                continue
            feasible = path['mask'].copy()
            for kept in covered:
                if not paths[kept]['mask'][reference[:2]].any():
                    feasible &= paths[kept]['mask']
            eligible = ranks[feasible[ranks]]
            if len(eligible):
                proposed = int(eligible[0])
                if probabilities[proposed] > 0 and probabilities[backup] - probabilities[proposed] <= mass_budget + 1e-12:
                    candidates[proposed] = {
                        key for key, value in paths.items()
                        if value['supported'] and key not in covered and value['mask'][proposed]}
        if candidates:
            # For equally probable choices, keep the more extensive coverage
            # and then use the matrix's row-major score order.
            selected[-1] = min(candidates, key=lambda k: (-probabilities[k], -len(candidates[k]), k))

    def label(index):
        return '%d-%d' % np.unravel_index(index, pooled.shape)

    def mass(indices):
        return float(probabilities[indices].sum())

    result = {
        'version': VERSION, 'policy': policy, 'primary': label(first),
        'alternates': [label(v) for v in selected[1:]], 'reference_scores': [label(v) for v in reference],
        'model_count': int(inputs.shape[0]), 'pooled_selected_probability': mass(selected),
        'pooled_reference_probability': mass(reference), 'pooled_probability_loss': max(0., mass(reference) - mass(selected)),
        'minimum_path_mass': minimum_path_mass, 'mass_budget': mass_budget,
        'verified_total_line': line, 'quote_ineligibility': line_issues,
        'path_evidence': {
            name: {**{k: v for k, v in path.items() if k != 'mask'},
                   'covered_in_reference': bool(path['mask'][reference].any()),
                   'covered_in_selection': bool(path['mask'][selected].any())}
            for name, path in paths.items()
        },
    }
    return result
