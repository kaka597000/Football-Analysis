"""Offline same-book, same-total-line movement conditional on the observed market level."""
from math import isfinite, log

import numpy as np
from scipy.optimize import brentq
from scipy.special import expit

from .market_baseline import quote_only_matrices
from .three_arm_model import _market_matrix, _quote_values, score_picks, top_three_scores


def valid_prices(values, names):
    return isinstance(values, dict) and all(
        type(values.get(k)) in (int, float) and isfinite(values[k]) and values[k] > 1 for k in names)


def total_ok(quote):
    if not valid_prices(quote, ('over', 'under')):
        return False
    line = quote.get('line')
    return type(line) in (int, float) and isfinite(line) and line > 0 and (4. * line).is_integer()


def observation(row):
    """Both endpoints come from the same book and response edition verified by the caller."""
    if row.get('excluded'):
        return row['excluded'], None
    opening, current = row.get('opening_total'), row.get('current_total')
    if not valid_prices(row.get('latest_h2h'), ('home', 'draw', 'away')) or not total_ok(current):
        return 'no_current_pair', None
    if not total_ok(opening):
        return 'opening_unavailable', 0.
    if opening['line'] != current['line']:
        return 'different_total_line', 0.
    here = log(current['under'] / current['over'])
    earlier = log(opening['under'] / opening['over'])
    return 'same_line', float(np.clip(here - earlier, -log(2.), log(2.)))


def training_term(row, score):
    status, signal = observation(row)
    if status != 'same_line' or score is None:
        return None
    goals = sum(score)
    quote = row['current_total']
    line = float(quote['line'])
    legs = (line,) if (2*line).is_integer() else (line-.25, line+.25)
    stake = sum(int(goals > leg) - int(goals < leg) for leg in legs) / len(legs)
    if not stake:
        return None
    return log(quote['under']/quote['over']), signal, float(stake > 0), abs(stake)


def fit(terms):
    if len(terms) < 12:
        return None
    level, move, outcome, stake = np.asarray(terms, dtype=float).T
    def gradient(beta):
        return float(np.dot(stake * move, expit(level + beta*move) - outcome) + beta)
    if gradient(-1.) >= 0:
        return -1.
    if gradient(1.) <= 0:
        return 1.
    return float(brentq(gradient, -1., 1.))


def paired_matrices(row, beta):
    status, signal = observation(row)
    if signal is None:
        raise ValueError(f'no decision-time market: {status}')
    if not isfinite(beta) or abs(beta) > 1:
        raise ValueError('coefficient outside registration')
    h2h, total = row['latest_h2h'], row['current_total']
    plain, baseline = quote_only_matrices(h2h, total)
    if not beta or not signal:
        return baseline, baseline
    wdl, line, _ = _quote_values({'h2h': h2h, 'total': total}, None, proxy=True)
    target = float(expit(log(total['under']/total['over']) + beta*signal))
    return baseline, _market_matrix(plain, (wdl, line, target))


def picks(matrix, line):
    terms = score_picks(matrix, line=line)
    return dict(first=terms['primary'], top3=top_three_scores(matrix), wdl=terms['wdl'],
                total=terms['total']['direction'])
