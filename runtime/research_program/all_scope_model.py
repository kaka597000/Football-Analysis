"""Competition- and phase-local score models with causal features and separately selected heads."""
from collections import defaultdict
from functools import lru_cache
import math

import lightgbm as lgb
import numpy as np
from scipy.optimize import brentq

from .elo_kelly_shadow import devig
from .market_baseline import quote_only_matrices
from .score_form import poisson_matrix
from .three_arm_model import score_picks, top_three_scores

VERSION = 'WCPP-ALL-SCOPE-SCORE-1'
ARMS = ('anchor', 'team', 'calibrated', 'boosted', 'venue')
SIZE = 25
H, A = np.indices((SIZE, SIZE))
RESULT = np.where(H>A, 0, np.where(H==A, 1, 2))
CELL = (np.minimum(H,4)*5 + np.minimum(A,4)).ravel()
SIDES = ('home','draw','away')
FEATURES = ('team_home','team_away','venue_home','venue_away','neutral','venue_unknown',
            'home_games','away_games','home_rest','away_rest','p_home','p_draw','p_away',
            'actual_line','over_share','anchor_home','anchor_away')
PARAMS = dict(objective='poisson', learning_rate=.04, num_leaves=7, min_data_in_leaf=20,
              lambda_l2=8., feature_pre_filter=False, num_threads=1, deterministic=True,
              force_col_wise=True, verbosity=-1, seed=20261123)


def blank_state():
    return dict(day=None, venue_n=[0.,0.], venue_goals=[[0.,0.],[0.,0.]], teams={},
                observed=np.zeros(25), expected=np.zeros(25))


def dump_state(s):
    return dict(s, observed=s['observed'].tolist(), expected=s['expected'].tolist())


def load_state(s):
    # Deserialized teams and counters are updated only in the new run.
    return dict(s, observed=np.array(s['observed'],float), expected=np.array(s['expected'],float))


def advance(state, day, half_life):
    previous=state['day']
    if previous is not None:
        if day <= previous:
            raise ValueError('state updates must advance by full match date')
        decay=.5**((day-previous)/half_life)
        state['venue_n']=[v*decay for v in state['venue_n']]
        state['venue_goals']=[[v*decay for v in side] for side in state['venue_goals']]
        state['observed']*=decay
        state['expected']*=decay
    state['day']=day


def _rating(state, name, day, half_life):
    value=state['teams'].get(name)
    if value is None:
        return np.zeros(2), 0, math.nan
    attack, concession, n, last=value
    days=day-last
    return np.array([attack,concession])*(.5**(days/half_life)), n, min(days,365)


def rates(state, game, day, half_life):
    neutral=int(game.get('neutral') is True)
    prior=np.array([1.32,1.32] if neutral else [1.48,1.19])
    venue=(np.asarray(state['venue_goals'][neutral])+20*prior)/(state['venue_n'][neutral]+20)
    if neutral:
        venue[:]=venue.mean()
    home,nh,rh=_rating(state,game['home'],day,half_life)
    away,na,ra=_rating(state,game['away'],day,half_life)
    team=np.clip(venue*np.exp(np.clip([home[0]+away[1],away[0]+home[1]],-2.,2.)),.1,6.)
    return venue,team,[neutral,int(game.get('neutral') is None),math.log1p(nh),math.log1p(na),rh,ra]


def update_day(state, prepared, day, half_life):
    adjustments=defaultdict(lambda: np.zeros(3))
    for game, team, expected in prepared:
        h,a=game['home_goals'],game['away_goals']
        residual=np.clip(np.array([h,a])-team,-3.,3.)*.07
        adjustments[game['home']]+=np.array([residual[0],residual[1],1.])
        adjustments[game['away']]+=np.array([residual[1],residual[0],1.])
        neutral=int(game.get('neutral') is True)
        state['venue_n'][neutral]+=1.
        state['venue_goals'][neutral][0]+=h
        state['venue_goals'][neutral][1]+=a
        state['observed'][min(h,4)*5+min(a,4)]+=1.
        state['expected']+=expected
    for name,change in adjustments.items():
        old,n,_=_rating(state,name,day,half_life)
        attack,concession=np.clip(old+change[:2],-1.5,1.5)
        state['teams'][name]=[float(attack),float(concession),int(n+change[2]),day]


def _prices(prices, sides):
    if not isinstance(prices,dict):
        return None
    values=tuple(prices.get(s) for s in sides)
    if any(type(v) not in (int,float) or not math.isfinite(v) or v<=1 for v in values):
        return None
    return values


def actual_line(game):
    t=game.get('total') or {}
    line=t.get('line')
    if type(line) not in (int,float) or not math.isfinite(line) or line<=0 or not float(line*4).is_integer():
        return None
    return float(line)


@lru_cache(maxsize=768)
def _full_market(hda, line, ou):
    _,m=quote_only_matrices(dict(zip(SIDES,hda)),dict(line=line,over=ou[0],under=ou[1]))
    m=m[:SIZE,:SIZE].copy()
    if 1.-m.sum()>1e-8:
        raise ValueError('material market tail outside score support')
    return m/m.sum()


@lru_cache(maxsize=128)
def _net(line):
    goals=H+A
    legs=(line,) if (2*line).is_integer() else (line-.25,line+.25)
    return sum(np.sign(goals-leg) for leg in legs)/len(legs)


def _total_projection(matrix, line, share):
    net=_net(line)
    category=(2*net+2).astype(int)
    n=np.arange(-1.,1.01,.5)
    feature=np.maximum(n,0.)-share*abs(n)
    mass=np.bincount(category.ravel(), weights=matrix.ravel(), minlength=5)
    def equation(t):
        return float((mass*np.exp(t*feature))@feature)
    coefficient=brentq(equation,-100.,100.)
    tilted=matrix*np.exp(coefficient*feature[category])
    return tilted/tilted.sum()


def baseline(game, team):
    hda=_prices(game.get('h2h'),SIDES)
    line=actual_line(game)
    ou=_prices(game.get('total'),('over','under')) if line is not None else None
    hprob=None
    if hda:
        hprob=np.array(_wdl(hda))
    over=ou[1]/sum(ou) if ou else None
    if hda and ou and game.get('paired_market') is True:
        m=_full_market(hda,line,ou)
        return m,'paired',hprob,over
    if hda:
        implied=np.bincount(RESULT.ravel(),weights=team.ravel(),minlength=3)
        m=team*(hprob/implied)[RESULT]
        return m/m.sum(),'hda_only',hprob,over
    if ou:
        return _total_projection(team,line,over),'total_only',None,over
    return team,'team_fallback',None,None


@lru_cache(maxsize=4096)
def _wdl(hda):
    fair,_=devig(dict(zip(SIDES,hda)))
    return tuple(fair[k] for k in SIDES)


def prepare(state, game, day, half_life):
    venue,team,misc=rates(state,game,day,half_life)
    team_m=poisson_matrix(team,(SIZE,SIZE))
    anchor,kind,hprob,over=baseline(game,team_m)
    cell=np.bincount(CELL,weights=anchor.ravel(),minlength=25)
    line=actual_line(game)
    f=[*team,*venue,*misc,*(hprob if hprob is not None else [np.nan]*3),
       line if line is not None else np.nan,over if over is not None else np.nan,
       float((anchor*H).sum()),float((anchor*A).sum())]
    return dict(venue=venue,team=team,team_m=team_m,anchor=anchor,kind=kind,hprob=hprob,
                over=over,cell=cell,features=f,line=line)


def fit_trees(examples):
    if not examples:
        return None
    X=np.asarray([r[0] for r in examples],float)
    prior=np.log([r[1] for r in examples])
    labels=np.asarray([r[2] for r in examples],int)
    if labels.max(axis=0).min()==0 or len(examples)<2*PARAMS['min_data_in_leaf']:
        # No legal split or no positive label for one side: keep the offset.
        return None
    models=[]
    for side in (0,1):
        data=lgb.Dataset(X,label=labels[:,side],init_score=prior[:,side],feature_name=list(FEATURES))
        models.append(lgb.train(PARAMS,data,num_boost_round=40))
    return models


def matrices(state, p, correction=None):
    anchor=p['anchor']
    prior=np.maximum(p['cell'],1e-6)
    weights=(state['observed']+120*prior)/(state['expected']+120*prior)
    cal=anchor*np.clip(weights,.5,2.)[CELL.reshape(anchor.shape)]
    improved=p['team'] if correction is None else np.clip(p['team']*np.exp(np.clip(correction,-2.,2.)),.1,6.)
    boost=poisson_matrix(improved,(SIZE,SIZE))
    return dict(anchor=anchor,team=p['team_m'],venue=poisson_matrix(p['venue'],(SIZE,SIZE)),
                calibrated=cal/cal.sum(),boosted=.5*(anchor+boost))


def pick(matrix,line):
    p=score_picks(matrix,line=line)
    wdl=p['wdl_probabilities']
    winner=next(k for k in SIDES if wdl[k]>=max(wdl.values())-1e-12)
    return dict(primary=p['primary'],top3=top_three_scores(matrix),wdl=winner,
                total_side=p['total']['direction'] if line is not None else None,total_line=line)


def market_picks(p,game,picks):
    if p['kind']=='paired':
        return dict(picks['anchor'])
    out={}
    if p['hprob'] is not None:
        out['wdl']=SIDES[int(np.argmax(p['hprob']))]
    if p['over'] is not None:
        # With partial quotes use a market-total-only Poisson reference; the two
        # equal goal rates below are nuisance parameters for the totals direction.
        ou=_prices(game['total'],('over','under'))
        out['total_side']=_ou_reference(p['line'],ou)
        out['total_line']=p['line']
    return out


@lru_cache(maxsize=2048)
def _ou_reference(line,ou):
    # Equal opposite sides make home/away names irrelevant on the total market.
    from scipy.stats import poisson
    net=_net(line)[:,0]
    share=ou[1]/sum(ou)
    f=np.maximum(net,0.)-share*abs(net)
    scores=np.arange(len(net))
    rate=brentq(lambda x:poisson.pmf(scores,x)@f,.001,24.)
    m=poisson_matrix([rate/2,rate/2],(SIZE,SIZE))
    return pick(m,line)['total_side']
