"""Top-k hit rate, 1X2 ranked probability score, and exact McNemar tests."""
import numpy as np
from scipy.stats import binomtest


def outcome_index(home_goals, away_goals):
    home_goals = np.asarray(home_goals)
    away_goals = np.asarray(away_goals)
    return np.where(home_goals > away_goals, 0, np.where(home_goals == away_goals, 1, 2)).astype(int)


def rps_rows(probs, outcomes):
    """Constantinou–Fenton RPS for an ordered 3-way. Shape (n,). Lower is better."""
    probs = np.asarray(probs, dtype=float)
    outcomes = np.asarray(outcomes, dtype=int)
    cdf_p = np.cumsum(probs, axis=1)[:, :-1]
    cdf_o = np.cumsum(np.eye(3)[outcomes], axis=1)[:, :-1]
    return np.sum((cdf_p - cdf_o) ** 2, axis=1) / 2.0


def rps_matrix(mats, home_goals, away_goals, marginals_fn):
    probs, _ = marginals_fn(mats)
    return rps_rows(probs, outcome_index(home_goals, away_goals))


def hits_from_lists(home_top, away_top, true_home, true_away, ks=(1, 3)):
    """home_top/away_top are (n, k) integer scorelines, best first."""
    true_home = np.asarray(true_home)[:, None]
    true_away = np.asarray(true_away)[:, None]
    same = (home_top == true_home) & (away_top == true_away)
    out = {}
    for k in ks:
        width = min(k, same.shape[1])
        out[k] = same[:, :width].any(axis=1)
    return out


def mcnemar(candidate_hit, baseline_hit):
    """Exact two-sided McNemar on paired hits. Baseline entries may be None."""
    candidate_hit = np.asarray(candidate_hit, dtype=object)
    baseline_hit = np.asarray(baseline_hit, dtype=object)
    paired = np.array([b is not None for b in baseline_hit])
    if not np.any(paired):
        return {'n_paired': 0, 'n_candidate_hit_baseline_miss': 0,
                'n_candidate_miss_baseline_hit': 0, 'n_discordant': 0,
                'statistic': 0.0, 'p_value': None, 'test': 'exact_mcnemar_binomial'}
    cand = np.array([bool(x) for x in candidate_hit[paired]])
    base = np.array([bool(x) for x in baseline_hit[paired]])
    only_c = int(np.sum(cand & ~base))
    only_b = int(np.sum(~cand & base))
    discordant = only_c + only_b
    if discordant == 0:
        p_value = 1.0
        stat = 0.0
    else:
        p_value = float(binomtest(only_c, discordant, 0.5, alternative='two-sided').pvalue)
        stat = (only_c - only_b) ** 2 / discordant
    return {'n_paired': int(paired.sum()),
            'n_candidate_hit_baseline_miss': only_c,
            'n_candidate_miss_baseline_hit': only_b,
            'n_discordant': discordant,
            'statistic': float(stat),
            'p_value': p_value,
            'test': 'exact_mcnemar_binomial'}


def _rate(flags):
    flags = [f for f in flags if f is not None]
    if not flags:
        return None
    return float(np.mean(np.asarray(flags, dtype=float)))


def summarize(rows):
    """rows: dicts with dc/market/always/candidate hit and rps fields."""
    if not rows:
        return {'n': 0}

    def pack(prefix):
        h1 = [r[prefix + '_hit1'] for r in rows]
        h3 = [r[prefix + '_hit3'] for r in rows]
        rps = [r[prefix + '_rps'] for r in rows if r[prefix + '_rps'] is not None and r[prefix + '_hit1'] is not None]
        return {'n': int(sum(x is not None for x in h1)),
                'top1': _rate(h1),
                'top3': _rate(h3),
                'rps': None if not rps else float(np.mean(rps))}

    candidate_h1 = [r['candidate_hit1'] for r in rows]
    out = {'n': len(rows),
           'candidate': pack('candidate'),
           'dixon_coles': pack('dc'),
           'market_shin': pack('market'),
           'always_1_1': pack('always'),
           'mcnemar_top1': {
               'vs_dixon_coles': mcnemar(candidate_h1, [r['dc_hit1'] for r in rows]),
               'vs_market_shin': mcnemar(candidate_h1, [r['market_hit1'] for r in rows]),
               'vs_always_1_1': mcnemar(candidate_h1, [r['always_hit1'] for r in rows]),
           },
           'mcnemar_top3': {
               'vs_dixon_coles': mcnemar([r['candidate_hit3'] for r in rows], [r['dc_hit3'] for r in rows]),
               'vs_market_shin': mcnemar([r['candidate_hit3'] for r in rows], [r['market_hit3'] for r in rows]),
               'vs_always_1_1': mcnemar([r['candidate_hit3'] for r in rows], [r['always_hit3'] for r in rows]),
           }}
    return out
