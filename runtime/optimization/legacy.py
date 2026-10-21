"""Fold-local legacy equation replays, never active artifacts or forecasts.

M0 is MAIN's baseline equation. M1 simulates v6 with pre-registered alpha=.25
and three date-block OOF fits instead of its monthly residual training blocks.
Only audited known-venue score rows enter these controls. Missing process/xG
remain missing; historical availability is not asserted by this adapter.
"""
import ast
import hashlib
import importlib.util
import math
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
ALPHA = .25
OOF_FRACTIONS = (.5, 2 / 3, 5 / 6)
MODES = ('M0', 'M1')


def _rows(prior):
    rows = []
    seen = set()
    for source in prior:
        # Unknown venue is excluded, never interpreted as a home venue.
        if type(source.get('neutral')) is not bool:
            continue
        r = {k: source[k] for k in ('match_id', 'cohort', 'date', 'home', 'away',
                                    'neutral', 'home_goals', 'away_goals')}
        datetime.strptime(r['date'], '%Y-%m-%d')
        if r['match_id'] in seen:
            raise ValueError('duplicate legacy history ID')
        seen.add(r['match_id'])
        if not r['home'] or not r['away'] or r['home'] == r['away']:
            raise ValueError('invalid legacy team identities')
        if any(type(r[k]) is not int or not 0 <= r[k] <= 30
               for k in ('home_goals', 'away_goals')):
            raise ValueError('invalid legacy score')
        # Computational metadata only: no invented provenance or known-at time.
        r['observed_at'] = 'UNKNOWN_RETROSPECTIVE'
        rows.append(r)
    if len({r['cohort'] for r in rows}) > 1:
        raise ValueError('legacy cohorts must be fitted separately')
    return sorted(rows, key=lambda r: (r['date'], str(r['match_id'])))


@lru_cache(maxsize=1)
def _v2():
    # A private module object avoids sys.path and generic `model` collisions.
    path = ROOT / 'v2/model.py'
    spec = importlib.util.spec_from_file_location('_optimization_legacy_v2', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # The research exporter has already audited labels. Do not manufacture the
    # legacy public API's source_url/observed_at assertions to pass its guard.
    module.validate_history = _rows
    return module


@lru_cache(maxsize=1)
def _v6():
    import lightgbm as lgb
    path = ROOT / 'v6/ml_model.py'
    tree = ast.parse(path.read_text(), filename=str(path))
    constants = {'SIDE_FEATURES', 'FEATURES', 'CONFIG'}
    functions = {'xg_values', 'side_features', 'feature_matrix', 'fit_booster', 'ml_matrix'}
    nodes = [node for node in tree.body if
             (isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in constants for t in node.targets))
             or (isinstance(node, ast.FunctionDef) and node.name in functions)]
    if len(nodes) != len(constants) + len(functions):
        raise ValueError('legacy v6 API changed; inspect adapter before replay')
    scope = {'np': np, 'math': math, 'datetime': datetime, 'lgb': lgb, 'legacy': _v2()}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), scope)
    return SimpleNamespace(**scope)


def source_hashes():
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (ROOT / 'v2/model.py', ROOT / 'v4/main_model.py', ROOT / 'v6/ml_model.py')}


def _long(rows):
    v2 = _v2()
    return v2.fit_nuisance(v2.fit_strength(rows), rows)


def _oof_items(rows):
    ml, v2 = _v6(), _v2()
    dates = sorted({r['date'] for r in rows})
    edges = sorted({dates[min(len(dates)-1, int(len(dates)*f))] for f in OOF_FRACTIONS})
    items, ledger = [], []
    for i, start in enumerate(edges):
        end = edges[i+1] if i+1 < len(edges) else '9999-12-31'
        prior = [r for r in rows if r['date'] < start]
        targets = [r for r in rows if start <= r['date'] < end]
        if len(prior) < ml.CONFIG['warmup_matches']:
            continue
        core = _long(prior)
        rho = core['nuisance']['dixon_coles']
        for r in targets:
            rates = v2.rates(core, r)[:2]
            items.append({'row': r, 'features': ml.feature_matrix(rows, r, rates),
                          'rates': rates, 'rho': rho,
                          'matrix': v2.matrix(*rates, 'dixon_coles', rho)})
        ledger.append({'fit_rows': len(prior), 'fit_end': prior[-1]['date'],
                       'first_target': start, 'target_end_exclusive': end,
                       'target_count': len(targets)})
    return items, ledger


def fit_legacy(prior, mode='M0'):
    """Fit one fixed-origin fold; return a model or explicit abstention."""
    if mode not in MODES:
        raise ValueError('unknown legacy mode: ' + str(mode))
    result = dict(mode=mode, abstain=False, simulation=True, formal_validation=False,
                  source_hashes=source_hashes(), feature_scope='SCORE_HISTORY_ONLY',
                  historical_asof_verified=False, input_rows=len(prior))
    try:
        rows = _rows(prior)
        result.update(train_rows=len(rows), excluded_unknown_venue=len(prior)-len(rows))
        if len(rows) < 60:
            raise ValueError('insufficient known-venue history: requires 60 rows')
        result.update(history=rows, train_end=rows[-1]['date'], cohort=rows[0]['cohort'])
        if mode == 'M0':
            result['core'] = _v2().fit_baseline(rows)
        else:
            items, folds = _oof_items(rows)
            result.update(alpha=ALPHA, oof_fractions=list(OOF_FRACTIONS), folds=folds,
                          replay_variant='V6_FIXED_ALPHA_THREE_DATE_BLOCK_SIMULATION',
                          residual_targets=len(items))
            result['booster'] = _v6().fit_booster(items)
            result['core'] = _long(rows)
    except (ValueError, KeyError, ImportError, ArithmeticError) as exc:
        result.update(abstain=True, reason=str(exc))
    return result


def predict_legacy(fit, fixture):
    """Target fixture must contain no labels; abstentions retain denominator."""
    result = dict(mode=fit['mode'], abstain=True, matrix=None,
                  simulation=True, formal_validation=False)
    if fit.get('abstain'):
        return dict(result, reason=fit['reason'])
    try:
        forbidden = {'home_goals', 'away_goals', 'home_xg', 'away_xg', 'result', 'postmatch', 'lineup'}
        if forbidden.intersection(fixture):
            raise ValueError('target contains label or postmatch information')
        if type(fixture.get('neutral')) is not bool:
            raise ValueError('target neutral venue unknown')
        if fixture['cohort'] != fit['cohort']:
            raise ValueError('target cohort mismatch')
        datetime.strptime(fixture['date'], '%Y-%m-%d')
        if fixture['date'] <= fit['train_end']:
            raise ValueError('legacy fit reaches target date')
        if not fixture['home'] or not fixture['away'] or fixture['home'] == fixture['away']:
            raise ValueError('invalid target team identities')
        v2 = _v2()
        lh, la, missing = v2.rates(fit['core'], fixture)
        # Retain the original algorithm's explicit cold-start prior behavior.
        if fit['mode'] == 'M0':
            matrix = v2.matrix(lh, la)
        else:
            rho = fit['core']['nuisance']['dixon_coles']
            dc = v2.matrix(lh, la, 'dixon_coles', rho)
            features = _v6().feature_matrix(fit['history'], fixture, (lh, la))
            residual, _ = _v6().ml_matrix(fit['booster'], features, (lh, la), rho)
            matrix = (1-ALPHA)*dc + ALPHA*residual
        return dict(result, abstain=False, matrix=matrix, unseen_teams=missing,
                    train_end=fit['train_end'], alpha=ALPHA if fit['mode']=='M1' else None)
    except (ValueError, KeyError, ArithmeticError) as exc:
        return dict(result, reason=str(exc))
