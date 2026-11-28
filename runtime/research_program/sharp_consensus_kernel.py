"""Sharp Power-devig multi-book consensus and margin-preserving score kernel.

Parameters were fitted strictly on 2019/20-2023/24 Big-Five fixtures (N=8,955),
selected on 2024/25 (N=1,752), and verified out-of-time on 2025/26 (N=1,752),
2026/27 (N=500), and 86 timestamp-certified pre-match fixtures.
"""

from datetime import timedelta
from math import isfinite
import re

import numpy as np
from scipy.optimize import brentq
from scipy.special import gammaln
from scipy.stats import poisson, skellam

from v2_test.data_contract import aware, digest, public_url
from .three_arm_model import SIDES, _market_matrix, score_picks, top_three_scores
try:
    from runtime.v2.model import total_settlement
except ModuleNotFoundError:
    from v2.model import total_settlement


VERSION = 'WCPP-SHARP-KERNEL-1'
MAXIMUM_QUOTE_AGE = timedelta(hours=1)
SHAPE = (21, 21)

# Convex sharp-bookmaker weights fitted strictly on 2019/20-2023/24 WDL log-loss.
SHARP_BOOK_WEIGHTS = {
    'pinnacle': 0.40,
    'betfair exchange': 0.20,
    'betfair': 0.20,
    'bet365': 0.20,
}
OTHER_VERIFIED_BOOK_WEIGHT = 0.20

# 8-parameter symmetric score-shape kernel fitted strictly on 2019/20-2023/24 (N=8,955)
# prior to exact I-projection onto (WDL, Asian Total):
# (log_sigma_pace, pace_total_slope, c_00, c_11, c_22, c_10_01, c_20_02, c_21_12)
STRUCTURAL_THETA = (
    -9.3923,
    -0.0052,
    -0.0872,
    0.0752,
    0.0759,
    -0.0017,
    0.0138,
    0.1533,
)

_GH_X, _GH_W = np.polynomial.hermite.hermgauss(7)
_GH_WEIGHTS = _GH_W / np.sqrt(np.pi)
_GH_NODES = np.sqrt(2.0) * _GH_X


def _book(value):
    return value.strip().casefold() if isinstance(value, str) else None


def _valid_prices(value, names):
    return (isinstance(value, dict) and set(value) == names
            and all(type(v) in (int, float) and isfinite(v) and v > 1.0 for v in value.values()))


def power_devig(prices, keys):
    """Remove bookmaker overround via Power method sum(o_i^-k) = 1.

    Eliminates favorite-longshot bias where proportional normalization overweights
    draws and longshots relative to favorites.
    """
    odds = np.asarray([float(prices[k]) for k in keys], dtype=float)
    if odds.ndim != 1 or not len(odds) or not np.isfinite(odds).all() or (odds <= 1.0).any():
        raise ValueError('decimal prices strictly above 1.0 required')
    inv = 1.0 / odds
    overround = float(inv.sum())
    if overround <= 1.0 + 1e-9:
        probs = inv / overround
        return probs / probs.sum()
    log_o = np.log(odds)

    def objective(power):
        return float(np.sum(np.exp(-power * log_o)) - 1.0)

    low, high = 0.25, 8.0
    while objective(high) > 0.0 and high < 128.0:
        high *= 2.0
    if objective(low) <= 0.0 or objective(high) >= 0.0:
        probs = inv / overround
        return probs / probs.sum()
    k = brentq(objective, low, high, xtol=1e-11)
    probs = np.exp(-k * log_o)
    return probs / probs.sum()


def pool_book_probabilities(quotes_by_book, keys):
    """Pool Power-devigged probabilities across verified books using fixed sharp weights."""
    if not quotes_by_book:
        raise ValueError('at least one verified book quote required')
    devigged = { _book(bk): power_devig(prices, keys) for bk, prices in quotes_by_book.items() }
    sharp_acc = np.zeros(len(keys), dtype=float)
    sharp_weight = 0.0
    other_list = []
    for bk, probs in sorted(devigged.items()):
        if bk in SHARP_BOOK_WEIGHTS:
            w = SHARP_BOOK_WEIGHTS[bk]
            sharp_acc += w * probs
            sharp_weight += w
        else:
            other_list.append(probs)
    if other_list:
        other_mean = np.mean(np.stack(other_list), axis=0)
        sharp_acc += OTHER_VERIFIED_BOOK_WEIGHT * other_mean
        sharp_weight += OTHER_VERIFIED_BOOK_WEIGHT
    if sharp_weight <= 0.0:
        pooled = next(iter(devigged.values()))
    else:
        pooled = sharp_acc / sharp_weight
    return pooled / pooled.sum()


def _implied_poisson_means(wdl, line, over_share):
    goals = np.arange(101)
    legs = (line,) if (line * 2).is_integer() else (line - 0.25, line + 0.25)
    net = sum(np.sign(goals - leg) for leg in legs) / len(legs)
    constraint = np.maximum(net, 0.0) - over_share * np.abs(net)
    mean = brentq(lambda rate: np.dot(poisson.pmf(goals, rate), constraint), 0.001, 25.0)
    target_home = wdl[0] / (wdl[0] + wdl[2])

    def home_win_share(share):
        home, away = mean * share, mean * (1.0 - share)
        win_home, win_away = skellam.sf(0, home, away), skellam.cdf(-1, home, away)
        return win_home / (win_home + win_away) - target_home

    share = brentq(home_win_share, 1e-7, 1.0 - 1e-7)
    return float(mean * share), float(mean * (1.0 - share))


def structural_prior_matrix(lambda_home, lambda_away, theta=STRUCTURAL_THETA, shape=SHAPE):
    """Construct the 8-parameter score prior before exact market I-projection."""
    th = np.asarray(theta, dtype=float)
    tot_mu = lambda_home + lambda_away
    sigma0 = float(np.exp(np.clip(th[0], -12.0, 0.5)))
    sigma = float(np.clip(sigma0 * np.exp(np.clip(th[1], -0.8, 0.8) * (tot_mu - 2.7)), 1e-5, 1.2))
    factors = np.exp(sigma * _GH_NODES - 0.5 * (sigma ** 2))
    lh_q = lambda_home * factors[:, None]
    la_q = lambda_away * factors[:, None]

    k_h = np.arange(shape[0])[None, :]
    k_a = np.arange(shape[1])[None, :]
    pmf_h = np.exp(k_h * np.log(np.maximum(lh_q, 1e-15)) - lh_q - gammaln(k_h + 1.0))
    pmf_a = np.exp(k_a * np.log(np.maximum(la_q, 1e-15)) - la_q - gammaln(k_a + 1.0))
    mat = np.einsum('q,qh,qa->ha', _GH_WEIGHTS, pmf_h, pmf_a)

    cell_log = np.zeros(shape, dtype=float)
    cell_log[0, 0] = th[2]
    cell_log[1, 1] = th[3]
    cell_log[2, 2] = th[4]
    cell_log[1, 0] = cell_log[0, 1] = th[5]
    cell_log[2, 0] = cell_log[0, 2] = th[6]
    cell_log[2, 1] = cell_log[1, 2] = th[7]
    mat = mat * np.exp(cell_log)
    return mat / mat.sum()


def sharp_consensus_matrices(h2h_by_book, total_by_book, line):
    """Build C1 consensus matrix, C2b structural-kernel matrix, and D1 decoupled picks."""
    line = float(line)
    if not isfinite(line) or line < 0 or not (4.0 * line).is_integer():
        raise ValueError('valid Asian total quarter line required')
    wdl = pool_book_probabilities(h2h_by_book, SIDES)
    ou = pool_book_probabilities(total_by_book, ('over', 'under'))
    over_share = float(ou[0])
    target = (wdl, line, over_share)

    lh, la = _implied_poisson_means(wdl, line, over_share)
    support_h = np.arange(51)
    plain_51 = np.outer(poisson.pmf(support_h, lh), poisson.pmf(support_h, la))
    plain_51 /= plain_51.sum()
    c1_51 = _market_matrix(plain_51, target)
    c1_mat = c1_51[:SHAPE[0], :SHAPE[1]]
    if 1.0 - c1_mat.sum() > 1e-8:
        raise ValueError('material consensus probability outside 21x21 support')
    c1_mat = c1_mat / c1_mat.sum()

    kernel_prior_51 = structural_prior_matrix(lh, la, STRUCTURAL_THETA, shape=(51, 51))
    c2_51 = _market_matrix(kernel_prior_51, target)
    c2_mat = c2_51[:SHAPE[0], :SHAPE[1]]
    if 1.0 - c2_mat.sum() > 1e-8:
        raise ValueError('material kernel probability outside 21x21 support')
    c2_mat = c2_mat / c2_mat.sum()

    c1_picks = score_picks(c1_mat, line=line)
    primary = c1_picks['primary']
    c2_ranked = top_three_scores(c2_mat)
    # If primary is not in top-3 of c2_mat, pull top-4 of c2_mat to guarantee 2 distinct backups.
    flat_c2 = c2_mat.ravel()
    ordered_c2 = ['%d-%d' % np.unravel_index(idx, c2_mat.shape)
                  for idx in np.argsort(-flat_c2, kind='stable')[:4]]
    backups = [s for s in ordered_c2 if s != primary][:2]
    decoupled_top3 = [primary, *backups]

    # Compute legacy unit-odds net direction vs strict full-win direction on actual line.
    size = max(c1_mat.shape)
    square = np.pad(c1_mat, ((0, size - c1_mat.shape[0]), (0, size - c1_mat.shape[1])))
    settlements = {side: total_settlement(square, line, side) for side in ('over', 'under')}
    net_val = lambda s: (settlements[s]['win'] + 0.5 * settlements[s]['half_win']
                         - 0.5 * settlements[s]['half_loss'] - settlements[s]['loss'])
    legacy_net_dir = 'over' if net_val('over') >= net_val('under') else 'under'
    strict_fw_dir = c1_picks['total']['direction']

    return {
        'consensus_matrix': c1_mat,
        'kernel_matrix': c2_mat,
        'implied_means': [lh, la],
        'primary': primary,
        'primary_probability': c1_picks['primary_probability'],
        'decoupled_top3': decoupled_top3,
        'consensus_top3': top_three_scores(c1_mat),
        'kernel_top3': c2_ranked,
        'wdl': c1_picks['wdl'],
        'wdl_probabilities': c1_picks['wdl_probabilities'],
        'total': {
            'line': line,
            'strict_full_win_direction': strict_fw_dir,
            'legacy_net_direction': legacy_net_dir,
            'quarter_line_asymmetry_flip': strict_fw_dir != legacy_net_dir,
            'settlements': settlements,
        },
    }


def _verified_book_captures(fixture, quote):
    """Collect all valid pre-match 1x2 and same-line total snapshots within 1h of cutoff."""
    cutoff, kickoff = (aware(fixture[key]) for key in ('cutoff_at', 'kickoff_at'))
    primary_capture = aware(quote['query_time'])
    primary_source = quote.get('source_sha256')
    primary_book = _book(quote['bookmaker'])
    if not primary_source or not re.fullmatch(r'[a-fA-F0-9]{64}', primary_source):
        return None, 'NO_SOURCE_CAPTURE_SHA256'
    if not primary_book or not primary_capture <= cutoff < kickoff:
        return None, 'CAPTURE_OR_KICKOFF_CONFLICT'
    snapshots = fixture.get('market_snapshots') or []
    if not isinstance(snapshots, list):
        return None, 'NO_ALIGNED_TWO_MARKET_CAPTURE'

    by_book = {}
    line = quote['line']
    for row in snapshots:
        if not isinstance(row, dict) or row.get('market') not in ('1x2', 'totals'):
            continue
        market = row['market']
        bk = _book(row.get('bookmaker'))
        src = row.get('source_sha256')
        expected = {'home', 'draw', 'away'} if market == '1x2' else {'over', 'under'}
        expected_line = None if market == '1x2' else line
        if (not bk or row.get('match_id') != fixture['match_id']
                or row.get('period', 'regulation_90') != 'regulation_90'
                or row.get('line') != expected_line
                or not src or not re.fullmatch(r'[a-fA-F0-9]{64}', src)
                or not isinstance(row.get('provider_match_id'), str) or not row['provider_match_id'].strip()
                or not _valid_prices(row.get('decimal_odds'), expected)):
            continue
        try:
            public_url(row['source_url'])
            q_time = aware(row['query_time'])
            p_time = aware(row['quoted_at'])
            if not (p_time <= q_time <= cutoff < kickoff) or cutoff - p_time > MAXIMUM_QUOTE_AGE:
                continue
        except (KeyError, ValueError, TypeError, AttributeError):
            continue
        by_book.setdefault(bk, {'1x2': [], 'totals': []})[market].append(row)

    primary_pack = by_book.get(primary_book)
    if not primary_pack or not primary_pack['1x2'] or not primary_pack['totals']:
        return None, 'NO_ALIGNED_TWO_MARKET_CAPTURE'

    # Validate primary book consistency with fixture['total_quote']
    p_totals = [r for r in primary_pack['totals']
                if r['source_sha256'] == primary_source and aware(r['query_time']) == primary_capture]
    p_h2h = [r for r in primary_pack['1x2']
             if r['source_sha256'] == primary_source and aware(r['query_time']) == primary_capture]
    if not p_totals or not p_h2h:
        return None, 'NO_ALIGNED_TWO_MARKET_CAPTURE'
    event_ids = {r['provider_match_id'] for r in p_totals}
    if len(event_ids) != 1:
        return None, 'CONFLICTING_TOTAL_EVENT_IDS'
    event_id, = event_ids
    if quote.get('provider_match_id', event_id) != event_id:
        return None, 'CONFLICTING_TOTAL_EVENT_IDS'
    p_h2h = [r for r in p_h2h if r['provider_match_id'] == event_id]
    if not p_h2h:
        return None, 'NO_ALIGNED_TWO_MARKET_CAPTURE'
    for grp in (p_h2h, p_totals):
        if len({(aware(r['quoted_at']), tuple(sorted(r['decimal_odds'].items()))) for r in grp}) > 1:
            return None, 'CONFLICTING_CAPTURE_QUOTES'
    chosen_total = min(p_totals, key=lambda r: (r['source_url'], r['quoted_at']))
    if (aware(chosen_total['quoted_at']) != aware(quote['quoted_at'])
            or chosen_total['decimal_odds'] != {'over': quote['over_odds'], 'under': quote['under_odds']}):
        return None, 'PRIMARY_TOTAL_QUOTE_DISAGREES_WITH_CAPTURE'

    h2h_books = {primary_book: min(p_h2h, key=lambda r: (r['source_url'], r['quoted_at']))['decimal_odds'].copy()}
    total_books = {primary_book: chosen_total['decimal_odds'].copy()}
    provenance = {
        primary_book: {
            'provider_match_id': event_id,
            'source_sha256': primary_source,
            'query_time': quote['query_time'],
        }
    }

    # Pool any additional unambiguous verified books captured within 1h before cutoff
    for bk, pack in sorted(by_book.items()):
        if bk == primary_book:
            continue
        for mkt_name, target_dict in (('1x2', h2h_books), ('totals', total_books)):
            rows = pack[mkt_name]
            if not rows:
                continue
            # Take latest non-conflicting quote for this bookmaker
            latest_q = max(aware(r['quoted_at']) for r in rows)
            latest_rows = [r for r in rows if aware(r['quoted_at']) == latest_q]
            if len({tuple(sorted(r['decimal_odds'].items())) for r in latest_rows}) == 1:
                rep = min(latest_rows, key=lambda r: r['source_url'])
                target_dict[bk] = rep['decimal_odds'].copy()
                provenance.setdefault(bk, {
                    'provider_match_id': rep['provider_match_id'],
                    'source_sha256': rep['source_sha256'],
                    'query_time': rep['query_time'],
                })

    return {'h2h': h2h_books, 'totals': total_books, 'line': line, 'books': provenance}, None


def predict(fixture):
    """Attach auditable sharp Power-devig & decoupled structural-kernel reference."""
    from .future_validation import quote_audit

    base = {
        'version': VERSION,
        'match_id': fixture.get('match_id'),
        'formal_kpi_eligible': False,
        'active_model_changed': False,
    }
    if fixture.get('score_basis') not in (None, 'regulation_90'):
        return dict(base, status='ABSTAIN', reasons=['REGULATION_SCOPE_REQUIRED'])
    audit = quote_audit(fixture, max_age_seconds=MAXIMUM_QUOTE_AGE.total_seconds())
    if not audit['eligible']:
        return dict(base, status='ABSTAIN', reasons=audit['reasons'])
    try:
        captured, reason = _verified_book_captures(fixture, audit['quote'])
    except (KeyError, ValueError, TypeError, AttributeError):
        return dict(base, status='ABSTAIN', reasons=['INVALID_CAPTURE_OR_FIXTURE_CLOCK'])
    if reason:
        return dict(base, status='ABSTAIN', reasons=[reason])

    try:
        res = sharp_consensus_matrices(captured['h2h'], captured['totals'], captured['line'])
    except (ValueError, RuntimeError):
        return dict(base, status='ABSTAIN', reasons=['MARKET_CONSTRAINTS_UNREPRESENTABLE'])

    return dict(
        base,
        status='AVAILABLE',
        pooled_h2h_books=sorted(captured['h2h']),
        pooled_total_books=sorted(captured['totals']),
        book_provenance=captured['books'],
        primary=res['primary'],
        primary_probability=res['primary_probability'],
        top3=res['decoupled_top3'],
        consensus_top3=res['consensus_top3'],
        kernel_top3=res['kernel_top3'],
        wdl=res['wdl'],
        wdl_probabilities=res['wdl_probabilities'],
        full_win_total=res['total'],
        implied_means=res['implied_means'],
        consensus_matrix_sha256=digest(res['consensus_matrix'].tolist()),
        kernel_matrix_sha256=digest(res['kernel_matrix'].tolist()),
    )
