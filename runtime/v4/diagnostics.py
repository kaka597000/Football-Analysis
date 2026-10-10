"""Read-only retrospective calibration and residual diagnostics; no fitting here."""
import math
from datetime import datetime, timezone, timedelta
import numpy as np
from main_model import aware, digest, url, probabilities, total_settlement, quote_check
from review import interval


def reliability(items, bins=10):
    if type(bins) is not int or not 1 <= bins <= 50:
        raise ValueError('invalid bin count')
    grouped = [[] for _ in range(bins)]
    for p, y in items:
        if not math.isfinite(p) or not 0 <= p <= 1 or y not in (0, 1):
            raise ValueError('invalid probability/outcome')
        grouped[min(int(p * bins), bins - 1)].append((p, y))
    rows = []
    for i, values in enumerate(grouped):
        n = len(values)
        if not n:
            continue
        hits = sum(y for _, y in values)
        mean = sum(p for p, _ in values) / n
        rows.append({'lower': i / bins, 'upper': (i + 1) / bins, 'n': n,
                     'mean_probability': mean, 'observed_frequency': hits / n,
                     'wilson95': interval(hits, n)})
    n = len(items)
    return {'n': n, 'bins': rows,
            'ece': sum(r['n'] * abs(r['mean_probability'] - r['observed_frequency']) for r in rows) / n if n else None,
            'brier': sum((p-y)**2 for p, y in items) / n if n else None,
            'status': 'DIAGNOSTIC_ONLY_NOT_A_CALIBRATION_CERTIFICATE'}


def matrix_check(f):
    m = np.asarray(f['matrix'], dtype=float)
    if (m.ndim != 2 or min(m.shape) < 2 or m.shape[0] != m.shape[1]
            or not np.isfinite(m).all() or (m < 0).any() or abs(float(m.sum()) - 1) > 1e-8):
        raise ValueError('invalid score matrix')
    if f.get('matrix_hash') != digest(f['matrix']):
        raise ValueError('matrix hash mismatch')
    try:
        h, a = map(int, f['primary'].split('-'))
    except (ValueError, AttributeError):
        raise ValueError('invalid primary score')
    if not 0 <= h < len(m) or not 0 <= a < len(m):
        raise ValueError('primary outside matrix')
    if f.get('primary_probability') is None or abs(f['primary_probability'] - float(m[h, a])) > 1e-8:
        raise ValueError('primary probability mismatch')
    expected = dict(zip(('主胜', '平局', '客胜'), map(float, probabilities(m))))
    if any(abs(f['wdl'].get(k, -1) - v) > 1e-8 for k, v in expected.items()):
        raise ValueError('WDL/matrix mismatch')
    if f['wdl_reference'] not in expected:
        raise ValueError('invalid WDL reference')
    return m


def diagnose(forecasts, results, population, bins=10, window=50):
    if type(population) is not int or population < len(forecasts):
        raise ValueError('full population required')
    if type(window) is not int or window < 1:
        raise ValueError('invalid monitoring window')
    result_map = {}
    for r in results:
        if r['match_id'] in result_map:
            raise ValueError('duplicate result')
        result_map[r['match_id']] = r
    rows, pending, seen = [], [], set()
    now = datetime.now(timezone.utc)
    for f in forecasts:
        fixture = f['input']; mid = fixture['match_id']
        if mid in seen:
            raise ValueError('duplicate forecast; select the valid prematch version before diagnosis')
        seen.add(mid)
        kickoff = aware(fixture['kickoff_at']); cutoff = aware(fixture['cutoff_at'])
        if not cutoff <= aware(f['generated_at']) < kickoff or aware(f['generated_at']) > now:
            raise ValueError('not a saved prematch forecast')
        if f.get('freeze_status') == 'NEW_MAIN_RESEARCH_FREEZE':
            if digest({k:v for k,v in f.items() if k != 'record_hash'}) != f.get('record_hash'):
                raise ValueError('tampered research freeze')
            if not aware(f['generated_at']) <= aware(f['frozen_at']) < kickoff:
                raise ValueError('invalid freeze chronology')
        m = matrix_check(f)
        r = result_map.get(mid)
        if r is None:
            pending.append(mid); continue
        if r.get('score_basis') != 'regulation_90' or not url(r.get('source_url')):
            raise ValueError('sourced regulation result required')
        if not kickoff + timedelta(minutes=90) <= aware(r['observed_at']) <= now:
            raise ValueError('invalid result chronology')
        h, a = r['home_goals'], r['away_goals']
        if any(type(v) is not int or not 0 <= v <= 30 for v in (h, a)):
            raise ValueError('invalid goals')
        actual = '主胜' if h > a else '平局' if h == a else '客胜'
        wd = f['wdl']; actual_p = float(m[h, a]) if h < len(m) and a < len(m) else 0.
        mean_h = float(np.arange(len(m)) @ m.sum(axis=1))
        mean_a = float(np.arange(len(m)) @ m.sum(axis=0))
        total = None
        if f.get('total'):
            q = quote_check(f['total']['quote'], fixture); line = q['line']
            side = f['total']['reference_direction']
            if side not in ('over', 'under'):
                raise ValueError('invalid total direction')
            dist = total_settlement(m, line, side)
            legs = [line] if line * 2 == int(line * 2) else [line-.25, line+.25]
            net = sum(1 if h+a > leg else -1 if h+a < leg else 0 for leg in legs) / len(legs)
            if side == 'under': net = -net
            outcome = {1:'win', .5:'half_win', 0:'push', -.5:'half_loss', -1:'loss'}[net]
            total = {'line': line, 'bookmaker': q['bookmaker'], 'side': side,
                     'settlement': outcome, 'full_win_probability': dist['win'],
                     'full_win_indicator': int(outcome == 'win'),
                     'settlement_brier': sum((dist[k] - int(outcome == k))**2 for k in dist),
                     'net_units_at_even_odds': net, 'actual_return_calculated': False}
        rows.append({'match_id': mid, 'cohort': fixture['cohort'], 'kickoff_at': fixture['kickoff_at'],
                     'model_version': f['version'], 'recipe': f.get('recipe'),
                     'primary': f['primary'], 'actual': f'{h}-{a}', 'primary_hit': f['primary'] == f'{h}-{a}',
                     'primary_probability': f['primary_probability'], 'wdl_hit': f['wdl_reference'] == actual,
                     'wdl_reference_probability': wd[f['wdl_reference']],
                     'score_log_loss': -math.log(max(actual_p, 1e-300)), 'actual_outside_matrix': h >= len(m) or a >= len(m),
                     'wdl_brier': sum((p-int(k == actual))**2 for k,p in wd.items()),
                     'home_goal_residual': h - mean_h, 'away_goal_residual': a - mean_a,
                     'total_goal_residual': h+a-mean_h-mean_a,
                     'missing_context': len(f.get('context_assessment', {}).get('reasons', [])),
                     'comprehensive_primary_ready': f.get('comprehensive_primary_ready') is True,
                     'freeze_status': f.get('freeze_status'), 'total': total})
    rows.sort(key=lambda r: (aware(r['kickoff_at']), r['match_id']))

    def summarize(group):
        n = len(group); quoted = [r['total'] for r in group if r['total']]
        settlements = {k: sum(q['settlement'] == k for q in quoted)
                       for k in ('win', 'half_win', 'push', 'half_loss', 'loss')}
        return {'n': n, 'primary_hits': sum(r['primary_hit'] for r in group),
                'top1': sum(r['primary_hit'] for r in group)/n if n else None,
                'primary_wilson95': interval(sum(r['primary_hit'] for r in group), n),
                'wdl_accuracy': sum(r['wdl_hit'] for r in group)/n if n else None,
                'wdl_wilson95': interval(sum(r['wdl_hit'] for r in group), n),
                'score_log_loss': sum(r['score_log_loss'] for r in group)/n if n else None,
                'wdl_brier': sum(r['wdl_brier'] for r in group)/n if n else None,
                'mean_goal_residuals': {k: sum(r[k] for r in group)/n if n else None
                                       for k in ('home_goal_residual', 'away_goal_residual', 'total_goal_residual')},
                'actual_outside_matrix': sum(r['actual_outside_matrix'] for r in group),
                'primary_reliability': reliability([(r['primary_probability'], int(r['primary_hit'])) for r in group], bins),
                'wdl_reference_reliability': reliability([(r['wdl_reference_probability'], int(r['wdl_hit'])) for r in group], bins),
                'total': {'quoted_settled': len(quoted), 'coverage_settled': len(quoted)/n if n else None,
                          'settlements': settlements,
                          'full_win_frequency_all_quoted': settlements['win']/len(quoted) if quoted else None,
                          'settlement_brier': sum(q['settlement_brier'] for q in quoted)/len(quoted) if quoted else None,
                          'full_win_reliability': reliability([(q['full_win_probability'], q['full_win_indicator']) for q in quoted], bins)}}
    chunks = [summarize(rows[i:i+window]) for i in range(0, len(rows), window)]
    return {'mode': 'SAVED_PREMATCH_RETROSPECTIVE_DIAGNOSTIC', 'formal_kpi_added': 0,
            'target_attainment_proven': False, 'population': population, 'saved': len(forecasts),
            'saved_coverage': len(forecasts)/population if population else None, 'pending': pending,
            'overall': summarize(rows), 'by_cohort': {c: summarize([r for r in rows if r['cohort'] == c])
                                                    for c in sorted({r['cohort'] for r in rows})},
            'by_model': {v: summarize([r for r in rows if r['model_version'] == v])
                         for v in sorted({r['model_version'] for r in rows})},
            'monitoring_windows': {'size': window, 'blocks': chunks, 'automatic_retraining': False,
                                   'change_in_accuracy_is_not_proof_of_drift': True},
            'rows': rows, 'limitations': ['No new calibration fit or numerical model adjustment',
              'Previously inspected results are diagnostic, not independent prospective validation',
              'Drafts lack freeze integrity; frozen and draft provenance are retained per row',
              'ECE depends on binning; small-sample intervals are not a calibration certificate',
              'Out-of-matrix score probabilities floored at 1e-300; count reported',
              'Total uses actual quoted lines; half wins and pushes remain separate; no profitability claim']}
