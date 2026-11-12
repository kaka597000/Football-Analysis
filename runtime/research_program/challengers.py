"""Dynamic team strength and score-distribution challengers; isolated development."""
import copy
from datetime import date,datetime,timezone
from itertools import groupby
import math
import numpy as np
from scipy.stats import poisson,nbinom
from competition_scope import training_rows
from v2_test.evaluation import evaluate
from v2_test.data_contract import digest

CONFIG={'learning_rate':.025,'shrink_days':365.,'season_retention':.5,'max_log_strength':1.5,
        'minimum_rows':120,'max_goals':80}
FAMILIES=[('poisson',None),('bivariate',.15),('bivariate',.3),('negative_binomial',2.),
          ('negative_binomial',5.),('negative_binomial',10.),('tempo_mixture',None)]


def prepare(rows):
    rows=copy.deepcopy(rows)
    # Only unphased registered regular leagues may inherit their baseline identity.
    for r in rows:
        if not r.get('stage') and r['cohort'] in {'eng.1','esp.1','ger.1','ita.1','fra.1','swe.1','nor.1'}:
            r['stage']='regular';r['stage_origin']='REGISTERED_REGULAR_LEAGUE_BASELINE'
    training_rows(rows)
    seen=set()
    for r in rows:
        if r['match_id'] in seen:raise ValueError('duplicate match')
        seen.add(r['match_id']);date.fromisoformat(r['date'])
        if r.get('score_basis')!='regulation_90' or r['home']==r['away'] or type(r.get('neutral')) is not bool:
            raise ValueError('verified regulation identity and venue required')
        if any(type(r.get(k)) is not int or not 0<=r[k]<=40 for k in ('home_goals','away_goals')):
            raise ValueError('invalid score')
    return sorted(rows,key=lambda r:(r['date'],r['match_id']))


def _team(model,team,day,season):
    state=copy.deepcopy(model['teams'].get(team,{'attack':0.,'defence':0.,'date':day,'season':season}))
    gap=(date.fromisoformat(day)-date.fromisoformat(state['date'])).days
    if gap<0:raise ValueError('target before strength state')
    retain=math.exp(-gap/model['config']['shrink_days'])
    if season is not None and state['season'] is not None and season!=state['season']:
        retain*=model['config']['season_retention']
    for k in ('attack','defence'):state[k]*=retain
    state.update(date=day,season=season)
    return state


def rates(model,fixture):
    h=_team(model,fixture['home'],fixture['date'],fixture.get('season'))
    a=_team(model,fixture['away'],fixture['date'],fixture.get('season'))
    home=model['home_advantage'] if not fixture['neutral'] else 0.
    return np.clip(np.exp([model['intercept']+home+h['attack']+a['defence'],
                           model['intercept']+a['attack']+h['defence']]),.05,8.)


def _config(value=None):
    config=copy.deepcopy(CONFIG if value is None else value)
    for key in ('learning_rate','shrink_days','max_log_strength'):
        if type(config[key]) not in (int,float) or not math.isfinite(config[key]) or config[key]<=0:
            raise ValueError('positive finite dynamic configuration required')
    if type(config['season_retention']) not in (int,float) or not 0<=config['season_retention']<=1:
        raise ValueError('season retention must be between zero and one')
    if any(type(config[k]) is not int or config[k]<1 for k in ('minimum_rows','max_goals')):
        raise ValueError('positive integer sample/support configuration required')
    return config


def fit(rows,config=None):
    config=_config(config)
    rows=prepare(rows)
    if not rows:raise ValueError('history required')
    model={'schema':'WCPP-DYNAMIC-STRENGTH-1','cohort':rows[0]['cohort'],'stage':rows[0]['stage'],
           'intercept':math.log(1.2),'home_advantage':math.log(1.2),'teams':{},'config':config}
    # Predict all same-day games before any label updates on that day.
    for day,group in groupby(rows,key=lambda r:r['date']):
        block=list(group);updates={};global_res=[];home_res=[]
        for row in block:
            mu=rates(model,row);res=np.array([row['home_goals'],row['away_goals']])-mu
            for team,own,opponent in ((row['home'],res[0],res[1]),(row['away'],res[1],res[0])):
                state=_team(model,team,day,row.get('season'))
                if team in updates:raise ValueError('same team has ambiguous same-day duplicate games')
                state['attack']=float(np.clip(state['attack']+config['learning_rate']*own,-config['max_log_strength'],config['max_log_strength']))
                state['defence']=float(np.clip(state['defence']+config['learning_rate']*opponent,-config['max_log_strength'],config['max_log_strength']))
                updates[team]=state
            global_res.extend(res)
            if not row['neutral']:home_res.append(float(res[0]))
        model['teams'].update(updates)
        model['intercept']=float(np.clip(model['intercept']+.01*np.mean(global_res),math.log(.4),math.log(3.)))
        if home_res:model['home_advantage']=float(np.clip(model['home_advantage']+.01*np.mean(home_res),-.4,.6))
    model.update(training_end=rows[-1]['date'],training_n=len(rows),training_ids_sha256=digest([r['match_id'] for r in rows]))
    return model


def distribution(mu,family='poisson',parameter=None,*,max_goals=80):
    mu=np.asarray(mu,float)
    if mu.shape!=(2,) or not np.isfinite(mu).all() or (mu<=0).any() or (mu>8).any():raise ValueError('valid fitted means required')
    if type(max_goals) is not int or max_goals<1:raise ValueError('positive integer support required')
    x=np.arange(max_goals+1)
    if family=='poisson':m=np.outer(poisson.pmf(x,mu[0]),poisson.pmf(x,mu[1]))
    elif family=='negative_binomial':
        if parameter is None or parameter<=0:raise ValueError('positive dispersion required')
        m=np.outer(nbinom.pmf(x,parameter,parameter/(parameter+mu[0])),nbinom.pmf(x,parameter,parameter/(parameter+mu[1])))
    elif family=='bivariate':
        if parameter is None or not 0<=parameter<1:raise ValueError('common-intensity fraction required')
        common=min(mu)*parameter;m=np.zeros((len(x),len(x)))
        h,a=poisson.pmf(x,mu[0]-common),poisson.pmf(x,mu[1]-common)
        for k,p in enumerate(poisson.pmf(x,common)):
            m[k:,k:]+=p*np.outer(h[:len(x)-k],a[:len(x)-k])
    elif family=='tempo_mixture':
        factors=np.array([.6,1.,1.5]);weights=np.array([.25,.5,.25]);factors/=factors@weights
        m=sum(w*np.outer(poisson.pmf(x,mu[0]*t),poisson.pmf(x,mu[1]*t)) for w,t in zip(weights,factors))
    else:raise ValueError('unknown distribution family')
    mass=float(m.sum())
    if not mass>0 or 1-mass>1e-5:raise ValueError('unsupported tail truncation')
    return m/mass,{'family':family,'parameter':parameter,'omitted_tail_mass':max(0.,1-mass)}


def _evaluate(model,rows,family,parameter):
    return evaluate([{'matrix':distribution(rates(model,r),family,parameter,max_goals=model['config']['max_goals'])[0],
                      'home_goals':r['home_goals'],'away_goals':r['away_goals']} for r in rows])


def experiment(rows,config=None):
    config=_config(config)
    rows=prepare(rows)
    if len(rows)<config['minimum_rows']:raise ValueError('insufficient independent temporal groups')
    days=sorted({r['date'] for r in rows})
    if len(days)<12:raise ValueError('insufficient distinct dates')
    c1,c2=days[len(days)//2],days[3*len(days)//4]
    train=[r for r in rows if r['date']<c1];cal=[r for r in rows if c1<=r['date']<c2];test=[r for r in rows if r['date']>=c2]
    if min(map(len,(train,cal,test)))<20:raise ValueError('complete fit/cal/test denominators required')
    initial=fit(train,config);candidates=[]
    for family,param in FAMILIES:
        metrics=_evaluate(initial,cal,family,param)
        candidates.append({'family':family,'parameter':param,'calibration':metrics})
    baseline=candidates[0]['calibration']['metrics']
    def admissible(c):
        m=c['calibration']['metrics']
        return (m['exact_top1']>=baseline['exact_top1'] and m['score_log_loss']<=baseline['score_log_loss']
                and m['score_brier']<=baseline['score_brier'] and m['wdl_brier']<=baseline['wdl_brier'])
    selected=min([c for c in candidates if admissible(c)],key=lambda c:(-c['calibration']['metrics']['exact_top1'],c['calibration']['metrics']['score_log_loss']))
    diagnostic_model=fit(train+cal,config)
    result={'schema':'WCPP-DYNAMIC-DISTRIBUTION-EXPERIMENT-1','status':'DEVELOPMENT_ONLY_NOT_PROMOTED',
            'cohort':rows[0]['cohort'],'stage':rows[0]['stage'],'generated_at':datetime.now(timezone.utc).isoformat(),
            'split':{'calibration_start':c1,'diagnostic_start':c2,'fit_n':len(train),'cal_n':len(cal),'diagnostic_n':len(test)},
            'candidates':candidates,'selected':selected,'diagnostic_model':diagnostic_model,
            'diagnostic':_evaluate(diagnostic_model,test,selected['family'],selected['parameter']),
            'dynamic_poisson_diagnostic':_evaluate(diagnostic_model,test,'poisson',None),
            'claims':{'historical_asof_certified':False,'prospective_unseen_validation':False,
                      'compared_with_actual_Current':False,'active_model_changed':False,'accuracy_improvement_proven':False},
            'limitations':['Opponent-adjusted online Poisson gradient baseline, not fitted Bayesian posterior',
                           'Missing season/coach changes are not inferred or given invented event weights',
                           'Historical capture gaps prevent certified as-of backtesting',
                           'Calibration selects family before diagnostic labels; diagnostic model excludes test labels',
                           'Current same-match prospective comparator still required']}
    return result
