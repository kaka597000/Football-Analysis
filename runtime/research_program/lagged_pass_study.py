"""Cross-date ablation of passing-network features on Wyscout's five 2017/18 domestic leagues."""
import argparse
from collections import defaultdict
from itertools import groupby
import json
from pathlib import Path

import numpy as np
from scipy.optimize import minimize

from . import all_scope_model as base
from . import continuation_scores as model
from .continuation_study import put, read_gzip
from .goal_state_study import write_new

VOLUME=('pass_volume','accuracy')
NETWORK=('density','weighted_outgoing_entropy_normalized','largest_sender_share','largest_receiver_share','reciprocity')
PRIOR=np.array([420.,.76,.48,.64,.15,.14,.55])
SCALE=np.array([140.,.12,.12,.12,.06,.06,.16])
ARMS=('score_only','past_volume','past_network')


def compress(path,odds,out):
    """Expose only summarized past interactions, separate provider outcome labels and the matched price receipt."""
    prices={str(r['wyscout_match_id']):r for r in (json.loads(l) for l in odds.open())}
    rows=[]
    for line in path.open():
        g=json.loads(line);wid=str(g['id']['value'])
        q=prices[wid];stamp=g['date']['utc'][:10]
        if (q['competition']!=g['competition'] or q['date']!=stamp or q['score_90']!=g['score_90'] or
                q['football_data_match_id']!=g['football_data_match_id']):
            raise ValueError('Wyscout and quote fixture mismatch: '+wid)
        hda=q['bet365_1x2'] if base._prices(q['bet365_1x2'],base.SIDES) else q['bbav_1x2']
        bets=base._prices(q['bet365_ou25'],('over','under'))
        totals=bets or base._prices(q['bbav_ou25'],('over','under'))
        total=dict(line=2.5,over=totals[0],under=totals[1]) if totals else None
        paired=bool(bets and base._prices(q['bet365_1x2'],base.SIDES))
        item=dict(id='wyscout:'+wid,competition=g['competition'],date=stamp,home=str(g['home']['id']),
            away=str(g['away']['id']),stage='regular',neutral=False,home_goals=g['score_90']['home'],
            away_goals=g['score_90']['away'],source_sha256=g['source_sha256']['matches'],h2h=hda,
            total=total,paired_market=paired,price_source_sha256=q['source_sha256'])
        for side in ('home','away'):
            s=g[side+'_stats']
            item[side+'_passing']=[s['pass_volume'],s['successful_passes']/s['pass_volume']]+[s[k] for k in NETWORK]
        rows.append(item)
    if len(rows)!=len(prices) or len({g['id'] for g in rows})!=len(rows):
        raise ValueError('passing/price coverage differs')
    put(out,sorted(rows,key=lambda r:(r['competition'],r['date'],r['id'])))


def prior_passing(rows):
    """Apply a whole match date only after each team has received its prematch features."""
    history={}
    result=[]
    for day,items in groupby(rows,key=lambda r:r['date']):
        games=list(items)
        for g in games:
            vectors=[]
            for side in ('home','away'):
                value,n=history.get(g[side],(np.zeros(len(PRIOR)),0.))
                vectors.append(value/(2.+n))
            result.append(np.r_[(vectors[0]-vectors[1]), (vectors[0]+vectors[1])/2])
        updates=defaultdict(list)
        for g in games:
            for side in ('home','away'):
                metrics=np.asarray(g[side+'_passing'])
                updates[g[side]].append((metrics-PRIOR)/SCALE)
        for team,values in updates.items():
            v,n=history.get(team,(np.zeros(len(PRIOR)),0.))
            history[team]=(.75*v+sum(values),.75*n+len(values))
    return np.stack(result)


def fit(x,means,y):
    coeff=[]
    x=np.column_stack((np.ones(len(x)),x))
    for side in (0,1):
        def loss(b):
            log_rate=np.log(means[:,side])+x@b
            rate=np.exp(log_rate)
            return rate.sum()-y[:,side]@log_rate+50*b@b,x.T@(rate-y[:,side])+100*b
        sol=minimize(loss,np.zeros(x.shape[1]),jac=True,method='L-BFGS-B')
        if not sol.success:
            raise ValueError('passing Poisson fit failed')
        coeff.append(sol.x)
    return np.column_stack(coeff)


def adjust(prior,x,coefs):
    delta=np.clip(np.column_stack((np.ones(len(x)),x))@coefs,-.8,.8)
    result=prior*np.exp(delta[:,0,None,None]*base.H+delta[:,1,None,None]*base.A)
    return result/result.sum(axis=(1,2),keepdims=True)


def run(panel,out):
    records=read_gzip(panel)
    out.mkdir(parents=True,exist_ok=True)
    result=[];params={}
    for code,group in groupby(records,key=lambda r:r['competition']):
        rows=list(group);goals=np.array([[g['home_goals'],g['away_goals']] for g in rows])
        prior,x,_=model.inputs(rows,code)
        means=np.column_stack(((prior*base.H).sum((1,2)),(prior*base.A).sum((1,2))))
        z=prior_passing(rows)
        volume=[0,1,len(PRIOR),len(PRIOR)+1]
        n=sum(g['date']<'2017-12-01' for g in rows);v=sum(g['date']<'2018-02-01' for g in rows)
        payload=[dict(id=g['id'],cohort=code,stage='regular',home=g['home'],away=g['away'],date=g['date'],
                      actual=[g['home_goals'],g['away_goals']],window='passing',line=base.actual_line(g),
                      source_sha256=g['source_sha256'],forecast={},quality={}) for g in rows[v:]]
        params[code]={'training':n,'selection':v-n,'assessment':len(rows)-v,'arms':{}}
        for arm,features in zip(ARMS,(x,np.column_stack((x,z[:,volume])),np.column_stack((x,z)))):
            coef=fit(features[:n],means[:n],goals[:n])
            test=adjust(prior[n:],features[n:],coef)
            sel=model.choose(prior[n:v],{'poisson':test[:v-n]},goals[n:v])
            mix=sel['mix']
            for r,m in zip(payload,(1-mix)*prior[v:]+mix*test[v-n:]):
                h,a=r['actual']
                r['forecast'][arm]=base.pick(m,r['line'])
                r['quality'][arm]={'p_observed':float(m[h,a]),'p_first':float(m.max()),
                    'p_three':float(np.partition(m.ravel(),-3)[-3:].sum()),
                    'wdl':[float(m[mask].sum()) for mask in (base.H>base.A,base.H==base.A,base.H<base.A)]}
            params[code]['arms'][arm]=dict(selection=sel,beta=coef.tolist())
        result.extend(payload)
    put(out/'forecasts.jsonl.gz',result)
    write_new(out/'fit.json',dict(feature_order=list(VOLUME+NETWORK),score_fields=model.COMMON,models=params))


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source',type=Path);ap.add_argument('--odds',type=Path)
    ap.add_argument('--panel',type=Path,required=True);ap.add_argument('--out',type=Path,required=True)
    a=ap.parse_args()
    if a.source:compress(a.source,a.odds,a.panel)
    run(a.panel,a.out)
