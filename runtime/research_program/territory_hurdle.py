"""R14 pooled home/away score hurdles on a chronological R7 joint distribution."""

import numpy as np
from scipy.optimize import brentq, minimize
from scipy.special import logsumexp


VERSION = 'R14-territory-press-hurdle-1'
FEATURES = ('own_xg_attack', 'opponent_xg_concession', 'own_deep_attack',
            'opponent_deep_concession', 'own_pressing', 'opponent_pressing')
RIDGE = 24.
COEFFICIENT_BOUND = 1.5
FEATURE_LIMIT = 1.
SHIFT_LIMIT = .65
IPF_RTOL = 1e-12
IPF_MAX_ITER = 1024


def _design(values, side_shape, width):
    x = np.asarray(values)
    if x.shape[:-1] != side_shape or x.shape[-1] not in (width, len(FEATURES)):
        raise ValueError('expected a home and away feature vector in registered column order')
    x = np.asarray(x[..., :width], dtype=float)
    return np.clip(np.where(np.isfinite(x), x, 0.), -FEATURE_LIMIT, FEATURE_LIMIT)


def _probabilities(values, ndim):
    p = np.asarray(values, dtype=float)
    if p.ndim != ndim or min(p.shape[-2:]) == 0:
        raise ValueError('expected a nonempty score grid for every anchor')
    if (not np.isfinite(p).all() or np.any(p < 0.)
            or not np.allclose(p.sum(axis=(-2, -1)), 1., rtol=1e-7, atol=0.)):
        raise ValueError('each anchor must be a probability matrix with unit mass')
    return p


def _log_mass(mass):
    with np.errstate(divide='ignore'):
        return np.log(mass)


def _fit_tilt(x, mass, observed):
    """A two-bin tilt is logistic regression with the anchor log odds as offset."""
    width = x.shape[1]
    if not len(observed):
        return [0.] * width
    # Center each row on its observed bin. Its logit is then always zero,
    # and both the NLL and its gradient remain defined for structural zeros.
    steps = np.arange(mass.shape[1]) - observed[:, None]
    log_odds = _log_mass(mass) - np.log(mass[np.arange(len(x)), observed])[:, None]

    def objective(beta):
        logits = log_odds + (x @ beta)[:, None] * steps
        norm = logsumexp(logits, axis=1)
        p = np.exp(logits - norm[:, None])
        loss = norm.sum() + RIDGE / 2 * (beta @ beta)
        gradient = x.T @ (p * steps).sum(axis=1) + RIDGE * beta
        return loss, gradient

    result = minimize(objective, np.zeros(width), jac=True, method='L-BFGS-B',
                      bounds=[(-COEFFICIENT_BOUND, COEFFICIENT_BOUND)] * width,
                      options={'ftol': 1e-13, 'gtol': 1e-8})
    if not result.success:
        raise ValueError('territory hurdle fit failed: ' + str(result.message))
    return result.x.tolist()


@np.errstate(under='ignore')
def fit(anchors, design, scores, *, xg_only=False):
    """Fit one league's supplied training slice, with design shaped (games, 2, 6).

    The xG arm accepts either the six columns or their first two. Nonfinite
    features are neutral per column. Each stage counts informative sides with
    observed goals. Fixed scoring rates and single-bin positive conditionals
    have zero slope information. A positive score outside the anchor support
    contributes only its scoring indicator when that rate can move.
    """
    n = len(anchors)
    if len(design) != n or len(scores) != n:
        raise ValueError('anchor, design and score populations must agree')
    width = 2 if xg_only else len(FEATURES)
    fitted = dict(version=VERSION, xg_only=bool(xg_only), features=list(FEATURES[:width]),
                  ridge=RIDGE, coefficient_bound=COEFFICIENT_BOUND, feature_limit=FEATURE_LIMIT,
                  shift_limit=SHIFT_LIMIT, ipf_rtol=IPF_RTOL, ipf_max_iter=IPF_MAX_ITER,
                  training_games=n, games_with_signal=0, fit_games=0, scoring_fit_sides=0,
                  positive_fit_sides=0, scoring_beta=[0.] * width, positive_beta=[0.] * width)
    if n == 0:
        return fitted

    p = _probabilities(anchors, 3)
    x = _design(design, (n, 2), width).reshape(-1, width)
    goals = np.asarray(scores, dtype=float)
    if goals.shape != (n, 2):
        raise ValueError('expected one home and one away score per game')
    goals = goals.ravel()
    known = np.isfinite(goals)
    if np.any(goals[known] < 0) or np.any(goals[known] != np.floor(goals[known])):
        raise ValueError('observed goals must be nonnegative integers')

    # Pad only the shorter marginal of a rectangular grid, retaining every goal.
    height, length = p.shape[1:]
    margins = np.zeros((n, 2, max(height, length)))
    margins[:, 0, :height] = p.sum(axis=2)
    margins[:, 1, :length] = p.sum(axis=1)
    margins = margins.reshape(2 * n, -1)
    positive = margins[:, 1:]
    binary = np.column_stack((margins[:, 0], positive.sum(axis=1)))
    signal = np.any(x != 0., axis=1)
    scoring_fit = signal & known & (binary > 0.).all(axis=1)
    in_grid = known & (goals > 0) & (goals < margins.shape[1])
    index = np.where(in_grid, goals, 0).astype(np.intp)
    positive_fit = (signal & in_grid & (margins[np.arange(2 * n), index] > 0.)
                    & (np.count_nonzero(positive, axis=1) > 1))

    fitted.update(
        games_with_signal=int(signal.reshape(n, 2).any(axis=1).sum()),
        fit_games=int((scoring_fit | positive_fit).reshape(n, 2).any(axis=1).sum()),
        scoring_fit_sides=int(scoring_fit.sum()), positive_fit_sides=int(positive_fit.sum()),
        scoring_beta=_fit_tilt(x[scoring_fit], binary[scoring_fit], (goals[scoring_fit] > 0).astype(int)),
        positive_beta=_fit_tilt(x[positive_fit], positive[positive_fit], index[positive_fit] - 1))
    return fitted


def _log_target(log_margin, scoring_shift, positive_shift):
    positive = log_margin[1:]
    support = np.count_nonzero(np.isfinite(positive))
    change_rate = scoring_shift != 0. and np.isfinite(log_margin[0]) and support > 0
    change_shape = positive_shift != 0. and support > 1
    if not (change_rate or change_shape):
        return log_margin

    target = log_margin.copy()
    log_mass = np.logaddexp.reduce(positive)
    if change_shape:
        tilted = positive + positive_shift * np.arange(len(positive))
        target[1:] = (tilted - np.logaddexp.reduce(tilted)) + log_mass
    if change_rate:
        normalizer = np.logaddexp(log_margin[0], log_mass + scoring_shift)
        target[0] -= normalizer
        target[1:] += scoring_shift - normalizer
    return target


def _ipf(matrix, log_home, log_away):
    """Return a row/column scaling, or None if the anchor cannot realize it."""
    # Finite tilts preserve the marginal support, including subnormal masses.
    block = np.ix_(np.isfinite(log_home), np.isfinite(log_away))
    log_anchor = _log_mass(matrix[block])
    log_rows = log_home[np.isfinite(log_home)][:, None]
    log_cols = log_away[np.isfinite(log_away)]
    cols = np.exp(log_cols)

    def scale_rows(log_v):
        weights = log_anchor + log_v
        log_joint = (weights - np.logaddexp.reduce(weights, axis=1, keepdims=True)) + log_rows
        return log_joint, np.logaddexp.reduce(log_joint, axis=0)

    log_v = np.zeros_like(cols)
    for iteration in range(IPF_MAX_ITER):
        log_joint, current_cols = scale_rows(log_v)
        correction = log_cols - current_cols
        error = np.max(np.abs(correction))
        if error <= IPF_RTOL / 2:
            joint = np.exp(log_joint)
            # Rounding a positive subnormal cell can also corrupt an odds ratio
            # without noticeably changing either marginal.
            tiny = (joint < np.finfo(float).tiny) & np.isfinite(log_anchor)
            if (np.any(np.abs(_log_mass(joint[tiny]) - log_joint[tiny]) > IPF_RTOL / 4)
                    or np.any(np.abs(_log_mass(joint.sum(axis=1)) - log_rows[:, 0]) > IPF_RTOL)
                    or np.any(np.abs(_log_mass(joint.sum(axis=0)) - log_cols) > IPF_RTOL)):
                return None
            result = np.zeros_like(matrix)
            result[block] = joint
            return result

        # Accelerate slow IPF on nearly disconnected score blocks. Holding
        # rows exact, the log-column Jacobian uses the two conditional tables.
        if (iteration + 1) % 16 == 0:
            limit = -2. * np.log(np.finfo(float).smallest_subnormal)
            by_row = np.exp(log_joint - log_rows)
            by_col = np.exp(log_joint - current_cols)
            jacobian = np.eye(len(cols)) - by_col.T @ by_row
            if np.max(np.abs(jacobian)) > np.finfo(float).eps:
                step = np.linalg.lstsq(jacobian, correction, rcond=None)[0]
                if np.max(np.abs(step)) < limit:
                    _, trial = scale_rows(log_v + step)
                    if np.max(np.abs(log_cols - trial)) < error / 2:
                        log_v += step
                        continue

            # Line-search the IPF direction, alternating with the worst column
            # so a single nearly isolated column can also move independently.
            direction = correction / error
            if (iteration + 1) % 32 == 0:
                column = np.argmax(np.abs(correction))
                direction = np.zeros_like(correction)
                direction[column] = np.sign(correction[column])

            def slope(step):
                _, trial_cols = scale_rows(log_v + step * direction)
                return (np.exp(trial_cols) - cols) @ direction

            if slope(0.) < 0.:
                if slope(limit) >= 0.:
                    log_v += brentq(slope, 0., limit) * direction
                    continue
        log_v += correction
    return None


@np.errstate(under='ignore')
def apply(prior, x, fitted):
    """Return the full score matrix. Neutral or infeasible adjustments inherit prior.

    All positive cells must survive rescaling and both marginal targets must be
    met to IPF_RTOL, including rare tails. Otherwise return the original object.
    """
    width = 2 if fitted['xg_only'] else len(FEATURES)
    beta = np.column_stack((fitted['scoring_beta'], fitted['positive_beta']))
    if beta.shape != (width, 2) or not np.isfinite(beta).all():
        raise ValueError('invalid fitted hurdle slopes')
    matrix = _probabilities(prior, 2)
    shift = np.clip(_design(x, (2,), width) @ beta, -fitted['shift_limit'], fitted['shift_limit'])
    if not np.any(shift):
        return prior
    home, away = _log_mass(matrix.sum(axis=1)), _log_mass(matrix.sum(axis=0))
    home -= np.logaddexp.reduce(home)
    away -= np.logaddexp.reduce(away)
    rows, cols = _log_target(home, *shift[0]), _log_target(away, *shift[1])
    if np.array_equal(rows, home) and np.array_equal(cols, away):
        return prior
    result = _ipf(matrix, rows, cols)
    return prior if result is None or np.array_equal(result, matrix) else result
