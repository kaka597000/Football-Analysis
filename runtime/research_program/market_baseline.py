"""Independent score and direction controls from one pre-match market capture."""

from datetime import timedelta
from math import isfinite
import re

import numpy as np
from scipy.optimize import brentq
from scipy.stats import poisson, skellam

from v2_test.data_contract import aware, digest, public_url
from .three_arm_model import _market_matrix, _quote_values, score_picks, top_three_scores


VERSION = 'WCPP-MARKET-ONLY-1'
MAXIMUM_QUOTE_AGE = timedelta(hours=1)
METHOD = 'ODDS_ONLY_POISSON_THEN_PROJECT_FULL_1X2_AND_ASIAN_SETTLEMENT'


def quote_only_matrices(h2h, total):
    """Map price dictionaries to raw Poisson and fully projected score matrices.

    This numerical function has no clock, identity, or result inputs. Call
    predict for a complete same-capture pre-match audit.
    """
    target = _quote_values({'h2h': h2h, 'total': total}, None, proxy=True)
    wdl, line, over_share = target

    # For a quarter line the two halves may win, push, or lose independently.
    goals = np.arange(101)
    legs = (line,) if (line * 2).is_integer() else (line - .25, line + .25)
    net = sum(np.sign(goals - leg) for leg in legs) / len(legs)
    constraint = np.maximum(net, 0.) - over_share * np.abs(net)
    mean = brentq(lambda rate: np.dot(poisson.pmf(goals, rate), constraint), .001, 25.)

    # Estimate how the total is shared from the win ratio conditional on a
    # decisive result. Projection then also matches the quoted draw probability.
    target_home = wdl[0] / (wdl[0] + wdl[2])

    def home_win_share(share):
        home, away = mean * share, mean * (1. - share)
        win_home, win_away = skellam.sf(0, home, away), skellam.cdf(-1, home, away)
        return win_home / (win_home + win_away) - target_home

    share = brentq(home_win_share, 1e-7, 1. - 1e-7)
    support = np.arange(51)
    plain = np.outer(poisson.pmf(support, mean * share), poisson.pmf(support, mean * (1. - share)))
    plain /= plain.sum()
    return plain, _market_matrix(plain, target)


def _opinions(prices):
    minimum = min(prices.values())
    choices = [side for side, value in prices.items() if value == minimum]
    return choices[0] if len(choices) == 1 else None


def _book(value):
    return value.strip().casefold() if isinstance(value, str) else None


def _valid_prices(value, names):
    return (isinstance(value, dict) and set(value) == names
            and all(type(v) in (int, float) and isfinite(v) and v > 1 for v in value.values()))


def _paired_capture(fixture, quote):
    cutoff, kickoff = (aware(fixture[key]) for key in ('cutoff_at', 'kickoff_at'))
    capture = aware(quote['query_time'])
    source = quote.get('source_sha256')
    book = _book(quote['bookmaker'])
    if not source or not re.fullmatch(r'[a-fA-F0-9]{64}', source):
        return None, 'NO_SOURCE_CAPTURE_SHA256'
    if not book or not capture <= cutoff < kickoff:
        return None, 'CAPTURE_OR_KICKOFF_CONFLICT'
    candidates = {'1x2': [], 'totals': []}
    snapshots = fixture.get('market_snapshots') or []
    if not isinstance(snapshots, list):
        return None, 'NO_ALIGNED_TWO_MARKET_CAPTURE'
    for row in snapshots:
        if not isinstance(row, dict) or row.get('market') not in candidates:
            continue
        market = row['market']
        expected = {'home', 'draw', 'away'} if market == '1x2' else {'over', 'under'}
        line = None if market == '1x2' else quote['line']
        if (row.get('match_id') != fixture['match_id'] or _book(row.get('bookmaker')) != book
                or row.get('period', 'regulation_90') != 'regulation_90' or row.get('line') != line
                or row.get('source_sha256') != source
                or not isinstance(row.get('provider_match_id'), str) or not row['provider_match_id'].strip()
                or not _valid_prices(row.get('decimal_odds'), expected)):
            continue
        try:
            public_url(row['source_url'])
            price_time = aware(row['quoted_at'])
            if (aware(row['query_time']) != capture or price_time > capture
                    or cutoff - price_time > MAXIMUM_QUOTE_AGE):
                continue
        except (KeyError, ValueError, TypeError, AttributeError):
            continue
        candidates[market].append(row)
    event_ids = {r['provider_match_id'] for r in candidates['totals']}
    if len(event_ids) > 1:
        return None, 'CONFLICTING_TOTAL_EVENT_IDS'
    if not event_ids:
        return None, 'NO_ALIGNED_TWO_MARKET_CAPTURE'
    event, = event_ids
    if quote.get('provider_match_id', event) != event:
        return None, 'CONFLICTING_TOTAL_EVENT_IDS'
    h2h = [r for r in candidates['1x2'] if r['provider_match_id'] == event]
    totals = candidates['totals']
    if not h2h:
        return None, 'NO_ALIGNED_TWO_MARKET_CAPTURE'
    for group in (h2h, totals):
        if len({(aware(r['quoted_at']), tuple(sorted(r['decimal_odds'].items()))) for r in group}) > 1:
            return None, 'CONFLICTING_CAPTURE_QUOTES'
    total = totals[0]
    if (aware(total['quoted_at']) != aware(quote['quoted_at'])
            or total['decimal_odds'] != {'over': quote['over_odds'], 'under': quote['under_odds']}):
        return None, 'PRIMARY_TOTAL_QUOTE_DISAGREES_WITH_CAPTURE'
    # Prefer a stable presentation if there are equivalent duplicate snapshots.
    return dict(h2h=min(h2h, key=lambda r: (r['source_url'], r['quoted_at'])),
                total=min(totals, key=lambda r: (r['source_url'], r['quoted_at']))), None


def predict(fixture):
    """Attach an auditable, independent market baseline to the paired freeze."""
    from .future_validation import quote_audit

    base = {'version': VERSION, 'method': METHOD, 'match_id': fixture.get('match_id'),
            'formal_kpi_eligible': False, 'active_model_changed': False}
    if fixture.get('score_basis') not in (None, 'regulation_90'):
        return dict(base, status='ABSTAIN', reasons=['REGULATION_SCOPE_REQUIRED'])
    audit = quote_audit(fixture, max_age_seconds=MAXIMUM_QUOTE_AGE.total_seconds())
    if not audit['eligible']:
        return dict(base, status='ABSTAIN', reasons=audit['reasons'])
    try:
        pairing, reason = _paired_capture(fixture, audit['quote'])
    except (KeyError, ValueError, TypeError, AttributeError):
        return dict(base, status='ABSTAIN', reasons=['INVALID_CAPTURE_OR_FIXTURE_CLOCK'])
    if reason:
        return dict(base, status='ABSTAIN', reasons=[reason])
    h2h, total = pairing['h2h'], pairing['total']
    h2h_prices = h2h['decimal_odds']
    total_prices = total['decimal_odds']
    line = total['line']
    try:
        plain, projected = quote_only_matrices(h2h_prices, dict(total_prices, line=line))
    except (ValueError, RuntimeError):
        return dict(base, status='ABSTAIN', reasons=['MARKET_CONSTRAINTS_UNREPRESENTABLE'])
    forecast = score_picks(projected, line=line)
    evidence = {'bookmaker': _book(total['bookmaker']), 'provider_match_id': total['provider_match_id'],
                'source_sha256': total['source_sha256'], 'query_time': total['query_time'],
                'regulation_90': True, 'cutoff_at': fixture['cutoff_at'],
                'h2h': {'prices': h2h_prices.copy(), 'quoted_at': h2h['quoted_at'], 'source_url': h2h['source_url']},
                'total': {'prices': total_prices.copy(), 'quoted_at': total['quoted_at'],
                          'source_url': total['source_url'], 'line': line}}
    return dict(base, status='AVAILABLE', quote_snapshot=evidence, quote_snapshot_sha256=digest(evidence),
                primary=forecast['primary'], primary_probability=forecast['primary_probability'],
                top3=top_three_scores(projected), poisson_top3=top_three_scores(plain),
                matrix_sha256=digest(projected.tolist()),
                wdl_probabilities=forecast['wdl_probabilities'], lowest_price_1x2=_opinions(h2h_prices),
                lowest_price_total=_opinions(total_prices), full_win_total=forecast['total'])
