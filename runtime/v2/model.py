"""Opponent-adjusted research candidates. Never substitutes for Champion V21.5."""
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy.optimize import minimize, minimize_scalar
from scipy.special import gammaln, logsumexp
from scipy.stats import poisson

VERSION='WCPP-CLOUD-CHALLENGER-0.2'
FAMILIES=('poisson','dixon_coles','bivariate')
CAP=50

def digest(x):
    return hashlib.sha256(json.dumps(x,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def aware(s):
    d=datetime.fromisoformat(s.replace('Z','+00:00'))
    if d.tzinfo is None: raise ValueError('timestamp must include timezone')
    return d

def validate_history(rows):
    seen=set();cohorts=set()
    for r in rows:
        if r['match_id'] in seen: raise ValueError('duplicate match')
        seen.add(r['match_id']);cohorts.add(r['cohort'])
        datetime.fromisoformat(r['date'])
        if r['score_basis']!='regulation_90' or not r['source_url'].startswith('https://'):
            raise ValueError('unverified score basis/source')
        for key in ('home_goals','away_goals'):
            if type(r[key]) is not int or not 0<=r[key]<=30: raise ValueError('invalid score')
    if len(cohorts)!=1: raise ValueError('cohorts must be fitted separately')
    return sorted(rows,key=lambda x:(x['date'],x['match_id']))

def fit_strength(rows,half_life=365.,ridge=8.):
    rows=validate_history(rows)
    if len(rows)<60: raise ValueError('insufficient history')
    teams=sorted({r[k] for r in rows for k in ('home','away')});index={t:i for i,t in enumerate(teams)};n=len(teams)
    h=np.array([index[r['home']] for r in rows]);a=np.array([index[r['away']] for r in rows])
    hg=np.array([r['home_goals'] for r in rows]);ag=np.array([r['away_goals'] for r in rows])
    dates=[datetime.fromisoformat(r['date']) for r in rows];end=max(dates)
    weight=np.array([2**(-(end-d).days/half_life) for d in dates])
    home=np.array([0. if r.get('neutral') else 1. for r in rows])
    def objective(v):
        attack=v[2:2+n]-np.mean(v[2:2+n]);defence=v[2+n:]-np.mean(v[2+n:])
        lh=np.exp(v[0]+v[1]*home+attack[h]+defence[a]);la=np.exp(v[0]+attack[a]+defence[h])
        loss=np.sum(weight*(lh-hg*np.log(lh)+la-ag*np.log(la)))+ridge*(attack@attack+defence@defence)
        rh=weight*(lh-hg);ra=weight*(la-ag)
        ga=np.bincount(h,weights=rh,minlength=n)+np.bincount(a,weights=ra,minlength=n)+2*ridge*attack
        gd=np.bincount(a,weights=rh,minlength=n)+np.bincount(h,weights=ra,minlength=n)+2*ridge*defence
        return loss,np.r_[sum(rh+ra),sum(rh*home),ga-ga.mean(),gd-gd.mean()]
    v=np.zeros(2+2*n);v[0]=math.log(max(ag.mean(),.1));v[1]=math.log(max(hg.mean(),.1)/max(ag.mean(),.1))
    fit=minimize(objective,v,jac=True,method='L-BFGS-B',bounds=[(-2,1.5),(-.8,.8)]+[(-1.5,1.5)]*(2*n),options={'maxiter':500,'ftol':1e-10})
    if not fit.success: raise ValueError('strength optimization failed: '+fit.message)
    v=fit.x;att=v[2:2+n]-v[2:2+n].mean();defn=v[2+n:]-v[2+n:].mean()
    return {'version':VERSION,'cohort':rows[0]['cohort'],'intercept':float(v[0]),'home_advantage':float(v[1]),
            'attack':dict(zip(teams,map(float,att))),'defence':dict(zip(teams,map(float,defn))),
            'half_life_days':half_life,'ridge':ridge,'train_rows':len(rows),'train_end':rows[-1]['date'],
            'observed_at':max(r['observed_at'] for r in rows),'dataset_hash':digest(rows),'nuisance':{},
            'context_coefficients':None,'context_status':'UNTRAINED_NO_VERIFIED_FEATURE_HISTORY',
            'champion_unchanged':'V21.5','formal_validation':False}

def rates(model,row):
    if model.get('method')=='v01_baseline':
        h=model['teams'].get(row['home'],{'attack':1.,'concede':1.});a=model['teams'].get(row['away'],{'attack':1.,'concede':1.})
        missing=[t for t in (row['home'],row['away']) if t not in model['teams']]
        return model['home_mean']*h['attack']*a['concede'],model['away_mean']*a['attack']*h['concede'],missing
    attack=model['attack'];defn=model['defence']
    missing=[t for t in (row['home'],row['away']) if t not in attack]
    base=model['intercept'];home=0 if row.get('neutral') else model['home_advantage']
    lh=math.exp(base+home+attack.get(row['home'],0)+defn.get(row['away'],0))
    la=math.exp(base+attack.get(row['away'],0)+defn.get(row['home'],0))
    if max(lh,la)>12: raise ValueError('rates outside supported range')
    return lh,la,missing

def fit_baseline(rows):
    """Same v0.1 estimator equations; date-order retrospective reconstruction only."""
    end=datetime.fromisoformat(rows[-1]['date']);weighted=[(r,2**(-(end-datetime.fromisoformat(r['date'])).days/180)) for r in rows]
    den=sum(w for _,w in weighted);hm=sum(r['home_goals']*w for r,w in weighted)/den;am=sum(r['away_goals']*w for r,w in weighted)/den
    teams={};base=(hm+am)/2
    for r,w in weighted:
        for team,gf,ga,ef,ea in [(r['home'],r['home_goals'],r['away_goals'],hm,am),(r['away'],r['away_goals'],r['home_goals'],am,hm)]:
            v=teams.setdefault(team,[0.,0.,0.,0.])
            for i,x in enumerate((gf,ga,ef,ea)):v[i]+=w*x
    return {'method':'v01_baseline','home_mean':hm,'away_mean':am,'teams':{t:{'attack':(v[0]+8*base)/(v[2]+8*base),'concede':(v[1]+8*base)/(v[3]+8*base)} for t,v in teams.items()},'nuisance':{'poisson':0.}}

def matrix(lh,la,family='poisson',nuisance=0.,temperature=1.):
    if family not in FAMILIES or not 0<lh<=12 or not 0<la<=12 or not .5<=temperature<=2:
        raise ValueError('invalid distribution parameters')
    grid=np.arange(CAP+1)
    if family=='bivariate':
        if not 0<=nuisance<=.5: raise ValueError('invalid shared fraction')
        shared=nuisance*min(lh,la)
        x=poisson.pmf(grid,lh-shared);y=poisson.pmf(grid,la-shared);z=poisson.pmf(grid,shared)
        m=np.zeros((CAP+1,CAP+1))
        for k in range(CAP+1):
            if k>shared+10 and z[k]<1e-16: break
            m[k:,k:]+=z[k]*np.outer(x[:CAP+1-k],y[:CAP+1-k])
    else:
        m=np.outer(poisson.pmf(grid,lh),poisson.pmf(grid,la))
        if family=='dixon_coles':
            rho=nuisance;tau=np.array([1-lh*la*rho,1+lh*rho,1+la*rho,1-rho])
            if np.min(tau)<=0: raise ValueError('invalid Dixon-Coles correction')
            m[0,0]*=tau[0];m[0,1]*=tau[1];m[1,0]*=tau[2];m[1,1]*=tau[3]
    captured=float(m.sum())
    if abs(1-captured)>1e-9: raise ValueError('distribution tail exceeds tolerance')
    if temperature!=1: m=np.exp(np.log(np.maximum(m,1e-300))/temperature)
    return m/m.sum()

def fit_nuisance(model,rows):
    values=[(*rates(model,r)[:2],r['home_goals'],r['away_goals']) for r in rows]
    lh,la,hg,ag=map(np.array,zip(*values))
    base=poisson.logpmf(hg,lh)+poisson.logpmf(ag,la)
    def dc(rho):
        t=np.ones(len(rows))
        for mask,correction in [((hg==0)&(ag==0),1-lh*la*rho),((hg==0)&(ag==1),1+lh*rho),((hg==1)&(ag==0),1+la*rho),((hg==1)&(ag==1),1-rho)]: t[mask]=correction[mask] if np.ndim(correction) else correction
        if np.min(t)<=0: return 1e20
        return float(-np.mean(base+np.log(t)))
    # Bounds guarantee positive corrections for every supported future rate <=12.
    rho=minimize_scalar(dc,bounds=(-.08,.006),method='bounded').x
    def biv(fraction):
        shared=fraction*np.minimum(lh,la);x=lh-shared;y=la-shared
        terms=[]
        for k in range(int(max(np.minimum(hg,ag)))+1):
            valid=(hg>=k)&(ag>=k)
            terms.append(np.where(valid,poisson.logpmf(hg-k,x)+poisson.logpmf(ag-k,y)+poisson.logpmf(k,shared),-np.inf))
        return float(-np.mean(logsumexp(np.array(terms),axis=0)))
    fraction=minimize_scalar(biv,bounds=(0,.5),method='bounded').x
    model['nuisance']={'poisson':0.,'dixon_coles':float(rho),'bivariate':float(fraction)}
    return model

def get_matrix(model,row,family,temperature=1.):
    lh,la,_=rates(model,row)
    return matrix(lh,la,family,model['nuisance'][family],temperature)

def calibrate(model,rows,family):
    logs=[np.log(np.maximum(get_matrix(model,r,family),1e-300)) for r in rows]
    def objective(t):
        return float(np.mean([-(m[r['home_goals'],r['away_goals']]/t-logsumexp(m/t)) for m,r in zip(logs,rows)]))
    fit=minimize_scalar(objective,bounds=(.7,1.5),method='bounded')
    return float(fit.x)

def partitions(rows):
    rows=validate_history(rows);dates=sorted({r['date'] for r in rows})
    if len(dates)<40: raise ValueError('insufficient distinct dates for four-way temporal split')
    boundaries=[dates[int(len(dates)*x)] for x in (.6,.7,.8)]
    blocks=[[],[],[],[]]
    for row in rows: blocks[sum(row['date']>=b for b in boundaries)].append(row)
    if min(map(len,blocks))<20: raise ValueError('insufficient temporal split samples')
    return blocks

def probabilities(m):
    return np.array([np.tril(m,-1).sum(),np.trace(m),np.triu(m,1).sum()])

def total_settlement(m,line,side):
    if line<0 or abs(line*4-round(line*4))>1e-8: raise ValueError('invalid quarter line')
    legs=[line] if line*2==int(line*2) else [line-.25,line+.25]
    goals=np.add.outer(np.arange(len(m)),np.arange(len(m)))
    net=sum(np.sign(goals-leg) for leg in legs)/len(legs)
    if side=='under': net=-net
    if side not in ('over','under'): raise ValueError('invalid side')
    return {label:float(m[net==value].sum()) for label,value in [('win',1),('half_win',.5),('push',0),('half_loss',-.5),('loss',-1)]}

def metrics(model,rows,family,temp):
    hits=wdhits=outhits=0;loss=[];wdloss=[];brier=[];oubrier=[];bins=[[] for _ in range(10)];unknown=0
    for row in rows:
        m=get_matrix(model,row,family,temp);h,a=row['home_goals'],row['away_goals'];mode=np.unravel_index(np.argmax(m),m.shape)
        hits+=mode==(h,a);loss.append(-math.log(max(float(m[h,a]),1e-300)))
        wd=probabilities(m);y=0 if h>a else 1 if h==a else 2;wdhits+=int(np.argmax(wd))==y
        wdloss.append(-math.log(max(float(wd[y]),1e-300)));brier.append(float(np.sum((wd-np.eye(3)[y])**2)))
        win=total_settlement(m,2.5,'over')['win'];target=int(h+a>2.5);outhits+=(win>.5)==bool(target);oubrier.append((win-target)**2)
        conf=float(max(wd));bins[min(int(conf*10),9)].append((conf,int(np.argmax(wd)==y)))
        unknown+=bool(rates(model,row)[2])
    calibration=[{'bin':i,'n':len(x),'mean_confidence':float(np.mean([t[0] for t in x])),'accuracy':float(np.mean([t[1] for t in x]))} for i,x in enumerate(bins) if x]
    return {'n':len(rows),'primary_hits':int(hits),'top1':hits/len(rows),'score_log_loss':float(np.mean(loss)),
            'wdl_hits':int(wdhits),'wdl_accuracy':wdhits/len(rows),'wdl_log_loss':float(np.mean(wdloss)),
            'wdl_brier':float(np.mean(brier)),'total_2_5_hits':int(outhits),'total_2_5_accuracy':outhits/len(rows),
            'total_2_5_brier':float(np.mean(oubrier)),'wdl_calibration_bins':calibration,'unseen_team_matches':unknown}

def research_predict(model,fixture,family,temp=1.):
    cutoff=aware(fixture['cutoff_at']);kickoff=aware(fixture['kickoff_at']);now=datetime.now(timezone.utc)
    if cutoff>now or cutoff>=kickoff or kickoff<=now: raise ValueError('only genuinely future prematch requests')
    if aware(model['observed_at'])>cutoff: raise ValueError('artifact contains later-observed data')
    if fixture['cohort']!=model['cohort']: raise ValueError('cohort mismatch')
    if fixture.get('phase')!='PreMatch': raise ValueError('phase must be PreMatch')
    sources=fixture.get('sources',[])
    if not sources: raise ValueError('fixture evidence required')
    for s in sources:
        if not s['source_url'].startswith('https://') or aware(s['query_time'])>cutoff: raise ValueError('invalid evidence')
    # Context effects must be trained separately; never invent injury multipliers.
    lh,la,missing=rates(model,fixture);adjustment=None
    if missing and fixture.get('accept_cold_start_prior') is not True:
        raise ValueError('unseen teams require explicit cold-start prior acceptance: '+','.join(missing))
    if fixture.get('context_features'):
        from evidence import context_rates
        context=model.get('context_coefficients') or {}
        if context.get('cohort')!=model['cohort'] or context.get('validated_family')!=family or context.get('validated_temperature')!=temp:
            raise ValueError('context validation not compatible with model cohort/distribution')
        if aware(context['observed_at'])>cutoff:raise ValueError('later context artifact')
        lh,la=context_rates(lh,la,fixture['context_features'],model.get('context_coefficients'),fixture.get('context_evidence',[]),fixture['cutoff_at'])
    m=matrix(lh,la,family,model['nuisance'][family],temp)
    if fixture.get('market_quote'):
        from evidence import market_predict
        m,adjustment=market_predict(m,fixture['market_quote'],fixture['cutoff_at'],model.get('market_policy'))
    primary=np.unravel_index(np.argmax(m),m.shape)
    return {'version':VERSION,'role':'Challenger','champion_unchanged':'V21.5','permanent_lambda_modified':False,
        'formal_validation':False,'freeze_status':'DRAFT_NOT_FROZEN','generated_at':now.isoformat(),
        'model_hash':digest(model),'input_hash':digest(fixture),'input':fixture,
        'family':family,'temperature':temp,'rates':[lh,la],'unseen_teams':missing,'market_adjustment':adjustment,
        'matrix_hash':digest(m.tolist()),'matrix':m.tolist(),'primary':f'{primary[0]}-{primary[1]}',
        'primary_probability':float(m[primary]),'wdl':dict(zip(['主胜','平局','客胜'],map(float,probabilities(m)))),
        'total_line':fixture['total_line'],'over':total_settlement(m,fixture['total_line'],'over'),
        'under':total_settlement(m,fixture['total_line'],'under')}
