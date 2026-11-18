"""Three prespecified score-distribution arms for a prospective market calibration trial."""
from collections import Counter
from datetime import date, timedelta
from functools import lru_cache
import hashlib

import numpy as np
from scipy.optimize import brentq, minimize
from scipy.special import logsumexp

from .elo_kelly_shadow import aware, devig, digest
try:
    from runtime.v2.model import probabilities, total_settlement
except ModuleNotFoundError:
    from v2.model import probabilities, total_settlement


VERSION = 'WCPP-THREE-ARM-MARKET-COHORT-1'
SIDES = ('home', 'draw', 'away')
FEATURES = ('home_zero', 'away_zero', 'total_at_least_four')
MARKET_RIDGE, GLOBAL_RIDGE, COHORT_RIDGE = 2.0, 12.0, 30.0
PATTERNS = np.array([[(k >> i) & 1 for i in range(3)] for k in range(8)])


def _matrix(value):
    m = np.asarray(value, dtype=float)
    if (m.ndim != 2 or not m.size or not np.isfinite(m).all() or (m < 0).any()
            or not np.isclose(m.sum(), 1., rtol=0, atol=1e-8)):
        raise ValueError('normalized nonnegative score matrix required')
    return m


@lru_cache(maxsize=32)
def _coordinates(shape):
    home, away = np.indices(shape)
    total = home + away
    sides = np.where(home > away, 0, np.where(home == away, 1, 2))
    patterns = (home == 0).astype(int) + 2 * (away == 0) + 4 * (total >= 4)
    return total, sides, patterns


def _quote_values(market, cutoff, proxy=False):
    if not proxy:
        for field in ('h2h_quoted_at', 'total_quoted_at'):
            stamp = market.get(field, market.get('quoted_at'))
            if not stamp or not timedelta(0) <= cutoff - aware(stamp) <= timedelta(hours=1):
                raise ValueError('both market quotes must be observed within one hour of prediction cutoff')
    wdl, _ = devig(market['h2h'])
    total = market['total']
    line = float(total['line'])
    if not np.isfinite(line) or line < 0 or not (4 * line).is_integer():
        raise ValueError('valid Asian total quarter line required')
    odds = np.array([total['over'], total['under']], dtype=float)
    if not np.isfinite(odds).all() or (odds <= 1).any():
        raise ValueError('two decimal total prices above one required')
    inverse = 1 / odds
    return np.array([wdl[k] for k in SIDES]), line, inverse[0] / inverse.sum()


def _market_matrix(m, quote):
    """I-project Current to W/D/L and fair Asian win-stake share simultaneously."""
    target_wdl, line, over_share = quote
    goals, sides, _ = _coordinates(m.shape)
    legs = (line,) if (line * 2).is_integer() else (line - .25, line + .25)
    net = sum(np.sign(goals - leg) for leg in legs) / len(legs)
    # The five settlement states have net units -1, -.5, 0, .5, 1.
    # Fair over probability is E[win units] / E[win units + loss units].
    states = np.arange(-1., 1.01, .5)
    constraint = np.maximum(states, 0.) - over_share * np.abs(states)
    category = (net * 2 + 2).astype(int)
    groups = sides * 5 + category
    masses = np.bincount(groups.ravel(), weights=m.ravel(), minlength=15).reshape(3, 5)
    if np.any(masses.sum(axis=1) == 0):
        raise ValueError('score support cannot represent the market W/D/L')
    logs = np.full_like(masses, -np.inf)
    np.log(masses, out=logs, where=masses > 0)

    def tilted(coefficient):
        logits = logs + coefficient * constraint
        return target_wdl[:, None] * np.exp(logits - logsumexp(logits, axis=1, keepdims=True))

    def residual(coefficient):
        return np.sum(tilted(coefficient) * constraint)

    low, high = -512., 512.
    if residual(low) >= 0 or residual(high) <= 0:
        raise ValueError('score support cannot represent the Asian market')
    adjusted = tilted(brentq(residual, low, high))
    scale = np.divide(adjusted, masses, out=np.zeros_like(masses), where=masses > 0)
    result = m * scale[sides, category]
    return result / result.sum()


def _fit_weight(pairs):
    if not pairs:
        return 0.
    current, projected = np.array(pairs, dtype=float).T
    diff = projected - current

    def derivative(weight):
        return -np.sum(diff / np.maximum(current + weight * diff, 1e-300)) + MARKET_RIDGE * weight

    if derivative(0.) >= 0: return 0.
    if derivative(1.) <= 0: return 1.
    return float(brentq(derivative, 0., 1.))


def _corrections(rows, ridge, center):
    """Penalized exact-score likelihood; each cell in one of eight feature patterns."""
    if not rows: return np.array(center)
    masses = np.stack([mass for mass, _ in rows])
    actual = PATTERNS[[label for _, label in rows]].sum(axis=0)
    def objective(beta):
        tilt = np.exp(PATTERNS @ beta)
        normalizer = masses @ tilt
        expected = (masses * tilt / normalizer[:, None]).sum(axis=0) @ PATTERNS
        delta = beta - center
        return (np.log(normalizer).sum() - actual @ beta + ridge * (delta @ delta) / 2,
                expected - actual + ridge * delta)
    result = minimize(objective, center, jac=True, method='L-BFGS-B', bounds=[(-4,4)] * 3)
    if not result.success:
        raise ValueError('score calibration optimization failed: ' + result.message)
    return result.x


def fit(rows, *, cutoff_at, allow_historical_proxy=False):
    """Fit only already completed information. Date-only closing data require explicit study mode."""
    cutoff = aware(cutoff_at)
    market_pairs, calibration, identities = [], [], []
    seen = set()
    market_sources = Counter()
    source_counts = Counter()
    for row in sorted(rows, key=lambda r: r['match_id']):
        proxy = row.get('historical_proxy') is True
        if proxy:
            if not allow_historical_proxy:
                raise ValueError('historical closing proxies require explicit opt-in')
            if date.fromisoformat(row['game_date']) >= cutoff.date():
                continue
            original_cutoff = None
        else:
            if aware(row['kickoff_at']) >= cutoff or aware(row['observed_at']) > cutoff:
                continue
            original_cutoff = aware(row['cutoff_at'])
            if not original_cutoff < aware(row['kickoff_at']) < aware(row['observed_at']):
                raise ValueError('nonchronological frozen input or result')
        mid, cohort = row['match_id'], row['cohort']
        if mid in seen:
            raise ValueError('duplicate training match: ' + mid)
        seen.add(mid)
        source_kind = 'date_only_proxy' if proxy else 'frozen_current'
        source_counts[(source_kind, cohort)] += 1
        m = _matrix(row['current_matrix'])
        h, a = row['home_goals'], row['away_goals']
        if type(h) is not int or type(a) is not int or not (0 <= h < m.shape[0] and 0 <= a < m.shape[1]):
            raise ValueError('regulation score outside matrix support')
        projected = m
        if row.get('market') is not None:
            projected = _market_matrix(m, _quote_values(row['market'], original_cutoff, proxy))
            market_pairs.append((m[h, a], projected[h, a]))
            market_sources[(source_kind, cohort)] += 1
        groups = _coordinates(m.shape)[2]
        if not proxy:
            orig = np.bincount(groups.ravel(), weights=m.ravel(), minlength=8)
            alt = np.bincount(groups.ravel(), weights=projected.ravel(), minlength=8)
            calibration.append((cohort, orig, alt, groups[h, a]))
        stamp_keys = ('game_date',) if proxy else ('kickoff_at', 'cutoff_at', 'observed_at')
        identities.append([mid, cohort, source_kind, [row[k] for k in stamp_keys], h, a, row.get('market'),
                           m.shape, hashlib.sha256(np.ascontiguousarray(m, dtype='<f8').tobytes()).hexdigest()])
    weight = _fit_weight(market_pairs)
    pooled = [(cohort, ((1 - weight) * orig + weight * alt, label)) for cohort, orig, alt, label in calibration]
    global_beta = _corrections([item for _, item in pooled], GLOBAL_RIDGE, np.zeros(3))
    cohorts = {}
    for cohort in sorted({c for c, _ in pooled}):
        sample = [item for c, item in pooled if c == cohort]
        cohorts[cohort] = {'n': len(sample), 'coefficients': _corrections(sample, COHORT_RIDGE, global_beta).tolist()}
    def inventory(counts):
        return {kind: {c: n for (k, c), n in sorted(counts.items()) if k == kind}
                for kind in ('frozen_current', 'date_only_proxy')}
    return {'version': VERSION, 'cutoff_at': cutoff_at, 'sample_sha256': digest(identities),
            'n': len(seen), 'training_by_source': inventory(source_counts),
            'market_n': len(market_pairs), 'market_training_by_source': inventory(market_sources),
            'market_weight': weight, 'market_ridge': MARKET_RIDGE, 'features': list(FEATURES),
            'cohort_calibration_n': len(pooled), 'global_ridge': GLOBAL_RIDGE, 'cohort_ridge': COHORT_RIDGE,
            'global_coefficients': global_beta.tolist(), 'cohorts': cohorts,
            'unseen_cohort_fallback': 'pooled_frozen_current_coefficients',
            'historical_close_times_certified': False, 'active_model_changed': False}


def apply(artifact, current_matrix, market, *, cohort, cutoff_at):
    """Use the frozen blend weight and the target's own as-of quotes at one cutoff."""
    cutoff = aware(cutoff_at)
    if artifact['version'] != VERSION or aware(artifact['cutoff_at']) > cutoff:
        raise ValueError('candidate identity or training cutoff conflicts with forecast')
    quote = _quote_values(market, cutoff) if market is not None else None
    return _apply(artifact, current_matrix, quote, cohort)


def apply_historical_proxy(artifact, current_matrix, market, *, cohort, game_date):
    """Development diagnostics on later calendar dates; closing prices have no certified as-of time."""
    if artifact['version'] != VERSION or aware(artifact['cutoff_at']).date() >= date.fromisoformat(game_date):
        raise ValueError('later match day required for proxy evaluation')
    quote = _quote_values(market, None, proxy=True) if market is not None else None
    return _apply(artifact, current_matrix, quote, cohort)


def _apply(artifact, current_matrix, quote, cohort):
    current = _matrix(current_matrix)
    blended = current.copy()
    if quote is not None:
        projection = _market_matrix(current, quote)
        w = artifact['market_weight']
        blended = (1 - w) * current + w * projection
    local = artifact['cohorts'].get(cohort)
    beta = local['coefficients'] if local else artifact['global_coefficients']
    corrected = blended.copy()
    if np.any(beta):
        corrected *= np.exp((PATTERNS @ beta)[_coordinates(current.shape)[2]])
        corrected /= corrected.sum()
    return {'current': current.copy(), 'market': blended, 'market_cohort': corrected}


def score_picks(matrix, *, line=None):
    m = _matrix(matrix)
    h, a = np.unravel_index(np.argmax(m), m.shape)
    wdl = probabilities(m)
    value = {'primary': f'{h}-{a}', 'primary_probability': float(m[h, a]),
             'wdl': SIDES[np.argmax(wdl)], 'wdl_probabilities': dict(zip(SIDES, map(float, wdl))), 'total': None}
    if line is not None:
        size = max(m.shape)
        square = np.pad(m, ((0, size - m.shape[0]), (0, size - m.shape[1])))
        settlements = {side: total_settlement(square, line, side) for side in ('over', 'under')}
        def objective(side):
            p = settlements[side]
            nonpush = max(0., 1. - p['push'])
            return ((p['win'] / nonpush if nonpush > 1e-12 else 0.),
                    p['win'] + .5 * p['half_win'] - p['loss'] - .5 * p['half_loss'])
        # Both comparisons are bounded probability-scale quantities. Treat residuals
        # from market projection and PMF summation below 1e-12 as a numerical tie.
        direction = 'over'
        for over, under in zip(objective('over'), objective('under')):
            if abs(over - under) > 1e-12:
                direction = 'over' if over > under else 'under'
                break
        value['total'] = {'line': line, 'direction': direction, 'settlements': settlements,
                          'objective': 'strict_full_win_excluding_push; tie within 1e-12: settlement balance then over'}
    return value
