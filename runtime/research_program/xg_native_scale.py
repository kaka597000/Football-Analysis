"""Fixed native scales for bounded xG availability features; inactive research.

No post-prediction clipping. Existing goal/xG-only arms stay unchanged. This
module does not collect sources, replace active models or certify timestamps.
"""
import warnings
import numpy as np
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import PoissonRegressor


def fit(rows, variant, protocol, feature_names):
    x = np.asarray([v for r in rows for v in r['features'][variant]], dtype=float)
    y = np.asarray([v for r in rows for v in r['actual_score']], dtype=float)
    names = feature_names[variant]
    if x.ndim != 2 or x.shape[1] != len(names) or not len(y):
        raise ValueError('explicit feature schema and training rows required')
    n = np.isfinite(x).sum(axis=0)
    means = np.divide(np.nansum(x, axis=0), n, out=np.zeros(x.shape[1]), where=n > 0)
    filled = np.where(np.isfinite(x), x, means)
    scale = filled.std(axis=0)
    scale[scale == 0] = 1.
    fixed = {}
    for i, name in enumerate(names):
        if name.endswith(('_missing', '_missing_fraction_prior_goal_window')):
            scale[i] = 1.
        elif name.endswith(('_observed_count', '_effective_n')):
            scale[i] = float(protocol['window_matches'])
        elif name.endswith('_latest_age_days'):
            scale[i] = float(protocol['max_xg_age_days'])
        else:
            continue
        fixed[name] = float(scale[i])
    model = PoissonRegressor(alpha=protocol['ridge_alpha'], max_iter=protocol['max_iter'], tol=protocol['tol'])
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always', ConvergenceWarning)
        model.fit((filled - means) / scale, y)
    if any(issubclass(w.category, ConvergenceWarning) for w in caught):
        raise RuntimeError('native-scale Poisson GLM did not converge')
    return {'feature_names': names, 'mean': means.tolist(), 'scale': scale.tolist(),
            'coef': model.coef_.tolist(), 'intercept': float(model.intercept_), 'n_iter': int(model.n_iter_),
            'training_observed_counts': n.tolist(), 'unlearned_columns': np.flatnonzero(n == 0).tolist(),
            'fixed_native_scales': fixed,
            'imputation': 'training means only; unlearned columns remain neutral',
            'post_prediction_rate_clipping': False}
