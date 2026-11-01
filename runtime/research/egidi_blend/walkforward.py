"""Rolling-origin walk-forward for the Europe top five, with Asian leagues held out.

Blend weights and the LambdaMART ranker are fit only on Europe out-of-fold
rows whose match date is strictly before the test cutoff. Asian leagues get
their own Dixon–Coles fit and the already-fit Europe weights/ranker. They
never enter those training sets.
"""
import hashlib
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from blend import ZERO_WEIGHT, blend_batch, log_score
from constants import EUROPE, OOD_ASIA, VERSION, era_of
from dixon_coles import fit_score_model, marginals, predict_matrices, score_matrices, topk_scores
from market import cs_grid_from_odds, market_matrices
from metrics import hits_from_lists, outcome_index, rps_rows, summarize
from ranker import feature_matrix, labels_for, order_by_scores, rank_scores, train_ranker


@dataclass
class OofRow:
    match: object
    lh: float
    la: float
    rho: float
    share: float
    pi: float
    cold: bool


def source_hash():
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).resolve().parent.glob('*.py')):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def compute_prior_xg(matches):
    """Strictly earlier dates only. Same-day matches do not see each other."""
    totals = defaultdict(lambda: [0.0, 0])
    by_date = defaultdict(list)
    for match in matches:
        by_date[match.date].append(match)
    out = {}
    for day in sorted(by_date):
        for match in by_date[day]:
            home = totals[(match.league, match.home)]
            away = totals[(match.league, match.away)]
            if home[1] and away[1]:
                out[match.match_id] = home[0] / home[1] - away[0] / away[1]
            else:
                out[match.match_id] = None
        for match in by_date[day]:
            if match.home_xg is None or match.away_xg is None:
                continue
            totals[(match.league, match.home)][0] += match.home_xg
            totals[(match.league, match.home)][1] += 1
            totals[(match.league, match.away)][0] += match.away_xg
            totals[(match.league, match.away)][1] += 1
    return out


def build_oof(matches, config):
    """Out-of-fold rates. Each prediction is fit only on earlier dates of this league."""
    matches = sorted(matches, key=lambda m: (m.date, m.match_id))
    if not matches:
        return []
    blocks = []
    for match in matches:
        era = era_of(match.season_start)
        if not blocks or blocks[-1][0] != era:
            blocks.append((era, []))
        blocks[-1][1].append(match)
    cache = {}

    def fit_before(cutoff):
        if cutoff not in cache:
            train = [m for m in matches if m.date < cutoff]
            cache[cutoff] = fit_score_model(train, config) if len(train) >= config.min_train_matches else None
        return cache[cutoff]

    rows = []
    for era, group in blocks:
        cutoff = group[0].date
        model = fit_before(cutoff)
        targets = group
        if model is None:
            seasons = sorted({m.season_start for m in group})
            if len(seasons) < 2:
                continue
            mid = seasons[len(seasons) // 2]
            cutoff = min(m.date for m in group if m.season_start == mid)
            model = fit_before(cutoff)
            targets = [m for m in group if m.date >= cutoff]
            if model is None:
                continue
        if any(m.date < cutoff for m in targets):
            raise RuntimeError('OOF target precedes its fit cutoff')
        lh, la, cold = model.rates([m.home for m in targets], [m.away for m in targets],
                                   [m.neutral for m in targets])
        for i, match in enumerate(targets):
            rows.append(OofRow(match, float(lh[i]), float(la[i]), model.rho, model.share, model.pi, bool(cold[i])))
    return rows


def _model_matrices(rows, config):
    g = config.cap + 1
    mats = np.zeros((len(rows), g, g))
    buckets = defaultdict(list)
    for i, row in enumerate(rows):
        buckets[(row.rho, row.share, row.pi)].append(i)
    for (rho, share, pi), idxs in buckets.items():
        idxs = np.asarray(idxs, dtype=int)
        lh = np.array([rows[i].lh for i in idxs])
        la = np.array([rows[i].la for i in idxs])
        mats[idxs] = score_matrices(lh, la, rho, share, pi, config.cap, config.rate_clip)
    return mats


def _market_views(matches, cap):
    odds_1x2 = [m.odds_1x2 for m in matches]
    odds_ou = [m.odds_ou for m in matches]
    odds_cs = [m.odds_cs for m in matches]
    mats, available, from_cs, p_wdl, p_over = market_matrices(odds_1x2, odds_ou, odds_cs, cap)
    grids = []
    for i, match in enumerate(matches):
        grids.append(cs_grid_from_odds(match.odds_cs, cap) if from_cs[i] else None)
    return mats, available, from_cs, p_wdl, p_over, grids


def _best_weight(model_mats, matches, p_wdl, p_over, cs_grids, config):
    home = np.array([m.home_goals for m in matches])
    away = np.array([m.away_goals for m in matches])
    any_cs = any(grid is not None for grid in cs_grids)
    w_cs_values = config.weight_grid_cs if any_cs else (0.0,)
    w_ou_values = config.weight_grid_ou if np.isfinite(p_over).any() else (0.0,)
    w_1x2_values = config.weight_grid_1x2 if np.isfinite(p_wdl).all(axis=1).any() else (0.0,)
    best = None
    for w_1x2 in w_1x2_values:
        for w_ou in w_ou_values:
            for w_cs in w_cs_values:
                blended = blend_batch(model_mats, w_1x2, w_ou, w_cs, p_wdl, p_over, cs_grids)
                score = log_score(blended, home, away, config.cap)
                rank = (score, -(w_1x2 + w_ou + w_cs), -w_1x2, -w_ou, -w_cs)
                if best is None or rank > best[0]:
                    best = (rank, {'w_1x2': float(w_1x2), 'w_ou': float(w_ou), 'w_cs': float(w_cs),
                                   'n': int(len(matches)), 'log_score': score})
    return best[1]


def _slice_market(market, count):
    mats, available, from_cs, p_wdl, p_over, grids = market
    return mats[:count], available[:count], from_cs[:count], p_wdl[:count], p_over[:count], grids[:count]


def select_weights(oof_rows, config, model_mats=None, market=None):
    even_idx = [i for i, row in enumerate(oof_rows) if row.match.season_start % 2 == 0]
    used_even = len(even_idx) >= config.min_weight_matches
    idxs = even_idx if used_even else list(range(len(oof_rows)))
    rows = [oof_rows[i] for i in idxs]
    meta = {'even_seasons_only': used_even, 'n': len(rows),
            'leagues': sorted({row.match.league for row in rows})}
    if any(league in OOD_ASIA for league in meta['leagues']):
        raise RuntimeError('Asian OOD rows entered blend-weight fitting')
    if len(rows) < 8:
        table = {('*', '*'): dict(ZERO_WEIGHT, n=len(rows))}
        return table, meta
    if model_mats is None or market is None:
        model_mats = _model_matrices(oof_rows, config)
        market = _market_views([row.match for row in oof_rows], config.cap)
    _, _, _, p_wdl, p_over, cs_grids = market
    chosen = np.asarray(idxs, dtype=int)
    model_mats = model_mats[chosen]
    p_wdl = p_wdl[chosen]
    p_over = p_over[chosen]
    cs_grids = [cs_grids[i] for i in idxs]
    rows = [oof_rows[i] for i in idxs]
    groups = defaultdict(list)
    for i, row in enumerate(rows):
        era = era_of(row.match.season_start)
        groups[(row.match.league, era)].append(i)
        groups[(row.match.league, '*')].append(i)
        groups[('*', era)].append(i)
        groups[('*', '*')].append(i)
    table = {}
    for key, idxs in groups.items():
        if len(idxs) < config.min_weight_matches and key != ('*', '*'):
            continue
        if len(idxs) < 8:
            continue
        idxs = np.asarray(idxs, dtype=int)
        record = _best_weight(model_mats[idxs], [rows[i].match for i in idxs],
                              p_wdl[idxs], p_over[idxs], [cs_grids[i] for i in idxs], config)
        if key[0] == '*' and key[1] == '*':
            record['source'] = 'europe_pooled'
        elif key[0] == '*':
            record['source'] = 'europe_era'
        elif key[1] == '*':
            record['source'] = 'league_pooled'
        else:
            record['source'] = 'league_era'
        table[key] = record
    if ('*', '*') not in table:
        table[('*', '*')] = dict(ZERO_WEIGHT, n=len(rows))
    meta['cells'] = len(table)
    return table, meta


def resolve_weight(table, league, era, allow_league_key, min_n):
    keys = []
    if allow_league_key:
        keys.extend([(league, era), (league, '*')])
    keys.extend([('*', era), ('*', '*')])
    for key in keys:
        record = table.get(key)
        if record and record.get('log_score') is not None and record.get('n', 0) >= min_n:
            return record, key
        if record and key == ('*', '*') and record.get('log_score') is not None:
            return record, key
    return dict(ZERO_WEIGHT), None


def _softmax_redistribute(mats, home, away, scores):
    out = np.array(mats, copy=True)
    n, k = home.shape
    g = out.shape[1]
    shifted = scores - np.max(scores, axis=1, keepdims=True)
    weights = np.exp(shifted)
    weights /= np.maximum(weights.sum(axis=1, keepdims=True), 1e-300)
    for i in range(n):
        flat = out[i].reshape(-1)
        cells = home[i] * g + away[i]
        mass = float(flat[cells].sum())
        flat[cells] = mass * weights[i]
    return out


def _flag_array(matches, predicate):
    return np.array([1.0 if predicate(m) else 0.0 for m in matches])


def evaluate_matches(matches, model, weight_table, ranker, prior_xg, config, allow_league, ood):
    if not matches:
        return []
    lh, la, cold, model_mats = predict_matrices(model, matches)
    market_mats, available, from_cs, p_wdl, p_over, cs_grids = _market_views(matches, config.cap)
    blended = np.empty_like(model_mats)
    buckets = defaultdict(list)
    chosen = []
    for i, match in enumerate(matches):
        record, key = resolve_weight(weight_table, match.league, era_of(match.season_start),
                                     allow_league, config.min_weight_matches)
        chosen.append((record, key))
        buckets[(record['w_1x2'], record['w_ou'], record['w_cs'])].append(i)
    for weights, idxs in buckets.items():
        idxs = np.asarray(idxs, dtype=int)
        blended[idxs] = blend_batch(model_mats[idxs], weights[0], weights[1], weights[2],
                                    p_wdl[idxs], p_over[idxs], [cs_grids[i] for i in idxs])
    true_h = np.array([m.home_goals for m in matches])
    true_a = np.array([m.away_goals for m in matches])
    dc_h, dc_a, _ = topk_scores(model_mats, config.top_k)
    bl_h, bl_a, bl_p = topk_scores(blended, config.top_k)
    mk_h, mk_a, mk_p = topk_scores(np.where(available[:, None, None], market_mats, 0.0), config.top_k)
    market_cell = np.zeros_like(bl_p)
    market_missing = np.ones_like(bl_p)
    for i in range(len(matches)):
        if not available[i]:
            continue
        for j in range(bl_h.shape[1]):
            market_cell[i, j] = market_mats[i, bl_h[i, j], bl_a[i, j]]
            market_missing[i, j] = 0.0
    model_cell = np.zeros_like(bl_p)
    for i in range(len(matches)):
        for j in range(bl_h.shape[1]):
            model_cell[i, j] = model_mats[i, bl_h[i, j], bl_a[i, j]]
    prior = np.array([0.0 if prior_xg.get(m.match_id) is None else prior_xg[m.match_id] for m in matches])
    missing_xg = np.array([1.0 if prior_xg.get(m.match_id) is None else 0.0 for m in matches])
    features = feature_matrix(
        bl_h, bl_a, bl_p, model_cell, market_cell, market_missing, lh, la,
        _flag_array(matches, lambda m: m.odds_1x2 is None),
        _flag_array(matches, lambda m: m.odds_ou is None),
        _flag_array(matches, lambda m: m.odds_cs is None),
        _flag_array(matches, lambda m: m.odds_1x2_closing or m.odds_ou_closing),
        missing_xg, prior,
        _flag_array(matches, lambda m: not m.lineup_known))
    scores = rank_scores(ranker, features)
    if ranker is None:
        cand_h, cand_a = bl_h, bl_a
        cand_mats = blended
    else:
        cand_mats = _softmax_redistribute(blended, bl_h, bl_a, scores)
        cand_h, cand_a, _, _ = order_by_scores(bl_h, bl_a, bl_p, scores)
    dc_hits = hits_from_lists(dc_h, dc_a, true_h, true_a)
    cand_hits = hits_from_lists(cand_h, cand_a, true_h, true_a)
    mk_hits = hits_from_lists(mk_h, mk_a, true_h, true_a)
    always_h = np.ones((len(matches), 1), dtype=int)
    always_a = np.ones((len(matches), 1), dtype=int)
    always_hits = hits_from_lists(always_h, always_a, true_h, true_a)
    dc_rps = rps_rows(marginals(model_mats)[0], outcome_index(true_h, true_a))
    cand_rps = rps_rows(marginals(cand_mats)[0], outcome_index(true_h, true_a))
    market_probs, _ = marginals(market_mats)
    always_probs = np.tile(np.array([0.0, 1.0, 0.0]), (len(matches), 1))
    always_rps = rps_rows(always_probs, outcome_index(true_h, true_a))
    rows = []
    for i, match in enumerate(matches):
        market_ok = bool(available[i])
        rows.append({
            'match_id': match.match_id, 'league': match.league, 'season_start': match.season_start,
            'date': match.date, 'ood': bool(ood), 'cold_start': bool(cold[i]),
            'ranker_applied': ranker is not None,
            'dc_hit1': bool(dc_hits[1][i]), 'dc_hit3': bool(dc_hits[3][i]), 'dc_rps': float(dc_rps[i]),
            'market_hit1': bool(mk_hits[1][i]) if market_ok else None,
            'market_hit3': bool(mk_hits[3][i]) if market_ok else None,
            'market_rps': float(rps_rows(market_probs[i:i + 1], outcome_index(true_h[i:i + 1], true_a[i:i + 1]))[0]) if market_ok else None,
            'market_from_correct_score': bool(from_cs[i]) if market_ok else None,
            'always_hit1': bool(always_hits[1][i]), 'always_hit3': bool(always_hits[3][i]),
            'always_rps': float(always_rps[i]),
            'candidate_hit1': bool(cand_hits[1][i]), 'candidate_hit3': bool(cand_hits[3][i]),
            'candidate_rps': float(cand_rps[i]),
            'weight_source': None if chosen[i][1] is None else list(chosen[i][1]),
        })
    return rows


def _train_ranker_on(oof_rows, weight_table, prior_xg, config, model_mats=None, market=None):
    odd_idx = [i for i, row in enumerate(oof_rows) if row.match.season_start % 2 == 1]
    used_odd = len(odd_idx) >= config.min_ranker_groups
    idxs = odd_idx if used_odd else list(range(len(oof_rows)))
    rows = [oof_rows[i] for i in idxs]
    info_base = {'odd_seasons_only': used_odd, 'n_rows': len(rows),
                 'training_leagues': sorted({row.match.league for row in rows})}
    if any(league in OOD_ASIA for league in info_base['training_leagues']):
        raise RuntimeError('Asian OOD rows entered LambdaMART training')
    if len(rows) < config.min_ranker_groups:
        info_base.update({'trained': False, 'reason': 'thin_oof', 'includes_asian_ood': False})
        return None, info_base
    if model_mats is None or market is None:
        model_mats = _model_matrices(oof_rows, config)
        market = _market_views([row.match for row in oof_rows], config.cap)
    chosen = np.asarray(idxs, dtype=int)
    model_mats = model_mats[chosen]
    market_mats, available, _, p_wdl, p_over, cs_grids = _slice_market(market, len(oof_rows))
    market_mats = market_mats[chosen]
    available = available[chosen]
    p_wdl = p_wdl[chosen]
    p_over = p_over[chosen]
    cs_grids = [cs_grids[i] for i in idxs]
    matches = [row.match for row in rows]
    blended = np.empty_like(model_mats)
    buckets = defaultdict(list)
    for i, row in enumerate(rows):
        record, _ = resolve_weight(weight_table, row.match.league, era_of(row.match.season_start),
                                   True, config.min_weight_matches)
        buckets[(record['w_1x2'], record['w_ou'], record['w_cs'])].append(i)
    for weights, idxs in buckets.items():
        idxs = np.asarray(idxs, dtype=int)
        blended[idxs] = blend_batch(model_mats[idxs], weights[0], weights[1], weights[2],
                                    p_wdl[idxs], p_over[idxs], [cs_grids[i] for i in idxs])
    home, away, prob = topk_scores(blended, config.top_k)
    model_cell = np.zeros_like(prob)
    market_cell = np.zeros_like(prob)
    market_missing = np.ones_like(prob)
    for i in range(len(rows)):
        for j in range(home.shape[1]):
            model_cell[i, j] = model_mats[i, home[i, j], away[i, j]]
            if available[i]:
                market_cell[i, j] = market_mats[i, home[i, j], away[i, j]]
                market_missing[i, j] = 0.0
    prior = np.array([0.0 if prior_xg.get(m.match_id) is None else prior_xg[m.match_id] for m in matches])
    missing_xg = np.array([1.0 if prior_xg.get(m.match_id) is None else 0.0 for m in matches])
    features = feature_matrix(
        home, away, prob, model_cell, market_cell, market_missing,
        np.array([row.lh for row in rows]), np.array([row.la for row in rows]),
        _flag_array(matches, lambda m: m.odds_1x2 is None),
        _flag_array(matches, lambda m: m.odds_ou is None),
        _flag_array(matches, lambda m: m.odds_cs is None),
        _flag_array(matches, lambda m: m.odds_1x2_closing or m.odds_ou_closing),
        missing_xg, prior, _flag_array(matches, lambda m: not m.lineup_known))
    labels = labels_for(home, away, [m.home_goals for m in matches], [m.away_goals for m in matches])
    booster, info = train_ranker(features, labels, config)
    info.update(info_base)
    info['includes_asian_ood'] = False
    return booster, info


def _json_table(table):
    out = {}
    for key, record in sorted(table.items(), key=lambda item: (str(item[0][0]), str(item[0][1]))):
        out['|'.join(key)] = {k: (None if isinstance(v, float) and v != v else v) for k, v in record.items()}
    return out


def walk_forward(matches, config):
    started = time.perf_counter()
    matches = [m for m in matches if m.league in EUROPE or m.league in OOD_ASIA]
    matches = sorted(matches, key=lambda m: (m.date, m.league, m.match_id))
    prior_xg = compute_prior_xg(matches)
    europe = defaultdict(list)
    asia = defaultdict(list)
    for match in matches:
        if match.league in EUROPE:
            europe[match.league].append(match)
        else:
            asia[match.league].append(match)
    print('Fitting Europe out-of-fold Dixon-Coles blocks', flush=True)
    oof = []
    for league in EUROPE:
        oof.extend(build_oof(europe[league], config))
    oof.sort(key=lambda row: (row.match.date, row.match.match_id))
    if any(row.match.league in OOD_ASIA for row in oof):
        raise RuntimeError('OOF builder leaked an Asian league')
    print(f'Out-of-fold rows {len(oof)}; building score matrices once', flush=True)
    oof_mats = _model_matrices(oof, config) if oof else None
    oof_market = _market_views([row.match for row in oof], config.cap) if oof else None

    rows = []
    folds = []
    weight_cache = {}
    ranker_cache = {}
    warm = {}

    def tools_before(cutoff):
        if cutoff in weight_cache:
            return weight_cache[cutoff], ranker_cache[cutoff]
        count = 0
        while count < len(oof) and oof[count].match.date < cutoff:
            count += 1
        subset = oof[:count]
        if subset and subset[-1].match.date >= cutoff:
            raise RuntimeError('weight sample is not before the cutoff')
        if count == 0:
            table, meta = select_weights([], config)
            booster, info = _train_ranker_on([], None, prior_xg, config)
        else:
            market_prefix = _slice_market(oof_market, count)
            table, meta = select_weights(subset, config, oof_mats[:count], market_prefix)
            booster, info = _train_ranker_on(subset, table, prior_xg, config, oof_mats[:count], market_prefix)
        weight_cache[cutoff] = (table, meta)
        ranker_cache[cutoff] = (booster, info)
        return weight_cache[cutoff], ranker_cache[cutoff]

    europe_seasons = sorted({m.season_start for rows_ in europe.values() for m in rows_})
    for year in europe_seasons:
        qualified = []
        ready = {}
        for league, league_rows in europe.items():
            prior_seasons = {m.season_start for m in league_rows if m.season_start < year}
            season_rows = [m for m in league_rows if m.season_start == year]
            if len(prior_seasons) >= config.min_prior_seasons and season_rows:
                ready[league] = season_rows
                qualified.extend(season_rows)
        if not qualified:
            continue
        cutoff = min(m.date for m in qualified)
        origin_started = time.perf_counter()
        print(f'Europe origin {year} cutoff {cutoff} test_candidates {len(qualified)}', flush=True)
        (table, weight_meta), (booster, ranker_info) = tools_before(cutoff)
        fold_rows = []
        train_ends = {}
        leakage_ok = True
        for league, season_rows in ready.items():
            train = [m for m in europe[league] if m.date < cutoff]
            if len(train) < config.min_train_matches:
                continue
            if max(m.date for m in train) >= cutoff:
                leakage_ok = False
                raise RuntimeError('Europe training date is not before the test cutoff')
            model = fit_score_model(train, config, warm.get(league))
            if model is None:
                continue
            warm[league] = model
            part = evaluate_matches(season_rows, model, table, booster, prior_xg, config,
                                    allow_league=True, ood=False)
            fold_rows.extend(part)
            train_ends[league] = {'train_end': model.train_end, 'train_rows': model.train_rows,
                                  'rho': model.rho, 'share': model.share, 'pi': model.pi,
                                  'converged': model.converged}
        if not fold_rows:
            continue
        rows.extend(fold_rows)
        folds.append({
            'scope': 'europe', 'season_start': year, 'cutoff': cutoff, 'n_test': len(fold_rows),
            'train_ends': train_ends, 'leakage_guard_passed': leakage_ok,
            'weight_fit': weight_meta, 'weights': _json_table(table),
            'ranker': {k: v for k, v in ranker_info.items() if k != 'features'} | {'features': ranker_info.get('features')},
            'metrics': summarize(fold_rows),
        })
        print(f'Europe origin {year} scored {len(fold_rows)} in {time.perf_counter() - origin_started:.1f}s', flush=True)

    for league in OOD_ASIA:
        league_rows = asia.get(league, [])
        seasons = sorted({m.season_start for m in league_rows})
        for year in seasons:
            prior_seasons = [s for s in seasons if s < year]
            season_rows = [m for m in league_rows if m.season_start == year]
            if len(prior_seasons) < config.min_prior_seasons or not season_rows:
                continue
            cutoff = min(m.date for m in season_rows)
            train = [m for m in league_rows if m.date < cutoff]
            if len(train) < config.min_train_matches:
                continue
            if max(m.date for m in train) >= cutoff:
                raise RuntimeError('Asian training date is not before the test cutoff')
            print(f'OOD {league} origin {year} cutoff {cutoff} n_test {len(season_rows)}', flush=True)
            (table, weight_meta), (booster, ranker_info) = tools_before(cutoff)
            model = fit_score_model(train, config, warm.get(league))
            if model is None:
                continue
            warm[league] = model
            part = evaluate_matches(season_rows, model, table, booster, prior_xg, config,
                                    allow_league=False, ood=True)
            rows.extend(part)
            folds.append({
                'scope': 'ood_asia', 'league': league, 'season_start': year, 'cutoff': cutoff,
                'n_test': len(part), 'train_end': model.train_end, 'train_rows': model.train_rows,
                'rho': model.rho, 'share': model.share, 'pi': model.pi,
                'leakage_guard_passed': True,
                'weight_fit_excludes_this_league': league not in weight_meta.get('leagues', []),
                'ranker_excludes_this_league': league not in ranker_info.get('training_leagues', []),
                'ranker_trained': bool(ranker_info.get('trained')),
                'metrics': summarize(part),
            })

    europe_rows = [r for r in rows if not r['ood']]
    asia_rows = [r for r in rows if r['ood']]
    by_league = {}
    for league in list(EUROPE) + list(OOD_ASIA):
        subset = [r for r in rows if r['league'] == league]
        if subset:
            by_league[league] = summarize(subset)
    by_season = {}
    for year in sorted({r['season_start'] for r in europe_rows}):
        by_season[str(year)] = summarize([r for r in europe_rows if r['season_start'] == year])
    report = {
        'version': VERSION,
        'role': 'RESEARCH_CANDIDATE',
        'champion_unchanged': True,
        'wired_into_predict_active': False,
        'formal_promotion': False,
        'source_sha256': source_hash(),
        'method': {
            'score_model': 'diagonal-inflated bivariate Poisson with Dixon-Coles low-score tau',
            'market': 'Shin de-vig; correct-score grid when at least 8 prices exist, else Poisson matched to Shin 1X2 and OU 2.5',
            'blend': 'per league and era on even Europe OOF seasons; Asian matches use Europe pooled weights only',
            'ranker': 'LightGBM lambdarank on blended top-10, trained on odd Europe OOF seasons',
            'walk_forward': 'expanding origin over every season after min_prior_seasons; cutoff is the first test-match date',
            'baselines': ['dixon_coles', 'market_shin', 'always_1_1'],
            'metrics': ['top1', 'top3', 'rps_1x2', 'exact_mcnemar'],
            'always_1_1_top3': 'the issued list is exactly [1-1], so top3 equals top1',
            'half_life_days': config.half_life_days,
            'min_prior_seasons': config.min_prior_seasons,
            'cap': config.cap,
            'asian_ood': list(OOD_ASIA),
            'europe_train': list(EUROPE),
        },
        'folds': folds,
        'metrics': {
            'europe_pooled': summarize(europe_rows),
            'ood_asia_pooled': summarize(asia_rows),
            'by_league': by_league,
            'europe_by_season': by_season,
        },
        'elapsed_seconds': round(time.perf_counter() - started, 3),
    }
    return report, rows
