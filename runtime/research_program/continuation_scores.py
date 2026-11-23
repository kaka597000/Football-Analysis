"""A prematch score distribution factored into total goals and conditional home goals."""
from collections import defaultdict
from datetime import date
from itertools import groupby

import lightgbm as lgb
import numpy as np
from scipy.optimize import minimize
from scipy.special import expit, logit

from . import all_scope_model as base
from .all_scope_study import half_life
from .score_form import poisson_matrix

VERSION = 'R7-continuation-1'
CAP = 8
TOTAL = base.H + base.A
STEPS = [(n,j) for n in range(1,CAP+1) for j in range(n)]
INDEX_N = np.array([n for n,j in STEPS])
INDEX_J = np.array([j for n,j in STEPS])
GE_TOTAL = np.array([TOTAL>=i for i in range(CAP+2)])
GOAL_DIFF_SCALE = 3.
COMMON = ('expected_total','home_share','home_win_mass','draw_mass','pi_difference','home_experience',
          'away_experience','neutral','unknown_venue','has_wdl','has_totals','stage_knockout')
GRID = (0.,.25,.5,.75,1.)


def blank():
    return {'goals':base.blank_state(),'pi':{}}


def dump(state):
    return {'goals':base.dump_state(state['goals']),'pi':state['pi']}


def load(blob):
    return {'goals':base.load_state(blob['goals']),'pi':{k:list(v) for k,v in blob['pi'].items()}}


def signed_log(x):
    return np.sign(x)*GOAL_DIFF_SCALE*np.log10(1+np.abs(x))


def rated(state,team,day,hl):
    home,away,last=state.get(team,(0.,0.,day))
    if last>day:
        raise ValueError('team rating originates after forecast')
    return np.array([home,away])* .5**((day-last)/hl)


def prepare(state, game, day, hl):
    venue, rates, misc=base.rates(state['goals'],game,day,hl)
    n=bool(game.get('neutral'))
    home=rated(state['pi'],game['home'],day,hl)
    away=rated(state['pi'],game['away'],day,hl)
    gap=float(home.mean()-away.mean() if n else home[0]-away[1])
    # Express the venue rating as an additional goal difference at the same total.
    goal_gap=np.sign(gap)*np.expm1(abs(np.clip(gap,-2.5,2.5))*np.log(10)/GOAL_DIFF_SCALE)
    pi_share=np.clip(.5+(goal_gap+venue[0]-venue[1])/(2*sum(rates)),.08,.92)
    prior_share=.65*rates[0]/sum(rates)+.35*pi_share
    prior=poisson_matrix(np.array([prior_share,1-prior_share])*sum(rates),base.H.shape)
    matrix,kind,hda,over=base.baseline(game,prior)
    if kind=='hda_only' and over is not None:
        from .three_arm_model import _market_matrix
        matrix=_market_matrix(matrix,(hda,base.actual_line(game),over))
    mh=float((matrix*base.H).sum());ma=float((matrix*base.A).sum())
    stage=(game.get('stage') or '').lower()
    x=[(mh+ma-2.7)/1.3,(mh/(mh+ma)-.5)*3,matrix[base.H>base.A].sum()-.45,
       matrix[base.H==base.A].sum()-.26, gap, misc[2]/4.,misc[3]/4.,
       int(n),int(game.get('neutral') is None),int(hda is not None),int(over is not None),
       int(any(t in stage for t in ('knockout','final','leg-','round-of','quarter','semi')))]
    return matrix,x,(rates,home,away,gap,venue)


def update(state,games,prior,day,hl):
    delta=defaultdict(lambda:np.zeros(2))
    zero=np.zeros(25)
    for g,(_,home,away,gap,venue) in zip(games,prior):
        margin=g['home_goals']-g['away_goals']-(venue[0]-venue[1])
        residual=float(np.clip(signed_log(margin)-gap,-3,3))*.11
        home_step=np.array([.7,.7]) if g.get('neutral') else np.array([1.,.35])
        delta[g['home']]+=home_step*residual
        delta[g['away']]-=home_step[::-1]*residual
    for team,change in delta.items():
        value=np.clip(rated(state['pi'],team,day,hl)+change,-2.5,2.5)
        state['pi'][team]=[*map(float,value),day]
    base.update_day(state['goals'],[(g,p[0],zero) for g,p in zip(games,prior)],day,hl)


def inputs(rows,competition):
    state=blank()
    matrices=[];features=[]
    hl=half_life(competition)
    for stamp,items in groupby(rows,key=lambda r:r['date']):
        games=list(items);day=date.fromisoformat(stamp).toordinal()
        base.advance(state['goals'],day,hl)
        group=[prepare(state,g,day,hl) for g in games]
        matrices.extend(p[0] for p in group)
        features.extend(p[1] for p in group)
        update(state,games,[p[2] for p in group],day,hl)
    return np.array(matrices),np.array(features),state


def hazard_inputs(matrices,features):
    # Only candidate totals and candidate allocation thresholds enter this design.
    # The observed goals determine training labels and exposure later.
    totals=np.column_stack([matrices[:,mask].sum(axis=1) for mask in GE_TOTAL])
    total_p=totals[:,1:]/np.maximum(totals[:,:-1],1e-280)
    share_p=np.empty((len(matrices),len(STEPS)))
    for n in range(1,CAP+1):
        ids=np.arange(n+1)
        cells=matrices[:,ids,n-ids]
        tails=np.cumsum(cells[:,::-1],axis=1)[:,::-1]
        share_p[:,INDEX_N==n]=tails[:,1:]/np.maximum(tails[:,:-1],1e-280)
    arrays=[]
    for raw,n,j in ((total_p,np.zeros(CAP+1),np.arange(CAP+1)),(share_p,INDEX_N,INDEX_J)):
        offset=logit(np.clip(raw,1e-10,1-1e-10))
        count=len(j)
        common=np.broadcast_to(features[:,None,:],(len(features),count,features.shape[1]))
        ncol=np.broadcast_to(n/4.,raw.shape); jcol=np.broadcast_to(j/4.,raw.shape)
        # Interactions let the ridge model correct draw/low-goal shape differently
        # for well-matched teams and strong favourites.
        out=np.concatenate((common,np.stack((ncol,jcol,jcol**2,ncol*jcol,offset/3,
            np.broadcast_to((j==0).astype(float),raw.shape),jcol*common[:,:,0],jcol*common[:,:,1]),axis=2)),axis=2)
        arrays.append((out,offset))
    return arrays


def labels(scores):
    h=np.asarray(scores)[:,0,None];n=np.sum(scores,axis=1)[:,None]
    steps=np.arange(CAP+1)
    return ((n>=steps,n>steps),((n==INDEX_N)&(h>=INDEX_J),h>INDEX_J))


def fit(arrays,scores):
    arms={'linear':[],'tree':[]}
    for (xx,offset),(at_risk,positive) in zip(arrays,labels(scores)):
        x=xx[at_risk];o=offset[at_risk];y=positive[at_risk].astype(float)
        if len(y)==0:
            arms['linear'].append([0.]*(xx.shape[2]+1));arms['tree'].append(None);continue
        design=np.column_stack((np.ones(len(y)),x))
        def objective(beta):
            eta=o+design@beta
            return np.logaddexp(0,eta).sum()-y@eta+35*(beta@beta),design.T@(expit(eta)-y)+70*beta
        opt=minimize(objective,np.zeros(design.shape[1]),jac=True,method='L-BFGS-B',
                     options={'maxiter':100,'ftol':1e-10})
        if not opt.success:
            raise ValueError('continuation logistic fit failed: '+opt.message)
        arms['linear'].append(opt.x.tolist())
        tree=None
        if len(np.unique(y))==2 and len(y)>=60:
            ds=lgb.Dataset(x,label=y,init_score=o)
            params=dict(objective='binary',num_leaves=7,learning_rate=.035,min_data_in_leaf=30,
                lambda_l2=12.,deterministic=True,force_col_wise=True,num_threads=1,verbosity=-1,seed=20261123)
            tree=lgb.train(params,ds,num_boost_round=42).model_to_string()
        arms['tree'].append(tree)
    return arms


def probabilities(arrays,params,arm):
    hazards=[]
    for (x,offset),value in zip(arrays,params[arm]):
        if arm=='linear':
            v=np.asarray(value);delta=v[0]+x@v[1:]
        elif value is None:
            delta=0.
        else:
            tree=lgb.Booster(model_str=value)
            delta=tree.predict(x.reshape(-1,x.shape[-1]),raw_score=True,num_threads=1).reshape(offset.shape)
        hazards.append(expit(offset+np.clip(delta,-5.,5.)))
    return hazards


def reconstruct(prior,total_prob,share_prob):
    survival=np.column_stack((np.ones(len(prior)),np.cumprod(total_prob,axis=1)))
    exact=survival[:,:-1]-survival[:,1:]
    output=np.zeros_like(prior)
    output[:,0,0]=exact[:,0]
    for n in range(1,CAP+1):
        tail=np.column_stack((np.ones(len(prior)),np.cumprod(share_prob[:,INDEX_N==n],axis=1)))
        mass=tail-np.column_stack((tail[:,1:],np.zeros(len(prior))))
        j=np.arange(n+1)
        output[:,j,n-j]=exact[:,n,None]*mass
    tail=TOTAL>CAP
    output[:,tail]=prior[:,tail]/prior[:,tail].sum(axis=1)[:,None]*survival[:,-1,None]
    return output


def arm_matrices(prior,features,params):
    data=hazard_inputs(prior,features)
    return {arm:reconstruct(prior,*probabilities(data,params,arm)) for arm in ('linear','tree')}


def choose(prior,alternatives,scores):
    if not len(scores):
        return {'arm':'linear','mix':0.,'validation_games':0,'log_loss':None}
    i=np.arange(len(scores));h,a=np.array(scores).T
    p=prior[i,h,a];results=[]
    for arm,other in alternatives.items():
        q=other[i,h,a]
        for weight in GRID:
            loss=-np.log(np.maximum((1-weight)*p+weight*q,1e-300)).mean()
            results.append((loss,weight,arm))
    loss,weight,arm=min(results)
    return {'arm':arm,'mix':weight,'validation_games':len(scores),'log_loss':float(loss)}
