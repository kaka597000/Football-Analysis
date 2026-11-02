"""Egidi-style blend of a history λ matrix with Shin market probabilities.

Weights are three exponents / mixture weights, fit outside this file:

* w_cs mixes a Shin correct-score grid into the model matrix when that grid exists
* w_1x2 tilts the 1X2 marginals toward the Shin 1X2
* w_ou tilts P(total > 2.5) toward the Shin over/under price

Missing markets leave that component at the history matrix. Weights are not
applied to leagues they were not fit for; the walk-forward chooses the cell.
"""
import numpy as np

from dixon_coles import marginals


def _masks(cap):
    g = cap + 1
    goals = np.arange(g)
    home, away = np.meshgrid(goals, goals, indexing='ij')
    return home > away, home == away, home < away, (home + away) >= 3


def blend_batch(model, w_1x2, w_ou, w_cs, p_market_1x2, p_market_over, cs_grids):
    """Blend a batch that shares one weight triple.

    model: (n, g, g)
    p_market_1x2: (n, 3) with NaN when 1X2 is absent
    p_market_over: (n,) with NaN when OU 2.5 is absent
    cs_grids: sequence of arrays or None, length n
    """
    out = np.array(model, copy=True)
    n, g, _ = out.shape
    if w_cs > 0:
        for i, grid in enumerate(cs_grids):
            if grid is None:
                continue
            out[i] = (1.0 - w_cs) * out[i] + w_cs * grid
            total = out[i].sum()
            if total > 0:
                out[i] /= total
    mask_h, mask_d, mask_a, mask_over = _masks(g - 1)
    if w_1x2 > 0:
        model_p, _ = marginals(out)
        have = np.isfinite(p_market_1x2).all(axis=1)
        scale = np.ones((n, 3))
        if np.any(have):
            scale[have] = (p_market_1x2[have] / np.maximum(model_p[have], 1e-8)) ** w_1x2
        factor = np.ones_like(out)
        factor[:, mask_h] *= scale[:, 0:1]
        factor[:, mask_d] *= scale[:, 1:2]
        factor[:, mask_a] *= scale[:, 2:3]
        out = out * factor
        out /= np.maximum(out.sum(axis=(1, 2), keepdims=True), 1e-300)
    if w_ou > 0:
        _, pover = marginals(out)
        have = np.isfinite(p_market_over)
        if np.any(have):
            so = np.ones(n)
            su = np.ones(n)
            market_over = p_market_over[have]
            so[have] = (market_over / np.maximum(pover[have], 1e-8)) ** w_ou
            su[have] = ((1.0 - market_over) / np.maximum(1.0 - pover[have], 1e-8)) ** w_ou
            factor = np.ones_like(out)
            factor[:, mask_over] *= so[:, None]
            factor[:, ~mask_over] *= su[:, None]
            out = out * factor
            out /= np.maximum(out.sum(axis=(1, 2), keepdims=True), 1e-300)
    return out


def log_score(mats, home_goals, away_goals, cap):
    home_goals = np.asarray(home_goals, dtype=int)
    away_goals = np.asarray(away_goals, dtype=int)
    p = np.full(len(home_goals), 1e-8)
    ok = (home_goals >= 0) & (away_goals >= 0) & (home_goals <= cap) & (away_goals <= cap)
    ii = np.flatnonzero(ok)
    if len(ii):
        p[ii] = mats[ii, home_goals[ii], away_goals[ii]]
    return float(np.mean(np.log(np.maximum(p, 1e-300))))


ZERO_WEIGHT = {'w_1x2': 0.0, 'w_ou': 0.0, 'w_cs': 0.0, 'n': 0, 'log_score': None, 'source': 'zero'}
