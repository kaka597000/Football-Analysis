"""Shared Poisson residual math; league-specific feature policies stay in adapters."""
import numpy as np
import lightgbm as lgb

def fit_booster(items, config, feature_names):
    if len(items) < 30: raise ValueError('insufficient real feature targets')
    features = np.concatenate([it['features'] for it in items])
    goals = np.array([it['row'][k] for it in items for k in ('home_goals','away_goals')])
    offsets = np.log(np.array([v for it in items for v in it['rates']]))
    params = {k: config[k] for k in ('objective', 'seed', 'num_leaves', 'min_data_in_leaf', 'learning_rate', 'lambda_l2')}
    params.update(verbosity=-1, num_threads=1, deterministic=True, force_col_wise=True)
    ds = lgb.Dataset(features, label=goals, init_score=offsets, feature_name=feature_names)
    booster = lgb.train(params, ds, num_boost_round=config['num_boost_round'])
    return booster

def ml_matrix(booster, features, rates, rho, config, legacy):
    delta = np.clip(booster.predict(features, raw_score=True, num_threads=1),
                    -config['residual_cap'], config['residual_cap'])
    adjusted = np.clip(np.array(rates)*np.exp(delta), .05, 12.)
    return legacy.matrix(float(adjusted[0]), float(adjusted[1]), 'dixon_coles', rho), delta
