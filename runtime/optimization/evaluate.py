"""Independent score-distribution evaluation of saved historical receipts.

All rates are fractions, errors are prediction minus observation, and paired
differences are B minus A.  Bootstrap units are ISO calendar weeks, shared
across competitions.  Probability losses use an explicit 1e-15 floor.
"""
from collections import Counter, defaultdict
from datetime import date
import math

import numpy as np

try:
    from .schema import aware
except ImportError:
    from schema import aware

LOSS_FLOOR = 1e-15
BOOTSTRAP_REPLICATES = 2000
BOOTSTRAP_SEED = 20261015
COUNTS = ('N_scope', 'N_pred', 'N_settled', 'N_scored', 'N_pending', 'N_abstain')
METRICS = (
    'top1', 'top1_probability', 'wdl_confidence',
    'home_error', 'away_error', 'home_absolute_error', 'away_absolute_error',
    'total_error', 'total_absolute_error', 'total_squared_error',
    'mode_home_error', 'mode_away_error',
    'mode_total_error', 'mode_total_absolute_error', 'mode_total_squared_error',
    'score_log_loss', 'total_log_loss', 'total_crps', 'poisson_deviance',
    'wdl_hit', 'wdl_brier', 'wdl_log_loss', 'total_variance',
    'interval80_covered', 'interval90_covered',
    'p_zero', 'p_zero_one', 'p_four_plus', 'p_five_plus',
    'observed_zero', 'observed_zero_one', 'observed_four_plus', 'observed_five_plus',
    'zero_brier', 'zero_one_brier', 'four_plus_brier', 'five_plus_brier',
)


def _matrix(value):
    # Same strict normalization contract as ledger.check_matrix, without
    # ledger's script-only import path when this module is imported as a package.
    m = np.asarray(value, dtype=float)
    if (m.ndim != 2 or m.shape[0] != m.shape[1] or not m.size
            or not np.isfinite(m).all() or (m < 0).any()):
        raise ValueError('invalid score matrix')
    if abs(float(m.sum()) - 1.) > 1e-10:
        raise ValueError('matrix is not normalized')
    return m


def settle_ou(total, line, side):
    """Settle a unit Asian total stake; prices do not change these categories."""
    if (not isinstance(total, (int, np.integer)) or isinstance(total, bool)
            or total < 0):
        raise ValueError('total must be a nonnegative integer')
    if isinstance(line, bool) or not isinstance(line, (int, float, np.number)):
        raise ValueError('invalid total line')
    line = float(line)
    if not math.isfinite(line) or line < 0 or not math.isclose(line * 4, round(line * 4), abs_tol=1e-9):
        raise ValueError('line must be a nonnegative quarter-goal line')
    side = str(side).lower()
    if side not in ('over', 'under'):
        raise ValueError('side must be over or under')
    quarter = round(line * 4)
    lines = [line] if quarter % 2 == 0 else [(quarter - 1) / 4, (quarter + 1) / 4]
    signs = [int(bool(total > x)) - int(bool(total < x)) for x in lines]
    value = sum(signs) / len(signs) * (1 if side == 'over' else -1)
    return {1: 'FULL_WIN', .5: 'HALF_WIN', 0: 'PUSH', -.5: 'HALF_LOSS', -1: 'FULL_LOSS'}[value]


def _label(label, match_id):
    if label is None:
        return None
    if label.get('match_id', match_id) != match_id:
        raise ValueError('prediction/label identity mismatch')
    h, a = label.get('home_goals'), label.get('away_goals')
    if h is None or a is None:
        return None
    if any(isinstance(v, bool) or not isinstance(v, (int, np.integer)) or v < 0 for v in (h, a)):
        raise ValueError('labels must be nonnegative integer 90-minute goals')
    return int(h), int(a)


def _market(pred, total):
    """A quote must prove identity, prices and cutoff before actual-line scoring.

    Optional input: actual_market={match_id, bookmaker, line, over_odds,
    under_odds, quote_at, side}; pred.cutoff_at supplies the cutoff.
    Missing evidence never falls back to a fixed line.
    """
    result = {'actual_market_status': 'NA', 'actual_market_settlement': None,
              'actual_market_reason': 'MISSING_ACTUAL_MARKET'}
    q = pred.get('actual_market')
    if not isinstance(q, dict):
        return result
    try:
        if q.get('match_id') != pred['match_id'] or not q.get('bookmaker'):
            raise ValueError('MISSING_OR_MISMATCHED_QUOTE_IDENTITY')
        if any(isinstance(q.get(k), bool) or not isinstance(q.get(k), (int, float))
               or not math.isfinite(q[k]) or q[k] <= 1 for k in ('over_odds', 'under_odds')):
            raise ValueError('MISSING_TWO_SIDED_DECIMAL_PRICES')
        if aware(q['quote_at']) > aware(pred['cutoff_at']):
            raise ValueError('QUOTE_AFTER_CUTOFF')
        settlement = settle_ou(total if total is not None else 0, q['line'], q['side'])
    except (KeyError, TypeError, ValueError) as exc:
        result['actual_market_reason'] = str(exc)
        return result
    result.update(actual_market_status='SETTLED' if total is not None else 'PENDING',
                  actual_market_settlement=settlement if total is not None else None,
                  actual_market_reason=None, actual_market_line=float(q['line']),
                  actual_market_side=q['side'].lower(), actual_market_bookmaker=q['bookmaker'])
    return result


def score_prediction(pred, label):
    """Score one saved prediction; abstentions and pending rows remain present."""
    actual = _label(label, pred['match_id'])
    predicted = pred.get('status') == 'PREDICTED'
    m = _matrix(pred['matrix']) if predicted else None
    row = {k: pred.get(k) for k in ('match_id', 'date', 'model', 'status', 'cohort', 'fold')}
    row.update({k: None for k in METRICS})
    row.update(N_scope=1, N_pred=int(predicted), N_settled=int(actual is not None),
               N_scored=int(predicted and actual is not None), N_pending=int(actual is None),
               N_abstain=int(not predicted), loss_probability_floor=LOSS_FLOOR,
               actual_market_status='NA', actual_market_settlement=None,
               actual_market_reason='NO_VALID_PREDICTION')
    if not predicted:
        return row
    n = m.shape[0]
    hgrid, agrid = np.indices(m.shape)
    totals = np.bincount((hgrid + agrid).ravel(), weights=m.ravel(), minlength=2 * n - 1)
    mu_h = float(np.sum(hgrid * m)); mu_a = float(np.sum(agrid * m)); mu = mu_h + mu_a
    mode = tuple(int(x) for x in np.unravel_index(np.argmax(m), m.shape))
    total_axis = np.arange(len(totals))
    wdl = [float(m[hgrid > agrid].sum()), float(np.trace(m)), float(m[hgrid < agrid].sum())]
    row.update(mu_home=mu_h, mu_away=mu_a, mu_total=mu, mode_score=list(mode),
               top1_probability=float(m[mode]), wdl_confidence=max(wdl),
               mode_total=sum(mode), total_variance=float(np.sum((total_axis - mu) ** 2 * totals)),
               wdl_probabilities=wdl, total_probabilities=totals.tolist(),
               p_zero=float(totals[0]), p_zero_one=float(totals[:2].sum()),
               p_four_plus=float(totals[4:].sum()), p_five_plus=float(totals[5:].sum()))
    cdf = np.cumsum(totals)
    for coverage in (80, 90):
        alpha = (1 - coverage / 100) / 2
        row['interval' + str(coverage)] = [int(np.searchsorted(cdf, alpha)),
                                          min(len(totals) - 1, int(np.searchsorted(cdf, 1 - alpha)))]
    row.update(_market(pred, sum(actual) if actual is not None else None))
    if actual is None:
        return row
    h, a = actual; t = h + a
    phit = float(m[h, a]) if h < n and a < n else 0.
    pt = float(totals[t]) if t < len(totals) else 0.
    support = max(len(totals), t + 1)
    padded = np.pad(totals, (0, support - len(totals)))
    crps = float(np.sum((np.cumsum(padded) - (np.arange(support) >= t)) ** 2))
    outcome = 0 if h > a else 1 if h == a else 2
    row.update(actual_home=h, actual_away=a, actual_total=t,
               top1=int(mode == actual), home_error=mu_h-h, away_error=mu_a-a,
               home_absolute_error=abs(mu_h-h), away_absolute_error=abs(mu_a-a),
               total_error=mu-t, total_absolute_error=abs(mu-t), total_squared_error=(mu-t)**2,
               mode_home_error=mode[0]-h, mode_away_error=mode[1]-a,
               mode_total_error=sum(mode)-t, mode_total_absolute_error=abs(sum(mode)-t),
               mode_total_squared_error=(sum(mode)-t)**2,
               score_log_loss=-math.log(max(LOSS_FLOOR, phit)),
               total_log_loss=-math.log(max(LOSS_FLOOR, pt)), total_crps=crps,
               poisson_deviance=(2*mu if t == 0 else 2*(t*math.log(t/max(LOSS_FLOOR, mu))-(t-mu))),
               wdl_hit=int(int(np.argmax(wdl)) == outcome),
               wdl_brier=sum((p-int(i == outcome))**2 for i, p in enumerate(wdl)),
               wdl_log_loss=-math.log(max(LOSS_FLOOR, wdl[outcome])),
               fixed_2_5_diagnostic_settlement=settle_ou(t, 2.5, 'over' if totals[3:].sum() > .5 else 'under'))
    for name, event in (('zero', t == 0), ('zero_one', t <= 1), ('four_plus', t >= 4), ('five_plus', t >= 5)):
        row['observed_' + name] = int(event)
        row[name + '_brier'] = (row['p_' + name] - int(event)) ** 2
    for coverage in (80, 90):
        lo, hi = row['interval' + str(coverage)]
        row['interval' + str(coverage) + '_covered'] = int(lo <= t <= hi)
    return row


def _week(row):
    try:
        day = date.fromisoformat(row['date'][:10])
    except (KeyError, TypeError, ValueError):
        raise ValueError('ISO match date required for week-block confidence intervals') from None
    iso = day.isocalendar()
    return (iso.year, iso.week)


def _week_ci(rows, keys, replicates, seed, derived_summary=False):
    """Resample whole weeks; use each sampled week's original match weights."""
    groups = defaultdict(list)
    for row in rows:
        groups[_week(row)].append(row)
    result = {'method': 'ISO_WEEK_BLOCK_PERCENTILE_BOOTSTRAP', 'level': .95,
              'n_blocks': len(groups), 'replicates': replicates, 'seed': seed,
              'metrics': {}, 'standard_errors': {}}
    if len(groups) < 2:
        result['status'] = 'INSUFFICIENT_WEEK_BLOCKS'
        return result
    if replicates < 1:
        raise ValueError('replicates must be positive')
    blocks = [groups[k] for k in sorted(groups)]
    sums = np.zeros((len(blocks), len(keys))); counts = np.zeros_like(sums)
    for i, block in enumerate(blocks):
        for j, key in enumerate(keys):
            values = [r[key] for r in block if r.get(key) is not None]
            sums[i, j] = sum(values); counts[i, j] = len(values)
    rng = np.random.default_rng(seed)
    weights = rng.multinomial(len(blocks), np.full(len(blocks), 1 / len(blocks)), size=replicates)
    sampled_sums = weights @ sums; sampled_counts = weights @ counts
    for j, key in enumerate(keys):
        good = sampled_counts[:, j] > 0
        if good.any():
            values = sampled_sums[good, j] / sampled_counts[good, j]
            result['metrics'][key] = [float(x) for x in np.quantile(values, [.025, .975])]
            result['standard_errors'][key] = float(np.std(values, ddof=1)) if len(values) > 1 else None
            if derived_summary and key in ('total_squared_error', 'mode_total_squared_error'):
                derived = key.replace('squared_error', 'rmse')
                transformed = np.sqrt(values)
                result['metrics'][derived] = [float(x) for x in np.quantile(transformed, [.025, .975])]
                result['standard_errors'][derived] = float(np.std(transformed, ddof=1)) if len(values) > 1 else None
    aliases = {'top1_rate': 'top1', 'total_bias': 'total_error', 'total_mae': 'total_absolute_error',
               'mode_total_bias': 'mode_total_error', 'mode_total_mae': 'mode_total_absolute_error'}
    for alias, key in aliases.items():
        if key in result['metrics']:
            result['metrics'][alias] = result['metrics'][key]
            result['standard_errors'][alias] = result['standard_errors'][key]
    result['status'] = 'OK'
    return result


def _reliability(rows, probability, observed):
    bins = [[] for _ in range(10)]
    for row in rows:
        if row.get(probability) is not None and row.get(observed) is not None:
            p = row[probability]
            bins[min(9, int(p * 10))].append((p, row[observed]))
    return [dict(lower=i/10, upper=(i+1)/10, upper_inclusive=i == 9,
                 N=len(values), mean_probability=float(np.mean([x[0] for x in values])) if values else None,
                 observed_rate=float(np.mean([x[1] for x in values])) if values else None)
            for i, values in enumerate(bins)]


def summarize(rows, *, replicates=BOOTSTRAP_REPLICATES, seed=BOOTSTRAP_SEED):
    """Aggregate one model/version's full scope, preserving missing denominators."""
    rows = list(rows)
    models = {r.get('model') for r in rows if r.get('model') is not None}
    if len(models) > 1:
        raise ValueError('summarize one model/version at a time')
    ids = [r['match_id'] for r in rows]
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate match_id: summarize versions/folds separately')
    result = {key: sum(r.get(key, 0) for r in rows) for key in COUNTS}
    result['model'] = next(iter(models), None)
    result['hits'] = sum(r.get('top1') or 0 for r in rows)
    for key in METRICS:
        values = [r[key] for r in rows if r.get('N_scored') and r.get(key) is not None]
        result[key] = float(np.mean(values)) if values else None
    result['top1_rate'] = result['hits'] / result['N_scored'] if result['N_scored'] else None
    result['coverage'] = result['N_scored'] / result['N_settled'] if result['N_settled'] else None
    result['coverage_adjusted_top1'] = result['hits'] / result['N_settled'] if result['N_settled'] else None
    result['total_bias'] = result['total_error']; result['total_mae'] = result['total_absolute_error']
    result['total_rmse'] = math.sqrt(result['total_squared_error']) if result['total_squared_error'] is not None else None
    result['mode_total_bias'] = result['mode_total_error']; result['mode_total_mae'] = result['mode_total_absolute_error']
    result['mode_total_rmse'] = math.sqrt(result['mode_total_squared_error']) if result['mode_total_squared_error'] is not None else None
    result['actual_market'] = {
        'N_scope': len(rows), 'N_settled': sum(r.get('actual_market_status') == 'SETTLED' for r in rows),
        'N_pending': sum(r.get('actual_market_status') == 'PENDING' for r in rows),
        'N_NA': sum(r.get('actual_market_status', 'NA') == 'NA' for r in rows),
        'settlements': dict(Counter(r['actual_market_settlement'] for r in rows if r.get('actual_market_settlement'))),
        'NA_reasons': dict(Counter(r.get('actual_market_reason') for r in rows if r.get('actual_market_status', 'NA') == 'NA')),
    }
    scored = [r for r in rows if r.get('N_scored')]
    result['week_block_ci'] = _week_ci(scored, METRICS, replicates, seed, derived_summary=True)
    result['reliability'] = {'score_top1': _reliability(scored, 'top1_probability', 'top1'),
                             'wdl_top1': _reliability(scored, 'wdl_confidence', 'wdl_hit')}
    for event in ('zero', 'zero_one', 'four_plus', 'five_plus'):
        result['reliability'][event] = _reliability(scored, 'p_' + event, 'observed_' + event)
    return result


def paired(rowsA, rowsB, *, replicates=BOOTSTRAP_REPLICATES, seed=BOOTSTRAP_SEED):
    """Compare common scored matches; each full-scope summary remains separate."""
    rowsA, rowsB = list(rowsA), list(rowsB)
    full_a = summarize(rowsA, replicates=replicates, seed=seed)
    full_b = summarize(rowsB, replicates=replicates, seed=seed)
    a = {r['match_id']: r for r in rowsA}; b = {r['match_id']: r for r in rowsB}
    shared = sorted(a.keys() & b.keys())
    deltas = []; wrong_right = right_wrong = 0
    for mid in shared:
        x, y = a[mid], b[mid]
        if not (x.get('N_scored') and y.get('N_scored')):
            continue
        if x['date'] != y['date'] or (x['actual_home'], x['actual_away']) != (y['actual_home'], y['actual_away']):
            raise ValueError('paired match date/label mismatch')
        delta = {'match_id': mid, 'date': x['date']}
        for key in METRICS:
            delta[key] = y[key] - x[key] if x.get(key) is not None and y.get(key) is not None else None
        deltas.append(delta)
        wrong_right += int(not x['top1'] and bool(y['top1']))
        right_wrong += int(bool(x['top1']) and not y['top1'])
    means = {}
    for key in METRICS:
        values = [r[key] for r in deltas if r.get(key) is not None]
        means[key] = float(np.mean(values)) if values else None
    return {'direction': 'B_MINUS_A', 'A': full_a, 'B': full_b, 'N_common_scope': len(shared),
            'N_common_scored': len(deltas), 'N_A_only': len(a.keys() - b.keys()),
            'N_B_only': len(b.keys() - a.keys()), 'wrong_to_right': wrong_right,
            'right_to_wrong': right_wrong, 'net_top1_hits': wrong_right-right_wrong,
            'mean_difference': means, 'week_block_ci': _week_ci(deltas, METRICS, replicates, seed)}
