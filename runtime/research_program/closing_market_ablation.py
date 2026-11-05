"""Independent, retrospective closing-price reconstruction; never an as-of certificate."""
from __future__ import annotations
import argparse, csv, hashlib, json, math
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
import numpy as np
from scipy.optimize import minimize
from scipy.stats import poisson, skellam

PROTOCOL = dict(name='ROLE_SYMMETRIC_NO_EXPLICIT_VENUE_FEATURE_RESEARCH', version=1,
    leagues={'E0':'eng.1','SP1':'esp.1','D1':'ger.1','I1':'ita.1','F1':'fra.1'},
    seasons=['1920','2021','2122','2223','2324','2425'], holdout='2425', excluded=['2526'],
    history_window=10, minimum_history=3, minimum_train_matches=200,
    ridge_alpha=.1, bootstrap_seed=20261105, bootstrap_repetitions=2000,
    baseline=['ownGF','ownGA','oppGF','oppGA'],
    treatment=['log_ownWin_over_oppWin','log_draw','logit_over2.5'],
    quote_columns=['PSCH','PSCD','PSCA','PC>2.5','PC<2.5'],
    asof_certified=False, venue_certified=False, active_model_comparison=False,
    formal_primary=False, top1_tie='lower-mode', quoted_at=None, first_known_at=None)

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def parse_date(value):
    for fmt in ('%d/%m/%Y','%d/%m/%y','%Y-%m-%d'):
        try: return datetime.strptime(value,fmt).date().isoformat()
        except ValueError: pass
    raise ValueError('Unsupported date: '+value)

def closing(row):
    try:
        prices=np.array([float(row[k]) for k in PROTOCOL['quote_columns']])
        if not np.isfinite(prices).all() or (prices<=1).any(): return None
    except (KeyError,ValueError,TypeError): return None
    w=1/prices[:3]; w/=w.sum(); o=1/prices[3:]; o/=o.sum()
    return [*w,float(o[0])]

def load_inputs(manifest,audit=None):
    rows=[]
    for source in manifest['sources']:
        if source['season'] not in PROTOCOL['seasons']: continue
        league=source['league']
        if league not in PROTOCOL['leagues']: raise ValueError('Unknown league')
        path=Path(source['path']); raw=path.read_bytes(); sha=hashlib.sha256(raw).hexdigest()
        if source.get('sha256') and source['sha256']!=sha: raise ValueError('Input hash mismatch')
        for line,r in enumerate(csv.DictReader(raw.decode('utf-8-sig').splitlines()),2):
            if not r.get('FTHG') or not r.get('FTAG'):
                if audit is not None: audit.append(dict(league=league,season=source['season'],row=line,raw_sha256=sha,reason='missing_label'))
                continue
            if r.get('Div')!=league: raise ValueError('Source division mismatch')
            d=parse_date(r['Date'])
            start=2000+int(source['season'][:2]); year=int(d[:4])
            if year not in (start,start+1): raise ValueError('Date outside source season')
            identity=[league,d,r['HomeTeam'],r['AwayTeam']]
            rows.append(dict(id=digest(identity),league=league,namespace=PROTOCOL['leagues'][league],season=source['season'],date=d,
                home=r['HomeTeam'],away=r['AwayTeam'],hg=int(r['FTHG']),ag=int(r['FTAG']),market=closing(r),
                neutral=r.get('neutral') or None, venue_status='UNKNOWN', quoted_at=None,first_known_at=None,
                source=dict(raw_sha256=sha,url=source.get('url'),fetch_time=source.get('fetch_time'),row=line,time_raw=r.get('Time'))))
    ids=[r['id'] for r in rows]
    if len(ids)!=len(set(ids)): raise ValueError('Duplicate match identity')
    return rows

def features(rows):
    history=defaultdict(lambda:deque(maxlen=10)); output=[]
    groups=defaultdict(list)
    for r in rows: groups[(r['league'],r['date'])].append(r)
    for key,block in sorted(groups.items()):
        for r in block:
            a,b=history[(key[0],r['home'])],history[(key[0],r['away'])]
            if min(len(a),len(b))>=3:
                am,bm=np.mean(a,axis=0),np.mean(b,axis=0)
                output.append(dict(r,x=[*am,*bm]))
        for r in block:
            history[(key[0],r['home'])].append((r['hg'],r['ag']))
            history[(key[0],r['away'])].append((r['ag'],r['hg']))
    return output

def role_x(r,treatment=False):
    a=np.array(r['x'],float); b=a[[2,3,0,1]]
    if treatment:
        h,d,v,o=r['market']; m=[math.log(h/v),math.log(d),math.log(o/(1-o))]
        a=np.r_[a,m]; b=np.r_[b,[-m[0],m[1],m[2]]]
    return np.array([a,b])

def objective(theta,x,y,alpha=.1):
    eta=theta[0]+x@theta[1:]; lam=np.exp(eta)
    loss=np.mean(lam-y*eta)+alpha/2*np.dot(theta[1:],theta[1:])
    residual=(lam-y)/len(y)
    return loss,np.r_[residual.sum(),x.T@residual+alpha*theta[1:]]

def fit(rows,treatment=False):
    x=np.concatenate([role_x(r,treatment) for r in rows]); y=np.array([[r['hg'],r['ag']] for r in rows]).ravel()
    mean=x.mean(0); scale=x.std(0); scale[scale==0]=1; z=(x-mean)/scale
    # Bound linear predictors through coefficient bounds, without clipping eta or its gradient.
    bound=20/(1+np.max(np.abs(z),axis=0).sum())
    result=minimize(objective,np.r_[math.log(max(y.mean(),.01)),np.zeros(x.shape[1])],args=(z,y),jac=True,
        method='L-BFGS-B',bounds=[(-10,10)]+[(-bound,bound)]*x.shape[1],options={'maxiter':2000,'ftol':1e-12,'gtol':1e-8})
    if not result.success or not np.isfinite(result.fun): raise RuntimeError('GLM did not converge: '+result.message)
    return dict(coef=result.x.tolist(),mean=mean.tolist(),scale=scale.tolist(),treatment=treatment,
        train_id_hash=digest(sorted(r['id'] for r in rows)),train_matches=len(rows),training_end=max(r.get('date','') for r in rows),feature_names=PROTOCOL['baseline']+(PROTOCOL['treatment'] if treatment else []),optimizer=str(result.message))

def predict(model,row):
    z=(role_x(row,model['treatment'])-model['mean'])/model['scale']
    return np.exp(model['coef'][0]+z@np.array(model['coef'][1:]))

def lower_mode(lam): return max(0,int(math.ceil(float(lam)))-1)

def probabilities(lam):
    h,a=map(float,lam); draw=float(skellam.pmf(0,h,a)); away=float(skellam.cdf(-1,h,a)); home=1-draw-away
    return np.array([home,draw,away]),float(poisson.sf(2,h+a))

def metrics(row,lam):
    h,a=row['hg'],row['ag']; w,o=probabilities(lam); actual=0 if h>a else 1 if h==a else 2; target=np.eye(3)[actual]
    return dict(top1=float([lower_mode(v) for v in lam]==[h,a]),score_logloss=float(-poisson.logpmf(h,lam[0])-poisson.logpmf(a,lam[1])),
        wdl_acc=float(w.argmax()==actual),wdl_brier=float(np.square(w-target).sum()),wdl_logloss=float(-math.log(max(w[actual],1e-300))),
        wdl_rps=float(np.square(np.cumsum(w)[:2]-np.cumsum(target)[:2]).sum()/2),
        ou_acc=float((o>.5)==(h+a>2)),ou_brier=float((o-(h+a>2))**2),
        tail4_probability=float(poisson.sf(3,sum(lam))),tail4_actual=float(h+a>=4),tail4_brier=float((poisson.sf(3,sum(lam))-(h+a>=4))**2))

def summarize(predictions):
    groups=defaultdict(list)
    for r in predictions:
        for k in ('all','league:'+r['league'],'season:'+r['season'],'league-season:'+r['league']+':'+r['season']): groups[k].append(r)
    result={}
    for name,rs in groups.items():
        blocks=defaultdict(list)
        for r in rs: blocks[(r['league'],r['date'])].append(r['treatment']['top1']-r['baseline']['top1'])
        vals=list(blocks.values()); rng=np.random.default_rng(PROTOCOL['bootstrap_seed']); draws=[]
        for _ in range(PROTOCOL['bootstrap_repetitions']):
            selected=[vals[i] for i in rng.integers(0,len(vals),len(vals))]; draws.append(sum(map(sum,selected))/sum(map(len,selected)))
        result[name]={'n':len(rs),'holdout':True,'top1_net_hits':sum(sum(v) for v in vals),
            'baseline_top1_hits':sum(r['baseline']['top1'] for r in rs), 'treatment_top1_hits':sum(r['treatment']['top1'] for r in rs),
            'changed_correct':sum(r['treatment']['top1']>r['baseline']['top1'] for r in rs),
            'changed_wrong':sum(r['treatment']['top1']<r['baseline']['top1'] for r in rs),
            'top1_net_rate_ci95':np.quantile(draws,[.025,.975]).tolist(),
            **{m:{k:float(np.mean([r[m][k] for r in rs])) for k in rs[0][m]} for m in ('baseline','treatment','market_untrained')}}
    return result

def adapt_manifest(manifest_path,data_root=None):
    manifest=json.loads(Path(manifest_path).read_text())
    if isinstance(manifest,dict): return manifest
    root=Path(data_root) if data_root else Path(manifest_path).parent.parent
    sources=[]
    for entry in manifest:
        stem=Path(entry.get('raw_file','')).stem
        parts=stem.split('_')
        if len(parts)!=2 or parts[0] not in PROTOCOL['seasons'] or parts[1] not in PROTOCOL['leagues']: continue
        sources.append(dict(league=parts[1],season=parts[0],path=str(root/entry['raw_file']),sha256=entry['sha256'],url=entry['url'],fetch_time=entry['retrieved_at_utc']))
    if len(sources)!=30: raise ValueError('Expected all thirty league-season sources')
    return dict(sources=sources)

def month_split(eligible,month):
    return ([r for r in eligible if r['date'][:7]<month],
            [r for r in eligible if r['date'][:7]==month and r['season']=='2425'])

def run(manifest_path,out,data_root=None):
    manifest=adapt_manifest(manifest_path,data_root); label_exclusions=[]; rows=load_inputs(manifest,label_exclusions)
    out=Path(out); out.mkdir(parents=True,exist_ok=False)
    protocol=dict(PROTOCOL,created_at_utc=datetime.now().isoformat()+'Z',input_manifest_sha256=hashlib.sha256(Path(manifest_path).read_bytes()).hexdigest(),audit_revision=2,same_fixed_recipe_as='run-001',input_manifest=manifest,input_hashes=sorted({r['source']['raw_sha256'] for r in rows}),protocol_written_before_fit=True)
    (out/'protocol.json').write_text(json.dumps(protocol,indent=2))
    fs=features(rows); predictions=[]; artifacts=[]
    featured={r['id'] for r in fs}
    exclusions=label_exclusions+[dict(id=r['id'],league=r['league'],season=r['season'],date=r['date'],reason='insufficient_history' if r['id'] not in featured else 'missing_or_invalid_closing_quote',source=r['source']) for r in rows if r['id'] not in featured or r['market'] is None]
    (out/'row_exclusions.json').write_text(json.dumps(exclusions,indent=2))
    for league in PROTOCOL['leagues']:
        eligible=[r for r in fs if r['league']==league and r['market'] is not None]
        for month in sorted({r['date'][:7] for r in eligible if r['season']=='2425'}):
            train,test=month_split(eligible,month)
            assert all(r['date'][:7]<month for r in train)
            assert all(r['season']=='2425' for r in test)
            if len(train)<200:
                exclusions.extend(dict(id=r['id'],reason='training_warmup',league=league,date=r['date']) for r in test)
                continue
            models=[fit(train,t) for t in (False,True)]; artifacts.append(dict(league=league,month=month,models=models))
            for r in test:
                lambdas=[predict(m,r).tolist() for m in models]; w=np.array(r['market'][:3]); o=r['market'][3]; actual=0 if r['hg']>r['ag'] else 1 if r['hg']==r['ag'] else 2; target=np.eye(3)[actual]
                predictions.append(dict(r,lambdas=lambdas,baseline=metrics(r,lambdas[0]),treatment=metrics(r,lambdas[1]),
                    market_untrained=dict(wdl_acc=float(w.argmax()==actual),wdl_brier=float(((w-target)**2).sum()),wdl_logloss=float(-math.log(w[actual])),wdl_rps=float(((w.cumsum()[:2]-target.cumsum()[:2])**2).sum()/2),ou_acc=float((o>.5)==(r['hg']+r['ag']>2)),ou_brier=float((o-(r['hg']+r['ag']>2))**2))))
    (out/'row_exclusions.json').write_text(json.dumps(exclusions,indent=2))
    coverage={}
    predicted={r['id'] for r in predictions}
    for league in PROTOCOL['leagues']:
        for season in PROTOCOL['seasons']:
            rs=[r for r in rows if r['league']==league and r['season']==season]
            coverage[league+':'+season]=dict(raw=len(rs)+sum(r.get('league')==league and r.get('season')==season for r in label_exclusions),labeled=len(rs),complete_quotes=sum(r['market'] is not None for r in rs),features=sum(r['id'] in featured for r in rs),paired_features=sum(r['id'] in featured and r['market'] is not None for r in rs),test_predicted=sum(r['id'] in predicted for r in rs),unknown_neutral=sum(r['neutral'] is None for r in rs))
    for name,obj in [('models',artifacts),('predictions',predictions),('report',dict(summary=summarize(predictions),coverage_by_league_season=coverage,coverage=dict(raw=len(rows),history_features=len(fs),complete_quotes=sum(r['market'] is not None for r in fs),no_quotes=sum(r['market'] is None for r in fs))))]:
        (out/(name+'.json')).write_text(json.dumps(obj,indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--data-root'); parser.add_argument('--manifest',required=True); parser.add_argument('--out',required=True); args=parser.parse_args(); run(args.manifest,args.out,args.data_root)
