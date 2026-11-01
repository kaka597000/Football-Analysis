"""Shin (1992/1993) de-vig and a history-free market score grid.

Closing prices are preferred when the source marks them. Correct-score
prices are used only when the row actually contains them. Nothing here
invents a price.
"""
import math

import numpy as np

from dixon_coles import bivariate_matrices, marginals


def parse_odd(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
    if not math.isfinite(number) or number <= 1.01:
        return None
    return number


def shin_probabilities(odds):
    """Return (probabilities, insider_proportion z). Probabilities sum to 1.

    q_i = 1/o_i, S = sum q. z solves
        sum_i (sqrt(z^2 + 4(1-z) q_i^2 / S) - z) / (2(1-z)) = 1.
    A fair book (S == 1) returns the implied probabilities and z = 0.
    """
    odds = np.asarray(odds, dtype=float)
    if odds.ndim != 1 or odds.size < 2 or np.any(~np.isfinite(odds)) or np.any(odds <= 1.0):
        raise ValueError('Shin input must be at least two finite decimal odds above 1')
    quoted = 1.0 / odds
    total = float(quoted.sum())
    if total <= 1.0 + 1e-12:
        probs = quoted / quoted.sum()
        return probs, 0.0

    def summed(z):
        disc = z * z + 4.0 * (1.0 - z) * quoted * quoted / total
        return float(np.sum((np.sqrt(np.maximum(disc, 0.0)) - z) / (2.0 * (1.0 - z))))

    lo, hi = 1e-10, 0.5
    if summed(hi) > 1.0:
        hi = 0.9
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if summed(mid) > 1.0:
            lo = mid
        else:
            hi = mid
    z = 0.5 * (lo + hi)
    disc = z * z + 4.0 * (1.0 - z) * quoted * quoted / total
    probs = (np.sqrt(np.maximum(disc, 0.0)) - z) / (2.0 * (1.0 - z))
    probs = probs / probs.sum()
    return probs, float(z)


def first_complete(row, groups):
    for keys in groups:
        values = [parse_odd(row.get(k) if isinstance(row, dict) else None) for k in keys]
        if all(v is not None for v in values):
            return tuple(values)
    return None


CLOSING_1X2 = (
    ('PSCH', 'PSCD', 'PSCA'),
    ('B365CH', 'B365CD', 'B365CA'),
    ('AvgCH', 'AvgCD', 'AvgCA'),
    ('MaxCH', 'MaxCD', 'MaxCA'),
)
OPEN_1X2 = (
    ('PSH', 'PSD', 'PSA'),
    ('B365H', 'B365D', 'B365A'),
    ('AvgH', 'AvgD', 'AvgA'),
    ('BbAvH', 'BbAvD', 'BbAvA'),
    ('WHH', 'WHD', 'WHA'),
    ('IWH', 'IWD', 'IWA'),
    ('VCH', 'VCD', 'VCA'),
    ('GBH', 'GBD', 'GBA'),
)
CLOSING_OU25 = (
    ('PC>2.5', 'PC<2.5'),
    ('B365C>2.5', 'B365C<2.5'),
    ('AvgC>2.5', 'AvgC<2.5'),
    ('MaxC>2.5', 'MaxC<2.5'),
)
OPEN_OU25 = (
    ('P>2.5', 'P<2.5'),
    ('B365>2.5', 'B365<2.5'),
    ('Avg>2.5', 'Avg<2.5'),
    ('BbAv>2.5', 'BbAv<2.5'),
    ('Max>2.5', 'Max<2.5'),
    ('GB>2.5', 'GB<2.5'),
)


def odds_from_mapping(row):
    """Parse a flat football-data style row or an explicit research record.

    Returns a dict with keys odds_1x2, odds_1x2_closing, odds_ou, odds_ou_closing,
    odds_cs. Missing markets are None. Correct-score keys are recognised only
    when present; they are never filled in.
    """
    if not isinstance(row, dict):
        return None
    out = {'odds_1x2': None, 'odds_1x2_closing': False, 'odds_ou': None,
           'odds_ou_closing': False, 'odds_cs': None}
    if row.get('odds_1x2') is not None:
        triple = row['odds_1x2']
        if len(triple) == 3:
            parsed = tuple(parse_odd(x) for x in triple)
            if all(parsed):
                out['odds_1x2'] = parsed
                out['odds_1x2_closing'] = bool(row.get('odds_1x2_closing', False))
    if out['odds_1x2'] is None:
        closing = first_complete(row, CLOSING_1X2)
        if closing:
            out['odds_1x2'] = closing
            out['odds_1x2_closing'] = True
        else:
            opened = first_complete(row, OPEN_1X2)
            if opened:
                out['odds_1x2'] = opened
    if row.get('odds_ou') is not None:
        pair = row['odds_ou']
        if len(pair) == 2:
            parsed = tuple(parse_odd(x) for x in pair)
            if all(parsed):
                out['odds_ou'] = parsed
                out['odds_ou_closing'] = bool(row.get('odds_ou_closing', False))
    if out['odds_ou'] is None:
        closing = first_complete(row, CLOSING_OU25)
        if closing:
            out['odds_ou'] = closing
            out['odds_ou_closing'] = True
        else:
            opened = first_complete(row, OPEN_OU25)
            if opened:
                out['odds_ou'] = opened
    cs = {}
    raw_cs = row.get('odds_cs')
    if isinstance(raw_cs, dict):
        for key, value in raw_cs.items():
            text = str(key)
            if '-' not in text:
                continue
            left, right = text.split('-', 1)
            try:
                i, j = int(left), int(right)
            except ValueError:
                continue
            odd = parse_odd(value)
            if odd is not None and i >= 0 and j >= 0:
                cs[(i, j)] = odd
    out['odds_cs'] = cs or None
    return out


def shin_1x2(odds_triple):
    if not odds_triple:
        return None
    probs, _ = shin_probabilities(list(odds_triple))
    return probs


def shin_ou(odds_pair):
    if not odds_pair:
        return None
    probs, _ = shin_probabilities(list(odds_pair))
    return float(probs[0])


def cs_grid_from_odds(odds_cs, cap, min_prices=8):
    """Shin over quoted correct scores. None when fewer than min_prices are usable.

    Unquoted scores stay at 0. The quoted cells sum to 1. That is the book's
    grid, not a completed one.
    """
    if not odds_cs:
        return None
    cells = []
    prices = []
    for (i, j), odd in odds_cs.items():
        if 0 <= i <= cap and 0 <= j <= cap and odd > 1.0:
            cells.append((i, j))
            prices.append(odd)
    if len(cells) < min_prices:
        return None
    probs, _ = shin_probabilities(prices)
    grid = np.zeros((cap + 1, cap + 1))
    for (i, j), prob in zip(cells, probs):
        grid[i, j] = prob
    return grid


_MARKET_TABLES = {}


def poisson_market_table(cap):
    """History-free independent Poisson catalog used to match Shin 1X2/OU."""
    if cap in _MARKET_TABLES:
        return _MARKET_TABLES[cap]
    grid = np.linspace(0.25, 3.6, 22)
    lh = np.repeat(grid, len(grid))
    la = np.tile(grid, len(grid))
    mats = bivariate_matrices(lh, la, 0.0, cap)
    one_x_two, pover = marginals(mats)
    table = {'lh': lh, 'la': la, 'mats': mats, 'wdl': one_x_two, 'over': pover}
    _MARKET_TABLES[cap] = table
    return table


def market_matrices(odds_1x2, odds_ou, odds_cs, cap):
    """Batch market grids.

    Preference: Shin correct-score grid when at least 8 prices exist; otherwise
    an independent Poisson grid whose 1X2 (and 2.5-goal) probabilities match
    the Shin market. Rows without 1X2 are marked unavailable.
    """
    n = len(odds_1x2)
    g = cap + 1
    mats = np.zeros((n, g, g))
    available = np.zeros(n, dtype=bool)
    from_cs = np.zeros(n, dtype=bool)
    table = poisson_market_table(cap)
    p_wdl = np.full((n, 3), np.nan)
    p_over = np.full(n, np.nan)
    for i, triple in enumerate(odds_1x2):
        if triple is None:
            continue
        p_wdl[i] = shin_1x2(triple)
        if odds_ou[i] is not None:
            p_over[i] = shin_ou(odds_ou[i])
        grid = cs_grid_from_odds(odds_cs[i], cap)
        if grid is not None:
            mats[i] = grid
            available[i] = True
            from_cs[i] = True
    need = np.array([odds_1x2[i] is not None and not from_cs[i] for i in range(n)])
    if np.any(need):
        err = ((table['wdl'][None, :, 0] - p_wdl[:, None, 0]) ** 2
               + (table['wdl'][None, :, 1] - p_wdl[:, None, 1]) ** 2
               + (table['wdl'][None, :, 2] - p_wdl[:, None, 2]) ** 2)
        over_term = (table['over'][None, :] - p_over[:, None]) ** 2
        use_over = np.isfinite(p_over)
        err = err + np.where(use_over[:, None], over_term, 0.0)
        pick = np.argmin(err, axis=1)
        mats[need] = table['mats'][pick[need]]
        available[need] = True
    return mats, available, from_cs, p_wdl, p_over
