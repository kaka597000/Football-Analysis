"""A single regularized joint score distribution with causal cross-phase team priors."""
from collections import defaultdict
from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize
from scipy.special import logsumexp

from . import all_scope_model as base
from .score_form import poisson_matrix
from .three_arm_model import _market_matrix

VERSION = 'R6-conditional-score-1'
RIDGE = 50.
BOUND = .65
H, A = base.H, base.A
GOALS = H + A
NAMES = ('0-0', '0-1', '1-0', '1-1', 'four_plus', 'six_plus', 'draw',
         'favourite_margin', 'home_rate_residual', 'away_rate_residual', 'total_rate_residual')
# Collapse cells only during fitting. Cells with identical features share a tilt,
# but retain their original probabilities, rank, and exact identities at inference.
_cell_features = np.stack(((H == 0) & (A == 0), (H == 0) & (A == 1),
    (H == 1) & (A == 0), (H == 1) & (A == 1), GOALS >= 4, GOALS >= 6, H == A,
    np.clip(H-A, -4, 4)/2, np.minimum(H, 6), np.minimum(A, 6), np.minimum(GOALS, 6)), axis=-1)
FEATURES, GROUP = np.unique(_cell_features.reshape(-1, len(NAMES)), axis=0, return_inverse=True)
GROUP = GROUP.reshape(H.shape)
ZERO_CELL = np.zeros(25)
ZERO_COEFS = np.zeros(len(NAMES))


def process_proxy(row):
    """Paired observed xG takes priority, followed by a shots proxy from both teams."""
    if not row:
        return None
    for field in ('xg', 'sot', 'shots'):
        values = [row.get(f'{side}_{field}') for side in ('home', 'away')]
        if not all(v is not None and np.isfinite(v) and v >= 0 for v in values):
            continue
        pair = np.array(values, dtype=float)
        if field == 'xg':
            return pair
        if field == 'shots':
            return .106 * pair
        shots = [row.get(f'{side}_shots') for side in ('home', 'away')]
        if all(v is not None and np.isfinite(v) and v >= s for v, s in zip(shots, pair)):
            return .22 * pair + .044 * (np.array(shots)-pair)
        return .30 * pair
    return None


def _process_rating(state, name, day, half_life):
    team = state.get(name)
    if team is None:
        return np.zeros(2), 0.
    previous, exposure, last = team
    age = day - last
    if age < 0:
        raise ValueError('process state lies in the future')
    decay = .5**(age / half_life)
    return decay * np.asarray(previous), decay * exposure


def _process_prior(state, game, day, half_life, venue):
    home, nh = _process_rating(state, game['home'], day, half_life)
    away, na = _process_rating(state, game['away'], day, half_life)
    signal = venue * np.exp(np.clip([home[0]+away[1], away[0]+home[1]], -1.5, 1.5))
    exposure = (nh + na) / 2
    return signal, exposure


def _update_process(state, games, day, half_life):
    delta = defaultdict(lambda: np.zeros(3))
    for game, expected, observation in games:
        value = process_proxy(observation)
        if value is None:
            continue
        residual = .075 * np.clip(value - expected, -3., 3.)
        delta[game['home']] += [residual[0], residual[1], 1.]
        delta[game['away']] += [residual[1], residual[0], 1.]
    for team, change in delta.items():
        old, n = _process_rating(state, team, day, half_life)
        state[team] = [np.clip(old+change[:2], -1.5, 1.5).tolist(), n+change[2], day]


def blank():
    return dict(pooled=base.blank_state(), phases={}, process={})


def dump(state):
    return dict(pooled=base.dump_state(state['pooled']), process=state['process'],
                phases={k:base.dump_state(v) for k,v in state['phases'].items()})


def load(state):
    return dict(pooled=base.load_state(state['pooled']), process=state['process'],
                phases={k:base.load_state(v) for k,v in state['phases'].items()})


def _anchor(game, rates):
    matrix, kind, hprob, over = base.baseline(game, poisson_matrix(rates, H.shape))
    if kind == 'hda_only' and over is not None:
        # The input declares different captures or books. Combine the observed
        # marginals for the model; the pure same-book score benchmark stays absent.
        matrix = _market_matrix(matrix, (hprob, base.actual_line(game), over))
        kind = 'separate_markets'
    return matrix, kind


def components(state, game, day, half_life):
    phase = state['phases'].get(game['stage'])
    if phase is None:
        phase = base.blank_state()
    _, local, local_misc = base.rates(phase, game, day, half_life)
    venue, pooled, global_misc = base.rates(state['pooled'], game, day, half_life)
    structural = pooled**.75 * local**.25
    process, n = _process_prior(state['process'], game, day, half_life, venue)
    w = .5*n/(n+6.)
    signal = structural**(1-w) * process**w
    anchor, kind = _anchor(game, structural)
    process_anchor = anchor if kind == 'paired' or n == 0 else _anchor(game, signal)[0]
    local_anchor = anchor if kind == 'paired' else base.baseline(game, poisson_matrix(local,H.shape))[0]
    hda = base._prices(game.get('h2h'), base.SIDES)
    ou = base._prices(game.get('total'), ('over','under')) if base.actual_line(game) is not None else None
    market = dict(kind='paired' if kind == 'paired' else kind,
                  hprob=base._wdl(hda) if hda else None, over=ou[1]/sum(ou) if ou else None,
                  line=base.actual_line(game))
    return dict(local_rate=local, pooled_rate=pooled, process_expected=process, process_exposure=n,
                local_games=[round(np.expm1(v)) for v in local_misc[2:4]],
                pooled_games=[round(np.expm1(v)) for v in global_misc[2:4]], local=local_anchor,
                structural=anchor, process_anchor=process_anchor, signal=signal, goal_signal=structural,
                market=market)


def advance(state, games, day, half_life):
    base.advance(state['pooled'], day, half_life)
    for stage in {g['stage'] for g in games}:
        base.advance(state['phases'].setdefault(stage,base.blank_state()),day,half_life)


def update(state, prepared, observations, day, half_life):
    phases = defaultdict(list)
    pooled, process = [], []
    for game, p in prepared:
        row = observations.get(game['id'])
        if row and any(row[k] != game[k] for k in ('competition','date','home','away')):
            raise ValueError('process fixture identity mismatch: '+game['id'])
        phases[game['stage']].append((game, p['local_rate'], ZERO_CELL))
        pooled.append((game, p['pooled_rate'], ZERO_CELL))
        process.append((game, p['process_expected'], row))
    for stage, group in phases.items():
        base.update_day(state['phases'][stage],group,day,half_life)
    base.update_day(state['pooled'],pooled,day,half_life)
    _update_process(state['process'],process,day,half_life)


def design(matrix, rates):
    strength = float(matrix[H>A].sum() - matrix[A>H].sum())
    means = np.array([(H*matrix).sum(), (A*matrix).sum()])
    residual = np.clip(np.log(rates / means), -.9, .9)
    total = np.clip(np.log(np.sum(rates)/means.sum()), -.9, .9)
    return FEATURES * np.array([1.]*7+[strength,*residual,total])


@dataclass
class Example:
    mass: np.ndarray
    features: np.ndarray
    observed: int


def example(matrix, rates, score):
    if min(score)<0 or max(score)>=len(H):
        raise ValueError('observation outside exact score support')
    return Example(np.bincount(GROUP.ravel(), weights=matrix.ravel(), minlength=len(FEATURES)),
                   design(matrix, rates), int(GROUP[score[0], score[1]]))


def fit(examples):
    if not examples:
        return ZERO_COEFS.copy()
    mass = np.stack([r.mass for r in examples])
    log_prior = np.log(np.maximum(mass, np.finfo(float).tiny))
    features = np.stack([r.features for r in examples])
    found = features[np.arange(len(examples)), [r.observed for r in examples]].sum(axis=0)
    def objective(beta):
        logits = log_prior + features @ beta
        norm = logsumexp(logits, axis=1)
        expected = np.einsum('ng,ngk->k', np.exp(logits - norm[:,None]), features)
        return norm.sum()-found@beta+RIDGE*(beta@beta)/2, expected-found+RIDGE*beta
    result = minimize(objective, ZERO_COEFS.copy(), jac=True, method='L-BFGS-B',
                      bounds=[(-BOUND,BOUND)]*len(NAMES), options={'ftol':1e-12,'gtol':1e-6})
    if not result.success:
        raise ValueError('score residual fit failed: '+result.message)
    return result.x


def distribution(matrix, rates, beta):
    weights = np.exp(design(matrix,rates) @ beta)
    posterior = matrix * weights[GROUP]
    return posterior/posterior.sum()


def _quality(matrix, score, line):
    ordered = np.partition(matrix.ravel(), -3)[-3:]
    wdl = np.bincount(base.RESULT.ravel(),weights=matrix.ravel(),minlength=3)
    out = dict(p1=float(ordered.max()), p3=float(ordered.sum()), wdl_max=float(wdl.max()))
    if score is not None:
        home,away = score
        out['score_nll'] = -float(np.log(max(matrix[home,away],np.finfo(float).tiny)))
        truth = 0 if home>away else 2 if away>home else 1
        out['wdl_brier'] = float(np.square(wdl-np.eye(3)[truth]).sum())
    if line is not None:
        net = base._net(line)
        out['full_mass'] = max(float(matrix[net==1].sum()),float(matrix[net==-1].sum()))
        out['nonpush_mass'] = float(matrix[net!=0].sum())
    return out


def forecast(game, p, coefficients, *, score=None):
    line = base.actual_line(game)
    structural = p['structural']
    no_process = distribution(structural,p['goal_signal'],coefficients['goals'])
    full = distribution(p['process_anchor'],p['signal'],coefficients['process'])
    matrices = dict(local_anchor=p['local'], structural=structural, no_process=no_process, conditional=full)
    picks = {label:base.pick(matrix,line) for label,matrix in matrices.items()}
    market = base.market_picks(p['market'],game,{'anchor':picks['local_anchor']})
    for name in ('no_process','conditional'):
        for key in ('wdl','total_side','total_line'):
            if key in market:
                picks[name][key] = market[key]
    picks['market'] = market
    if p['market']['kind']=='separate_markets':
        independent = base._full_market(base._prices(game['h2h'],base.SIDES),line,
                                        base._prices(game['total'],('over','under')))
        picks['price_pair_proxy'] = base.pick(independent,line)
        matrices['price_pair_proxy'] = independent
    elif p['market']['kind']=='paired':
        picks['price_pair_proxy'] = market.copy()
        matrices['price_pair_proxy'] = p['local']
    quality = {label:_quality(matrix,score,line) for label,matrix in matrices.items()}
    if p['market']['kind']=='paired':
        quality['market'] = quality['local_anchor']
    return picks,quality
