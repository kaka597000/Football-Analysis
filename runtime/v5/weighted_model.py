"""Learned score-distribution stacking, independent of the legacy Champion.

Weights use chronological out-of-sample forecasts, never target-match statistics.
This is an explicitly enabled research execution path, not formal promotion.
"""
import copy
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy.optimize import minimize

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'v4'))
import main_model as legacy
from competition_router import validate_training

VERSION = 'WCPP-WEIGHTED-0.1'
EXPERTS = ('baseline', 'strength365', 'strength180', 'dc365', 'bivariate365')
CONFIG = {'weight_regularization': 0.02, 'min_fit_rows': 60,
          'min_weight_rows': 20, 'min_test_rows': 20,
          'split_date_fractions': [0.50, 0.75], 'rolling_block': 'month'}


def code_hash():
    return legacy.digest({'weighted': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                          'baseline_code_hash': legacy.code_hash()})


def learn_weights(target_probabilities):
    p = np.asarray(target_probabilities, dtype=float)
    if p.ndim != 2 or p.shape[1] != len(EXPERTS) or len(p) < CONFIG['min_weight_rows']:
        raise ValueError('insufficient real out-of-sample weight targets')
    if not np.isfinite(p).all() or (p <= 0).any() or (p > 1).any():
        raise ValueError('invalid target probabilities')
    prior = np.full(len(EXPERTS), 1 / len(EXPERTS))
    reg = CONFIG['weight_regularization']

    def objective(w):
        pooled = p @ w
        loss = -np.log(pooled).mean() + reg * np.sum((w - prior) ** 2)
        grad = -(p / pooled[:, None]).mean(axis=0) + 2 * reg * (w - prior)
        return loss, grad

    fit = minimize(objective, prior, jac=True, method='SLSQP',
                   bounds=[(0., 1.)] * len(EXPERTS),
                   constraints=[{'type': 'eq', 'fun': lambda w: w.sum() - 1,
                                 'jac': lambda w: np.ones(len(EXPERTS))}],
                   options={'maxiter': 500, 'ftol': 1e-11})
    if not fit.success:
        raise ValueError('weight optimization failed: ' + fit.message)
    w = np.maximum(fit.x, 0.)
    return (w / w.sum()).tolist()


def pooled_matrix(matrices, weights):
    w = np.asarray(weights, dtype=float)
    if w.shape != (len(EXPERTS),) or not np.isfinite(w).all() or (w < 0).any() or abs(w.sum() - 1) > 1e-9:
        raise ValueError('invalid simplex weights')
    mats = np.asarray(matrices, dtype=float)
    if mats.shape != (len(EXPERTS), 51, 51) or not np.isfinite(mats).all() or (mats < 0).any():
        raise ValueError('invalid expert score distributions')
    if not np.allclose(mats.sum(axis=(1, 2)), 1., atol=1e-12):
        raise ValueError('unnormalized expert matrix')
    result = np.tensordot(w, mats, axes=1)
    return result / result.sum()


def chronological_split(rows):
    dates = sorted({r['date'] for r in rows})
    if len(dates) < 20:
        raise ValueError('insufficient distinct historical dates')
    edges = [dates[int(len(dates) * x)] for x in CONFIG['split_date_fractions']]
    initial = [r for r in rows if r['date'] < edges[0]]
    weights = [r for r in rows if edges[0] <= r['date'] < edges[1]]
    test = [r for r in rows if r['date'] >= edges[1]]
    for block, minimum in zip((initial, weights, test),
                              (CONFIG['min_fit_rows'], CONFIG['min_weight_rows'], CONFIG['min_test_rows'])):
        if len(block) < minimum:
            raise ValueError('insufficient chronological split samples')
    return initial, weights, test


def rolling_predictions(rows, targets):
    """Refit using dates strictly before each prediction month/block boundary."""
    output, folds = [], []
    for month in sorted({r['date'][:7] for r in targets}):
        block = [r for r in targets if r['date'][:7] == month]
        first = min(r['date'] for r in block)
        prior = [r for r in rows if r['date'] < first]
        core = legacy.fit_core(prior)
        folds.append({'first_target': first, 'fit_end': prior[-1]['date'],
                      'fit_rows': len(prior), 'target_rows': len(block)})
        for r in block:
            matrices, missing = [], set()
            for expert in EXPERTS:
                m, meta = legacy.distribution(core, None, prior, r, expert)
                matrices.append(m)
                missing.update(meta['unseen_teams'])
            output.append((r, matrices, {'unseen_teams': sorted(missing)}))
    return output, folds


def train(rows):
    rows = legacy.history(rows)
    initial, calibration, test = chronological_split(rows)
    oof, folds = rolling_predictions(rows, calibration)
    weights = learn_weights([[m[r['home_goals'], r['away_goals']] for m in ms]
                             for r, ms, _ in oof])
    evaluated, test_folds = rolling_predictions(rows, test)
    candidate = legacy.score_matrices([(r, pooled_matrix(ms, weights), meta)
                                      for r, ms, meta in evaluated])
    baseline = legacy.score_matrices([(r, ms[0], meta) for r, ms, meta in evaluated])
    comparison = legacy.comparison(candidate, baseline)
    # User-authorized research execution remains distinct from formal promotion.
    # A failed numerical integrity check aborts; insufficient gain is disclosed.
    report = {'version': VERSION, 'mode': 'INSPECTED_HISTORY_DEVELOPMENT_DIAGNOSTIC',
              'formal_validation': False, 'config': CONFIG,
              'weights': dict(zip(EXPERTS, weights)), 'weight_fit_rows': len(oof),
              'weight_folds': folds, 'test_folds': test_folds,
              'test': {'weighted': candidate, 'baseline': baseline},
              'acceptance': comparison,
              'execution': 'LEARNED_WEIGHTED_RESEARCH' if comparison['acceptance_gate_passed'] else 'BASELINE_FALLBACK_WITH_WEIGHTED_SHADOW',
              'limitations': ['No proven prospective accuracy or formal promotion',
                             'No trained lineup, injury, incentive, load or xG coefficients',
                             'Historical first-known context timestamps not reconstructed',
                             'Fixed-2.5 historical OU diagnostics are not actual-line accuracy'],
              'split': {name: {'n': len(v), 'start': v[0]['date'], 'end': v[-1]['date']}
                        for name, v in [('initial', initial), ('weights', calibration), ('test', test)]}}
    core = legacy.fit_core(rows)
    observed = max(r['observed_at'] for r in rows)
    built = datetime.now(timezone.utc).isoformat()
    anchor = {'version': legacy.VERSION, 'role': 'NEW_MAIN_RESEARCH', 'cohort': rows[0]['cohort'],
              'competition_route': validate_training(rows), 'core': core, 'form': None,
              'history': rows, 'selected_recipe': 'baseline', 'execution_recipe': 'baseline',
              'train_end': rows[-1]['date'], 'observed_at': observed, 'built_at': built,
              'config': legacy.CONFIG, 'code_hash': legacy.code_hash(),
              'context_coefficients': None, 'formal_validation': False}
    artifact = {'version': VERSION, 'role': 'WEIGHTED_RESEARCH_MAIN',
                'config': CONFIG, 'experts': list(EXPERTS), 'weights': weights,
                'anchor': anchor, 'core': core, 'history': rows,
                'cohort': rows[0]['cohort'], 'train_end': rows[-1]['date'],
                'observed_at': observed, 'built_at': built, 'code_hash': code_hash(),
                'report_hash': legacy.digest(report), 'acceptance': comparison,
                'context_modules': {'xg': 'UNTRAINED_NO_VERIFIED_HISTORY',
                                    'lineup_injuries': 'UNTRAINED_NO_ASOF_HISTORY',
                                    'load_incentives': 'UNTRAINED_NO_ASOF_HISTORY'},
                'legacy_champion_unchanged': 'V21.5', 'permanent_lambda_modified': False,
                'formal_validation': False, 'formal_kpi_eligible': False}
    return artifact, report


def predict(artifact, fixture, variant='protected'):
    if variant not in ('protected', 'test'):
        raise ValueError('unknown execution track')
    if artifact.get('version') != VERSION or artifact.get('code_hash') != code_hash() or artifact.get('config') != CONFIG:
        raise ValueError('weighted artifact/code/config mismatch')
    if artifact['anchor']['history'] != artifact['history'] or artifact['core'] != artifact['anchor']['core']:
        raise ValueError('tampered weighted history/core')
    if artifact['experts'] != list(EXPERTS):
        raise ValueError('expert contract mismatch')
    # Reuse all identity, competition, temporal, neutral-site and quote guards.
    base = legacy.predict(artifact['anchor'], fixture)
    matrices, metas = [], []
    for expert in EXPERTS:
        m, meta = legacy.distribution(artifact['core'], None, artifact['history'], fixture, expert)
        if meta['unseen_teams']:
            raise ValueError('unseen teams unsupported')
        matrices.append(m)
        metas.append(meta)
    m = pooled_matrix(matrices, artifact['weights'])
    primary = tuple(map(int, np.unravel_index(np.argmax(m), m.shape)))
    wd = dict(zip(('主胜', '平局', '客胜'), map(float, legacy.probabilities(m))))
    ranked = np.argsort(-m.ravel(), kind='stable')[:3]
    total = None
    if base['total']:
        q = base['total']['quote']
        over, under = (legacy.total_settlement(m, q['line'], side) for side in ('over', 'under'))
        value = lambda p: p['win'] + .5*p['half_win'] - .5*p['half_loss'] - p['loss']
        total = {'quote': q, 'over': over, 'under': under,
                 'reference_direction': 'over' if value(over) > value(under) else 'under',
                 'direction_basis': 'whole learned-mixture settlement balance, not one score'}
    candidate_matrix = m
    passed = artifact['acceptance']['acceptance_gate_passed']
    use_candidate = passed or variant == 'test'
    if not use_candidate:
        # A user request to enable the framework does not turn a failed
        # candidate into a validated Primary. Keep its forecast inspectable.
        m = np.asarray(base['matrix'])
        primary = tuple(map(int, np.unravel_index(np.argmax(m), m.shape)))
        wd = base['wdl']
        ranked = np.argsort(-m.ravel(), kind='stable')[:3]
        total = base['total']
    output = copy.deepcopy(base)
    output.update(version=VERSION, role='WEIGHTED_RESEARCH_MAIN', model_hash=legacy.digest(artifact),
                  code_hash=code_hash(), matrix=m.tolist(), matrix_hash=legacy.digest(m.tolist()),
                  recipe='learned_distribution_stacking' if use_candidate else 'baseline_fallback', primary=f'{primary[0]}-{primary[1]}',
                  primary_probability=float(m[primary]), primary_basis='LEARNED_SCORE_DISTRIBUTION_MIXTURE' if use_candidate else 'VALIDATION_GATED_BASELINE_FALLBACK',
                  primary_label='test模型研究比分；未通过验证项另列' if variant == 'test' else '学习权重研究比分' if passed else '融合未通过接受门槛，执行基线；融合候选仅观察',
                  wdl=wd, wdl_reference=max(wd, key=wd.get), total=total,
                  top3=[{'score': f'{i//51}-{i%51}', 'probability': float(m.ravel()[i])} for i in ranked],
                  rates={'means': [float(np.arange(51)@m.sum(axis=1)), float(np.arange(51)@m.sum(axis=0))]},
                  weighted_modules=dict(zip(EXPERTS, artifact['weights'])) if use_candidate else dict(zip(EXPERTS, [1., 0., 0., 0., 0.])),
                  learned_candidate_weights=dict(zip(EXPERTS, artifact['weights'])),
                  weighted_execution_enabled=use_candidate,
                  model_label='test模型' if variant == 'test' else '保护执行',
                  weighted_shadow={'primary': f'{np.unravel_index(np.argmax(candidate_matrix), candidate_matrix.shape)[0]}-{np.unravel_index(np.argmax(candidate_matrix), candidate_matrix.shape)[1]}',
                                   'matrix': candidate_matrix.tolist(), 'matrix_hash': legacy.digest(candidate_matrix.tolist()),
                                   'wdl': dict(zip(('主胜','平局','客胜'), map(float, legacy.probabilities(candidate_matrix)))),
                                   'formal_kpi_eligible': False},
                  module_status=artifact['context_modules'], statistical_baseline_primary=base['primary'],
                  acceptance=artifact['acceptance'], comprehensive_primary_ready=False,
                  freeze_status='DRAFT_NOT_FROZEN', formal_kpi_eligible=False,
                  probability_calibration_status='NOT_PROSPECTIVELY_VALIDATED')
    if not artifact['acceptance']['acceptance_gate_passed']:
        output['reasons'].append('Weighted research candidate has not passed formal acceptance gate')
    return output
