"""Add a soft quoted Asian handicap share to an exact 1X2 and total score projection."""

import numpy as np
from scipy.optimize import brentq

from . import three_arm_model as score

NAMES = ('latest_only', 'latest_plus_ah')
RIDGE = 0.01
PROTOCOL = '46d24970d1ad603c6621260c48828c9151b7159560e797e649d6fc2ffc4b6c36'


def stake_result(goal_difference, line):
    """Net won units at even money, splitting quarter goals across two handicaps."""
    if not np.isfinite(line) or not float(line * 4).is_integer():
        raise ValueError('finite quarter-step home Asian handicap required')
    quarter = not float(2 * line).is_integer()
    legs = (line - 0.25, line + 0.25) if quarter else (line,)
    return sum(np.sign(goal_difference + leg) for leg in legs) / len(legs)


def project(base, market, handicap):
    if not market.get('bookmaker') or handicap['bookmaker'] != market['bookmaker']:
        raise ValueError('same bookmaker needed for the closing handicap')
    prices = np.asarray([handicap['home'], handicap['away']], dtype=float)
    if not np.isfinite(prices).all() or min(prices) <= 1:
        raise ValueError('complete decimal odds for both sides required')
    quote = score._quote_values(market, None, proxy=True)
    latest = score._market_matrix(score._matrix(base), quote)
    share = (1 / prices[0]) / (1 / prices).sum()
    h, a = np.indices(latest.shape)
    stake = stake_result(h - a, handicap['line'])
    feature = np.maximum(stake, 0.0) - share * np.abs(stake)

    def adjustment(coef):
        tilted = latest * np.exp(coef * feature)
        return score._market_matrix(tilted / tilted.sum(), quote)

    def equation(coef):
        return float((adjustment(coef) * feature).sum() + RIDGE * coef)

    coef = brentq(equation, -1 / RIDGE, 1 / RIDGE, xtol=1e-10)
    added = adjustment(coef)

    def implied_share(m):
        active = float(np.sum(m * np.abs(stake)))
        return float(np.sum(m * np.maximum(stake, 0.0)) / active)

    return {NAMES[0]: latest, NAMES[1]: added}, {
        'home_target_share': float(share),
        'home_reference_share': implied_share(latest),
        'home_adjusted_share': implied_share(added),
        'soft_home_handicap_coefficient': float(coef),
    }
