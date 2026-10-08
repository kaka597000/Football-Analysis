"""Evidence-gated learned context and bounded market adjustments."""
import math
import numpy as np
from scipy.optimize import minimize
from model import aware, probabilities, matrix, total_settlement, digest

def evidence_before(evidence,cutoff,require_official=False):
    if not evidence or not evidence.get('source_url','').startswith('https://'):
        raise ValueError('source evidence required')
    if aware(evidence['query_time'])>aware(cutoff): raise ValueError('post-cutoff evidence')
    if require_official and evidence.get('source_tier')!='L1': raise ValueError('official lineup evidence required')

def fit_context(samples,feature_names,min_rows=100):
    """Residual goal-rate regression; only independently timestamped pre-match features."""
    if len(samples)<min_rows: raise ValueError('insufficient verified context feature history')
    x=[];yh=[];ya=[];bh=[];ba=[]
    for r in samples:
        if aware(r['cutoff_at'])>=aware(r['kickoff_at']):raise ValueError('context cutoff after kickoff')
        if aware(r['baseline_observed_at'])>aware(r['cutoff_at']):raise ValueError('baseline includes later data')
        if aware(r['result_observed_at'])<aware(r['kickoff_at']):raise ValueError('result observed before kickoff')
        for e in r['evidence']:evidence_before(e,r['cutoff_at'],True)
        if not r['evidence']:raise ValueError('missing context evidence')
        x.append([float(r['features'][f]) for f in feature_names]);yh.append(r['home_goals']);ya.append(r['away_goals']);bh.append(r['baseline_home']);ba.append(r['baseline_away'])
    x=np.asarray(x);mean=x.mean(axis=0);scale=x.std(axis=0);scale[scale<1e-8]=1.;x=(x-mean)/scale
    if not np.isfinite(x).all():raise ValueError('nonfinite features')
    coefs=[]
    for y,b in [(yh,bh),(ya,ba)]:
        y=np.asarray(y);b=np.asarray(b)
        if np.any(b<=0):raise ValueError('invalid baseline')
        def objective(beta):
            pred=b*np.exp(x@beta)
            return float(np.sum(pred-y*np.log(pred))+10*(beta@beta)),x.T@(pred-y)+20*beta
        fit=minimize(objective,np.zeros(len(feature_names)),jac=True,method='L-BFGS-B',bounds=[(-.3,.3)]*len(feature_names))
        if not fit.success:raise ValueError('context fit failed')
        coefs.append(fit.x.tolist())
    cohorts={r['cohort'] for r in samples}
    if len(cohorts)!=1:raise ValueError('context cohorts must not mix')
    return {'features':feature_names,'mean':mean.tolist(),'scale':scale.tolist(),'coefficients':coefs,'n':len(samples),
            'cohort':next(iter(cohorts)),'observed_at':max(r['result_observed_at'] for r in samples),
            'status':'FITTED_REQUIRES_TEMPORAL_VALIDATION','validated':False,'dataset_hash':digest(samples)}

def validate_context(samples,feature_names,min_rows=200):
    """Fixed earlier 70% dates fit, remaining 30% validate; no feature selection."""
    if len(samples)<min_rows:raise ValueError('insufficient independently verifiable context samples')
    rows=sorted(samples,key=lambda r:r['kickoff_at']);dates=sorted({r['kickoff_at'][:10] for r in rows})
    if len(dates)<20:raise ValueError('insufficient context dates')
    boundary=dates[int(.7*len(dates))]
    train=[r for r in rows if r['kickoff_at'][:10]<boundary];test=[r for r in rows if r['kickoff_at'][:10]>=boundary]
    artifact=fit_context(train,feature_names)
    # Fit-time observations must genuinely precede the validation cutoffs.
    if any(aware(artifact['observed_at'])>aware(r['cutoff_at']) for r in test):
        raise ValueError('context artifact not available before validation matches')
    counts={'baseline_top1':0,'candidate_top1':0,'baseline_wdl':0,'candidate_wdl':0,'baseline_ou':0,'candidate_ou':0}
    losses={'baseline':0.,'candidate':0.}
    for r in test:
        if aware(r['cutoff_at'])>=aware(r['kickoff_at']):raise ValueError('validation context after kickoff')
        for e in r['evidence']:evidence_before(e,r['cutoff_at'],True)
        if not r['evidence'] or aware(r['baseline_observed_at'])>aware(r['cutoff_at']):raise ValueError('invalid validation context')
        x=(np.array([r['features'][f] for f in feature_names])-artifact['mean'])/artifact['scale']
        shifts=np.clip(np.asarray(artifact['coefficients'])@x,-.2,.2)
        h,a=r['home_goals'],r['away_goals'];wd=0 if h>a else 1 if h==a else 2
        for label,m in [('baseline',matrix(r['baseline_home'],r['baseline_away'])),
                        ('candidate',matrix(r['baseline_home']*math.exp(shifts[0]),r['baseline_away']*math.exp(shifts[1])))]:
            counts[label+'_top1']+=np.unravel_index(np.argmax(m),m.shape)==(h,a)
            counts[label+'_wdl']+=int(np.argmax(probabilities(m)))==wd
            counts[label+'_ou']+=(total_settlement(m,2.5,'over')['win']>.5)==(h+a>2.5)
            losses[label]-=math.log(max(float(m[h,a]),1e-300))
    counts={k:int(v) for k,v in counts.items()}
    passed=counts['candidate_top1']>counts['baseline_top1'] and losses['candidate']<losses['baseline'] and counts['candidate_wdl']>=counts['baseline_wdl'] and counts['candidate_ou']>=counts['baseline_ou']
    artifact.update({'validated':bool(passed),'validated_family':'poisson','validated_temperature':1.,
                     'validation':{'n':len(test),'counts':counts,'loss_sums':losses,'start_date':boundary},
                     'observed_at':max(r['result_observed_at'] for r in rows),
                     'status':'RESEARCH_VALIDATED' if passed else 'VALIDATION_FAILED_DO_NOT_ENABLE'})
    return artifact

def context_rates(lh,la,features,artifact,evidence,cutoff):
    if not artifact or artifact.get('validated') is not True:raise ValueError('context artifact not independently validated')
    for e in evidence:evidence_before(e,cutoff,True)
    if not evidence:raise ValueError('context evidence required')
    v=np.array([features[k] for k in artifact['features']]);x=(v-artifact['mean'])/artifact['scale']
    if not np.isfinite(x).all():raise ValueError('invalid context features')
    shifts=np.clip(np.asarray(artifact['coefficients'])@x,-.2,.2)
    return lh*math.exp(shifts[0]),la*math.exp(shifts[1])

def no_vig(decimal_odds):
    x=np.asarray(decimal_odds,dtype=float)
    if not np.isfinite(x).all() or np.any(x<=1):raise ValueError('invalid decimal odds')
    p=1/x
    return p/p.sum()

def bounded_market(m,target,alpha,max_kl=.02):
    """One X Two tilt, bounded by KL divergence; no direct bookmaker mode replacement."""
    if not 0<=alpha<=.2 or not 0<max_kl<=.02:raise ValueError('unbounded market adjustment')
    target=np.asarray(target,dtype=float)
    if target.shape!=(3,) or np.any(target<=0) or abs(target.sum()-1)>1e-8:raise ValueError('invalid market target')
    home=np.subtract.outer(np.arange(len(m)),np.arange(len(m)))
    region=np.where(home>0,0,np.where(home==0,1,2));base=probabilities(m)
    for _ in range(40):
        tilt=(target/base)**alpha;candidate=m*tilt[region];candidate/=candidate.sum()
        kl=float(np.sum(candidate*np.log(np.maximum(candidate,1e-300)/np.maximum(m,1e-300))))
        if kl<=max_kl:return candidate,{'alpha':alpha,'kl':kl,'primary_changed':np.argmax(candidate)!=np.argmax(m)}
        alpha/=2
    return m,{'alpha':0.,'kl':0.,'primary_changed':False}

def market_predict(m,quote,cutoff,validated_policy):
    evidence_before(quote,cutoff)
    if not validated_policy or not validated_policy.get('validated'):
        raise ValueError('market policy lacks independent temporal validation')
    if quote.get('odds_timestamp_verified') is not True:raise ValueError('market source timestamp not verified')
    if aware(quote['quoted_at'])>aware(cutoff):raise ValueError('post-cutoff quote')
    return bounded_market(m,no_vig(quote['decimal_1x2']),validated_policy['alpha'])
