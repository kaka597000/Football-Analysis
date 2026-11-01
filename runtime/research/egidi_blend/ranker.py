"""LambdaMART reranker over the blended top-10 scorelines.

LightGBM's native lambdarank objective (not the sklearn wrapper) so the
dependency matches the rest of this repo's boosters: numpy groups, one
query per match. The ranker is trained only on Europe OOF rows. Asian
leagues are scored with the transferred booster and never enter `group`.
"""
import numpy as np
import lightgbm as lgb


FEATURE_NAMES = (
    'log_blend_prob', 'blend_prob', 'model_prob', 'market_cell_prob', 'market_cell_missing',
    'cand_home', 'cand_away', 'cand_total', 'cand_diff', 'is_draw', 'is_1_1', 'is_0_0',
    'abs_home_lambda', 'abs_away_lambda', 'rank_in_blend', 'lambda_home', 'lambda_away',
    'missing_1x2', 'missing_ou', 'missing_cs', 'odds_are_closing',
    'missing_xg', 'prior_xg_diff', 'missing_lineup',
)


def feature_matrix(home, away, blend_prob, model_prob, market_prob, market_missing,
                   lh, la, missing_1x2, missing_ou, missing_cs, odds_closing,
                   missing_xg, prior_xg_diff, missing_lineup):
    """home/away/probs are (n, k). Match-level flags are (n,) and broadcast."""
    n, k = home.shape
    rank = np.arange(k, dtype=float)[None, :] / float(k)
    log_p = np.log(np.clip(blend_prob, 1e-12, 1.0))
    cols = [
        log_p, blend_prob, model_prob, market_prob, market_missing,
        home.astype(float), away.astype(float),
        (home + away).astype(float), (home - away).astype(float),
        (home == away).astype(float), ((home == 1) & (away == 1)).astype(float),
        ((home == 0) & (away == 0)).astype(float),
        np.abs(home - lh[:, None]), np.abs(away - la[:, None]),
        np.broadcast_to(rank, (n, k)),
        np.broadcast_to(lh[:, None], (n, k)), np.broadcast_to(la[:, None], (n, k)),
        np.broadcast_to(missing_1x2[:, None], (n, k)),
        np.broadcast_to(missing_ou[:, None], (n, k)),
        np.broadcast_to(missing_cs[:, None], (n, k)),
        np.broadcast_to(odds_closing[:, None], (n, k)),
        np.broadcast_to(missing_xg[:, None], (n, k)),
        np.broadcast_to(prior_xg_diff[:, None], (n, k)),
        np.broadcast_to(missing_lineup[:, None], (n, k)),
    ]
    stacked = np.stack([np.asarray(c, dtype=float) for c in cols], axis=-1)
    if stacked.shape[-1] != len(FEATURE_NAMES):
        raise RuntimeError('feature width drifted from FEATURE_NAMES')
    return stacked


def labels_for(home, away, true_home, true_away):
    true_home = np.asarray(true_home)[:, None]
    true_away = np.asarray(true_away)[:, None]
    return ((home == true_home) & (away == true_away)).astype(np.int32)


def train_ranker(features, labels, config):
    """features (n, k, f), labels (n, k). Groups with no relevant scoreline are dropped.

    Returns (booster or None, info dict).
    """
    n, k, width = features.shape
    keep = labels.sum(axis=1) > 0
    n_keep = int(keep.sum())
    info = {'trained': False, 'groups': n_keep, 'dropped_unlisted_truth': int((~keep).sum()),
            'features': list(FEATURE_NAMES), 'leagues_are_not_a_feature': True}
    if n_keep < config.min_ranker_groups:
        info['reason'] = 'thin_positive_groups'
        return None, info
    x = features[keep].reshape(n_keep * k, width)
    y = labels[keep].reshape(n_keep * k)
    group = np.full(n_keep, k, dtype=int)
    dataset = lgb.Dataset(x, label=y, group=group, feature_name=list(FEATURE_NAMES), free_raw_data=False)
    params = {
        'objective': 'lambdarank',
        'metric': 'ndcg',
        'ndcg_eval_at': [1, 3],
        'learning_rate': config.ranker_learning_rate,
        'num_leaves': config.ranker_leaves,
        'min_data_in_leaf': config.ranker_min_data,
        'lambda_l2': 1.0,
        'verbosity': -1,
        'num_threads': 1,
        'seed': config.seed,
        'deterministic': True,
        'force_col_wise': True,
        'label_gain': [0, 1],
    }
    booster = lgb.train(params, dataset, num_boost_round=config.ranker_rounds)
    info['trained'] = True
    info['rounds'] = config.ranker_rounds
    return booster, info


def rank_scores(booster, features):
    """Higher is better. Shape (n, k). None booster returns the blend probability column."""
    n, k, width = features.shape
    if booster is None:
        return features[:, :, 1].copy()
    raw = booster.predict(features.reshape(n * k, width))
    return np.asarray(raw, dtype=float).reshape(n, k)


def order_by_scores(home, away, prob, scores):
    """Reorder each row's k candidates by descending ranker score, stable on ties."""
    n, k = home.shape
    # Tiny index penalty keeps ties deterministic (original blend order wins a tie).
    adjusted = scores - np.arange(k, dtype=float)[None, :] * 1e-9
    order = np.argsort(-adjusted, axis=1)
    rows = np.arange(n)[:, None]
    return home[rows, order], away[rows, order], prob[rows, order], scores[rows, order]
