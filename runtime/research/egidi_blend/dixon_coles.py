"""Dixon–Coles low-score correction on a diagonal-inflated bivariate Poisson.

Fresh research implementation. It does not import v2–v7, weighted_model, or ml_model.

The score matrix for home rate λh and away rate λa is

    P(x, y) ∝ τ_ρ(x, y) * [(1 - π) BP(x, y; λ1, λ2, λ3) + π * 1_{x=y} D(x)]

where λ3 = share * min(λh, λa), λ1 = λh - λ3, λ2 = λa - λ3, BP is the
Karlis–Ntzoufras bivariate Poisson, D is that distribution's diagonal
renormalised to a draw distribution, π is the diagonal-inflation weight,
and τ_ρ is the Dixon–Coles dependence factor on the four low scores
(0-0, 0-1, 1-0, 1-1). Team rates are a weighted independent-Poisson
attack/defence model; share, ρ and π are then chosen on a fixed grid.
"""
import math
from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize
from scipy.stats import poisson


def _axes(cap):
    goals = np.arange(cap + 1)
    home, away = np.meshgrid(goals, goals, indexing='ij')
    return home, away


def bivariate_matrices(lh, la, share, cap):
    """Truncated bivariate Poisson, renormalised inside 0..cap. Shape (n, g, g)."""
    lh = np.asarray(lh, dtype=float)
    la = np.asarray(la, dtype=float)
    n = len(lh)
    g = cap + 1
    ks = np.arange(g)
    shared = float(share) * np.minimum(lh, la)
    l1 = np.maximum(lh - shared, 1e-6)
    l2 = np.maximum(la - shared, 1e-6)
    p1 = poisson.pmf(ks[None, :], l1[:, None])
    p2 = poisson.pmf(ks[None, :], l2[:, None])
    m = np.zeros((n, g, g))
    if share <= 0:
        m = p1[:, :, None] * p2[:, None, :]
    else:
        p3 = poisson.pmf(ks[None, :], np.maximum(shared, 1e-8)[:, None])
        for k in range(g):
            w = p3[:, k][:, None, None]
            m[:, k:, k:] += w * p1[:, :g - k][:, :, None] * p2[:, :g - k][:, None, :]
    m = np.clip(m, 0.0, None)
    m /= np.maximum(m.sum(axis=(1, 2), keepdims=True), 1e-300)
    return m


def inflate_diagonal(m, pi):
    if pi <= 0:
        return m
    g = m.shape[1]
    idx = np.arange(g)
    diag = m[:, idx, idx]
    mass = np.maximum(diag.sum(axis=1, keepdims=True), 1e-15)
    out = (1.0 - pi) * m
    out[:, idx, idx] += pi * (diag / mass)
    return out


def apply_rho(m, lh, la, rho):
    """Dixon–Coles τ on the low-score cells. None when any τ is non-positive."""
    if rho == 0:
        return m
    lh = np.asarray(lh, dtype=float)
    la = np.asarray(la, dtype=float)
    t00 = 1.0 - lh * la * rho
    t01 = 1.0 + lh * rho
    t10 = 1.0 + la * rho
    t11 = 1.0 - rho
    if np.any(t00 <= 0) or np.any(t01 <= 0) or np.any(t10 <= 0) or t11 <= 0:
        return None
    out = np.array(m, copy=True)
    out[:, 0, 0] *= t00
    out[:, 0, 1] *= t01
    out[:, 1, 0] *= t10
    out[:, 1, 1] *= t11
    out /= np.maximum(out.sum(axis=(1, 2), keepdims=True), 1e-300)
    return out


def score_matrices(lh, la, rho, share, pi, cap, rate_clip=(0.05, 5.0)):
    lh = np.clip(np.asarray(lh, dtype=float), rate_clip[0], rate_clip[1])
    la = np.clip(np.asarray(la, dtype=float), rate_clip[0], rate_clip[1])
    base = inflate_diagonal(bivariate_matrices(lh, la, share, cap), pi)
    out = apply_rho(base, lh, la, rho)
    if out is None:
        raise ValueError('Dixon-Coles rho is not positive for these rates')
    return out


def mean_log_prob(mats, hg, ag, cap):
    hg = np.asarray(hg, dtype=int)
    ag = np.asarray(ag, dtype=int)
    p = np.full(len(hg), 1e-8)
    ok = (hg >= 0) & (ag >= 0) & (hg <= cap) & (ag <= cap)
    ii = np.flatnonzero(ok)
    if len(ii):
        p[ii] = mats[ii, hg[ii], ag[ii]]
    return float(np.mean(np.log(np.maximum(p, 1e-300))))


def poisson_objective(home, away, hg, ag, weights, hflag, n_teams, ridge):
    """Weighted independent-Poisson attack/defence loss and analytic gradient."""
    def objective(v):
        mu = v[0]
        home_adv = v[1]
        att = v[2:2 + n_teams]
        deff = v[2 + n_teams:]
        att = att - att.mean()
        deff = deff - deff.mean()
        log_lh = mu + home_adv * hflag + att[home] + deff[away]
        log_la = mu + att[away] + deff[home]
        lh = np.exp(log_lh)
        la = np.exp(log_la)
        nll = float(np.sum(weights * (lh - hg * log_lh + la - ag * log_la)))
        nll += float(ridge * (att @ att + deff @ deff))
        rh = weights * (lh - hg)
        ra = weights * (la - ag)
        g_att = (np.bincount(home, weights=rh, minlength=n_teams)
                 + np.bincount(away, weights=ra, minlength=n_teams))
        g_def = (np.bincount(away, weights=rh, minlength=n_teams)
                 + np.bincount(home, weights=ra, minlength=n_teams))
        g_att = g_att - g_att.mean() + 2.0 * ridge * att
        g_def = g_def - g_def.mean() + 2.0 * ridge * deff
        grad = np.concatenate([[np.sum(rh + ra), np.sum(rh * hflag)], g_att, g_def])
        return nll, grad
    return objective


def fit_strength(home, away, hg, ag, weights, hflag, n_teams, ridge, warm=None):
    objective = poisson_objective(home, away, hg, ag, weights, hflag, n_teams, ridge)
    v0 = np.zeros(2 + 2 * n_teams)
    v0[0] = math.log(max(float(np.average(np.concatenate([hg, ag]),
                                          weights=np.concatenate([weights, weights]))), 0.15))
    away_mean = max(float(np.average(ag, weights=weights)), 0.15)
    home_mean = max(float(np.average(hg, weights=weights)), 0.15)
    v0[1] = float(np.clip(math.log(home_mean / away_mean), -0.2, 0.6))
    if warm is not None and len(warm) == len(v0):
        v0 = np.asarray(warm, dtype=float).copy()
    bounds = [(-0.4, 1.3), (-0.3, 0.8)] + [(-1.6, 1.6)] * (2 * n_teams)
    fit = minimize(objective, v0, jac=True, method='L-BFGS-B', bounds=bounds,
                   options={'maxiter': 160, 'ftol': 1e-10})
    if not np.isfinite(fit.fun):
        raise ValueError('strength fit diverged')
    mu, home_adv = float(fit.x[0]), float(fit.x[1])
    att = fit.x[2:2 + n_teams]
    deff = fit.x[2 + n_teams:]
    att = att - att.mean()
    deff = deff - deff.mean()
    return mu, home_adv, att, deff, bool(fit.success), fit.x.copy()


def fit_nuisance(lh, la, hg, ag, config):
    """Grid-search share, Dixon–Coles ρ and diagonal inflation on fixed rates."""
    best = None
    best_score = -np.inf
    for share in config.share_grid:
        for pi in config.pi_grid:
            base = inflate_diagonal(
                bivariate_matrices(np.clip(lh, *config.rate_clip), np.clip(la, *config.rate_clip),
                                   share, config.cap),
                pi)
            for rho in config.rho_grid:
                adj = apply_rho(base, np.clip(lh, *config.rate_clip), np.clip(la, *config.rate_clip), rho)
                if adj is None:
                    continue
                score = mean_log_prob(adj, hg, ag, config.cap)
                if score > best_score:
                    best_score = score
                    best = (float(rho), float(share), float(pi), score)
    if best is None:
        return 0.0, 0.0, 0.0, None
    return best


def time_weights(dates, half_life_days):
    """dates: list of datetime.date. Newer matches weigh more. Half-life is fixed."""
    end = max(dates)
    age = np.array([(end - d).days for d in dates], dtype=float)
    return np.power(0.5, age / float(half_life_days))


@dataclass
class ScoreModel:
    teams: list
    mu: float
    home_advantage: float
    attack: dict
    defence: dict
    rho: float
    share: float
    pi: float
    cap: int
    rate_clip: tuple
    train_rows: int
    train_end: str
    half_life_days: float
    converged: bool
    nuisance_log_score: float

    def rates(self, home_names, away_names, neutral):
        n = len(home_names)
        att_h = np.array([self.attack.get(t, 0.0) for t in home_names])
        att_a = np.array([self.attack.get(t, 0.0) for t in away_names])
        def_h = np.array([self.defence.get(t, 0.0) for t in home_names])
        def_a = np.array([self.defence.get(t, 0.0) for t in away_names])
        cold = np.array([(h not in self.attack) or (a not in self.attack)
                         for h, a in zip(home_names, away_names)])
        neutral = np.asarray(neutral, dtype=bool)
        log_lh = self.mu + np.where(neutral, 0.0, self.home_advantage) + att_h + def_a
        log_la = self.mu + att_a + def_h
        return np.exp(log_lh), np.exp(log_la), cold


def _pack(matches):
    from datetime import date as date_cls
    teams = sorted({m.home for m in matches} | {m.away for m in matches})
    index = {t: i for i, t in enumerate(teams)}
    home = np.array([index[m.home] for m in matches], dtype=int)
    away = np.array([index[m.away] for m in matches], dtype=int)
    hg = np.array([m.home_goals for m in matches], dtype=float)
    ag = np.array([m.away_goals for m in matches], dtype=float)
    neutral = np.array([1.0 if m.neutral else 0.0 for m in matches])
    dates = [date_cls.fromisoformat(m.date) for m in matches]
    return teams, index, home, away, hg, ag, neutral, dates


def warm_vector(model, teams):
    if model is None:
        return None
    n = len(teams)
    v = np.zeros(2 + 2 * n)
    v[0] = model.mu
    v[1] = model.home_advantage
    for i, team in enumerate(teams):
        v[2 + i] = model.attack.get(team, 0.0)
        v[2 + n + i] = model.defence.get(team, 0.0)
    return v


def fit_score_model(matches, config, warm=None):
    """Fit one league on the supplied matches only. Returns None if the sample is thin."""
    if len(matches) < config.min_train_matches:
        return None
    teams, index, home, away, hg, ag, neutral, dates = _pack(matches)
    weights = time_weights(dates, config.half_life_days)
    hflag = 1.0 - neutral
    mu, home_adv, att, deff, converged, _ = fit_strength(
        home, away, hg, ag, weights, hflag, len(teams), config.ridge, warm_vector(warm, teams))
    attack = {t: float(att[i]) for i, t in enumerate(teams)}
    defence = {t: float(deff[i]) for i, t in enumerate(teams)}
    names_h = [m.home for m in matches]
    names_a = [m.away for m in matches]
    neutral_b = [m.neutral for m in matches]
    provisional = ScoreModel(
        teams=teams, mu=mu, home_advantage=home_adv, attack=attack, defence=defence,
        rho=0.0, share=0.0, pi=0.0, cap=config.cap, rate_clip=config.rate_clip,
        train_rows=len(matches), train_end=max(m.date for m in matches),
        half_life_days=config.half_life_days, converged=converged, nuisance_log_score=None)
    lh, la, _ = provisional.rates(names_h, names_a, neutral_b)
    rho, share, pi, nll = fit_nuisance(lh, la, hg.astype(int), ag.astype(int), config)
    provisional.rho = rho
    provisional.share = share
    provisional.pi = pi
    provisional.nuisance_log_score = nll
    return provisional


def predict_matrices(model, matches):
    """Return (lh, la, cold, matrices) for matches, cold-start teams at league average."""
    if not matches:
        g = model.cap + 1
        return np.zeros(0), np.zeros(0), np.zeros(0, dtype=bool), np.zeros((0, g, g))
    lh, la, cold = model.rates([m.home for m in matches], [m.away for m in matches],
                               [m.neutral for m in matches])
    mats = score_matrices(lh, la, model.rho, model.share, model.pi, model.cap, model.rate_clip)
    return lh, la, cold, mats


def marginals(mats):
    """1X2 and P(total >= 3) for a batch of score matrices."""
    g = mats.shape[1]
    home, away = _axes(g - 1)
    ph = (mats * (home > away)).sum(axis=(1, 2))
    pd = (mats * (home == away)).sum(axis=(1, 2))
    pa = (mats * (home < away)).sum(axis=(1, 2))
    pover = (mats * ((home + away) >= 3)).sum(axis=(1, 2))
    return np.column_stack([ph, pd, pa]), pover


def topk_scores(mats, k):
    """Deterministic top-k scorelines. Returns home, away, prob arrays of shape (n, k)."""
    n, g, _ = mats.shape
    k = min(k, g * g)
    flat = mats.reshape(n, -1).copy()
    flat -= np.arange(flat.shape[1], dtype=float) * 1e-15
    idx = np.argpartition(flat, -k, axis=1)[:, -k:]
    part = np.take_along_axis(flat, idx, axis=1)
    order = np.argsort(-part, axis=1)
    idx = np.take_along_axis(idx, order, axis=1)
    prob = np.take_along_axis(mats.reshape(n, -1), idx, axis=1)
    return idx // g, idx % g, prob
