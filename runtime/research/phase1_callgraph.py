"""Print the live prematch call graph from source. Does not load artifacts or fit models.

The edge list is the Phase-1 audit contract. Each caller function must contain
a call to the named symbol. A missing call exits non-zero so the diagram cannot
drift from runtime/predict_active.py without a failing check.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / 'runtime'

# (file relative to repo, function, symbol that must be called, status)
EDGES = (
    ('runtime/prediction_engine.py', 'predict_models', 'legacy.predict', 'LIVE'),
    ('runtime/prediction_engine.py', 'predict_models', 'predict_v4_compat', 'LIVE'),
    ('runtime/prediction_engine.py', 'predict_models', 'predict_generic_poisson', 'LIVE'),
    ('runtime/prediction_engine.py', 'predict_models', 'predict_systematic_candidate', 'LIVE'),
    ('runtime/prediction_engine.py', 'predict_models', 'predict_aus_expert', 'LIVE'),
    ('runtime/prediction_engine.py', 'predict_models', 'ml_predict', 'LIVE'),
    ('runtime/prediction_engine.py', 'predict_models', 'align_matrix_support', 'LIVE'),
    ('runtime/prediction_service.py', 'run', 'build_packets', 'LIVE'),
    ('runtime/prediction_service.py', 'run', 'upset_attach', 'PARTIAL'),
    ('runtime/prediction_service.py', 'run', 'candidate_run', 'PARTIAL'),
    ('runtime/prediction_diagnostics.py', 'attach', 'attach_goals', 'LIVE'),
    ('runtime/prediction_diagnostics.py', 'attach', 'attach_audit', 'LIVE'),
    ('runtime/prediction_diagnostics.py', 'attach', 'compare_pair', 'PARTIAL'),
    ('runtime/prediction_diagnostics.py', 'attach', 'analyze_market', 'PARTIAL'),
    ('runtime/prediction_diagnostics.py', 'attach', 'build_match_analysis', 'PARTIAL'),
    ('runtime/predict_active.py', 'main', 'save', 'LIVE'),
    ('runtime/v6/ml_model.py', 'predict', 'feature_matrix', 'LIVE'),
    ('runtime/v6/ml_model.py', 'predict', 'ml_matrix', 'LIVE'),
    ('runtime/v6/ml_model.py', 'predict', 'legacy.distribution', 'LIVE'),
    ('runtime/v4/main_model.py', 'predict', 'distribution', 'LIVE'),
    ('runtime/v4/main_model.py', 'predict', 'adjusted_rates', 'PARTIAL'),
    ('runtime/v4/main_model.py', 'predict', 'quote_check', 'LIVE'),
    ('runtime/v2/model.py', 'matrix', 'poisson.pmf', 'LIVE'),
    ('runtime/v5/paired_review.py', 'review', 'total_settlement', 'LIVE'),
)

# Symbols a reviewer might expect on the default entry, which are not called.
ABSENT_FROM_RUN = (
    'egidi',
    'shin_probabilities',
    'minute_simulator',
    'learn_weights',
    'pooled_matrix',
    'train_context',
    'shadow',
)


def _function(tree: ast.AST, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise SystemExit(f'missing function {name}')


def _called_names(fn: ast.AST) -> set[str]:
    names = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            names.add(func.id)
        elif isinstance(func, ast.Attribute):
            if isinstance(func.value, ast.Name):
                names.add(f'{func.value.id}.{func.attr}')
            names.add(func.attr)
    return names


def load_edges():
    cache = {}
    rows = []
    for rel, fn_name, symbol, status in EDGES:
        path = ROOT / rel
        if rel not in cache:
            cache[rel] = ast.parse(path.read_text())
        fn = _function(cache[rel], fn_name)
        called = _called_names(fn)
        if symbol not in called:
            raise SystemExit(f'{rel}:{fn_name} does not call {symbol}')
        rows.append({
            'file': rel,
            'function': fn_name,
            'calls': symbol,
            'line': fn.lineno,
            'status': status,
        })
    source = '\n'.join(path_text(ROOT / name) for name in ('runtime/prediction_engine.py', 'runtime/prediction_service.py', 'runtime/prediction_diagnostics.py'))
    absent = [name for name in ABSENT_FROM_RUN if name not in source]
    if len(absent) != len(ABSENT_FROM_RUN):
        present = [name for name in ABSENT_FROM_RUN if name in source]
        raise SystemExit('unexpected symbol inside predict_active.run: ' + ', '.join(present))
    return {'edges': rows, 'absent_from_predict_active_run': absent,
            'fusion': fusion_facts()}


def path_text(path: Path) -> str:
    return path.read_text()


def fusion_facts():
    ml = (RUNTIME / 'v6' / 'ml_model.py').read_text()
    tree = ast.parse(ml)
    config = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == 'CONFIG':
                    config = ast.literal_eval(node.value)
    if not config or config.get('blend_candidates') != [0.25, 0.5, 0.75, 1.0]:
        raise SystemExit('ML blend candidate grid changed')
    predict = _function(tree, 'predict')
    blend = None
    for node in ast.walk(predict):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id == 'm' and isinstance(node.value, ast.BinOp):
                blend = ast.get_source_segment(ml, node.value)
    if blend != "(1-artifact['alpha'])*dc + artifact['alpha']*ml":
        raise SystemExit('ML fusion expression changed: ' + str(blend))
    return {
        'formula': "(1 - alpha) * Dixon-Coles dc365 + alpha * LightGBM residual matrix",
        'alpha_grid': config['blend_candidates'],
        'alpha_storage': 'per cohort artifact field alpha; no league override table in source',
        'selection': 'calibration Top1, then lower score_log_loss; test fold does not select alpha',
    }


def main():
    payload = load_edges()
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write('\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
