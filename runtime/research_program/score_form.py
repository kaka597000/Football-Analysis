"""Six-game, competition-specific statistical score experiment. Parameters belong to an independent candidate."""
from datetime import date
import math

import numpy as np
from scipy.stats import poisson

from .three_arm_model import top_three_scores

VERSION = 'WCPP-SCORE-FORM-1'
WINDOW = 6
PRIOR_GOALS = 1.3
FEATURES = ('home_advantage', 'own_scored', 'opponent_conceded', 'own_gap', 'opponent_gap',
            'own_xgf', 'opponent_xga')
GOALS_FEATURES = FEATURES[:5]


def summarize(games, target_date):
    """Games are strictly earlier, sorted most recent first, and in team perspective."""
    if len(games) > WINDOW: raise ValueError('at most six games required')
    n = len(games)
    gap = (target_date-date.fromisoformat(games[0]['date'])).days if n else 21
    if any(date.fromisoformat(r['date']) >= target_date for r in games):
        raise ValueError('form must predate target day')
    if any(a['date'] < b['date'] for a,b in zip(games,games[1:])):
        raise ValueError('form must be ordered latest first')
    def avg(field):
        return (sum(r[field] for r in games) + 4*PRIOR_GOALS)/(n+4)
    scored, conceded = avg('gf'), avg('ga')
    xg = [r for r in games if r.get('xgf') is not None and r.get('xga') is not None]
    def shrink_xg(key, fallback):
        return (sum(r[key] for r in xg)+4*fallback)/(len(xg)+4)
    return {'n':n, 'xg_n':len(xg), 'gap':min(21,gap), 'gf':scored, 'ga':conceded,
            'xgf':shrink_xg('xgf',scored), 'xga':shrink_xg('xga',conceded),
            'raw_xgf':sum(r['xgf'] for r in xg)/len(xg) if xg else None,
            'raw_xga':sum(r['xga'] for r in xg)/len(xg) if xg else None,
            'points':(sum(3 if r['gf']>r['ga'] else int(r['gf']==r['ga']) for r in games)+4*PRIOR_GOALS)/(n+4),
            'btts':sum(min(r['gf'],r['ga'])>0 for r in games)/max(1,n),
            'over25':sum(r['gf']+r['ga']>2.5 for r in games)/max(1,n)}


def features(home, away, neutral=False):
    def side(own, opponent, venue):
        return [venue, math.log(own['gf']), math.log(opponent['ga']), math.log1p(own['gap']),
                math.log1p(opponent['gap']), math.log(own['xgf']), math.log(opponent['xga'])]
    return np.asarray([side(home,away,int(not neutral)),side(away,home,0)])


def rates(model, x):
    names = tuple(model['features'])
    if names not in (GOALS_FEATURES,FEATURES): raise ValueError('unknown form feature order')
    return np.exp(model['intercept'] + x[:,:len(names)] @ np.asarray(model['coefficients']))


def poisson_matrix(means, shape):
    if len(shape)!=2 or min(shape)<5 or not np.isfinite(means).all() or min(means)<=0:
        raise ValueError('positive finite means and adequate joint support required')
    m=np.outer(poisson.pmf(np.arange(shape[0]),means[0]),poisson.pmf(np.arange(shape[1]),means[1]))
    if 1-m.sum()>1e-8: raise ValueError('material score probability outside matrix support')
    return m/m.sum()


def cell_index(shape):
    return 5*np.minimum(4,np.arange(shape[0])[:,None])+np.minimum(4,np.arange(shape[1]))


def calibrate(matrix, factors, weight):
    f=np.asarray(factors,dtype=float)
    if f.shape!=(25,) or not np.isfinite(f).all() or (f<=0).any() or weight not in (0,.5,1):
        raise ValueError('invalid score-cell calibration')
    m=matrix*np.power(f[cell_index(matrix.shape)],weight)
    return m/m.sum()


def form_matrix(artifact, home, away, shape, neutral=False):
    if artifact is None:
        means=np.array([(home['xgf']+away['xga'])/2,(away['xgf']+home['xga'])/2])
        return poisson_matrix(means,shape)
    means=rates(artifact['xg'],features(home,away,neutral))
    return calibrate(poisson_matrix(means,shape),artifact['cell_factors'],artifact['weight'])


def select_scores(candidate, proposed=None):
    """Keep a proposed exact score when either independent input ranks it first."""
    supported=proposed is not None and proposed in candidate['expert_primaries']
    primary=proposed if supported else candidate['pooled_top3'][0]
    alternates=[s for s in candidate['pooled_top3'] if s!=primary][:2]
    return {'primary':primary,'alternates':alternates,'proposed_primary':proposed,
            'decision':'SUPPORTED_PROPOSAL' if supported else 'POOLED_MODE',
            'override_changed':None if proposed is None else primary!=proposed}


def rerank_issued(issued, preferred):
    """Reorder an existing three-score issue without losing any covered outcome."""
    if len(issued)!=3 or len(set(issued))!=3:
        return None
    first=preferred if preferred in issued else issued[0]
    return [first,*(s for s in issued if s!=first)]


def selection_inputs(test, form):
    if test.shape!=form.shape: raise ValueError('paired score support differs')
    pooled=(test+form)/2
    return pooled, {'pooled_top3':top_three_scores(pooled),
                    'expert_primaries':[top_three_scores(test)[0],top_three_scores(form)[0]]}
