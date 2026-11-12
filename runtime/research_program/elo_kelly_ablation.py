"""Fixed-protocol retained-data Elo/Kelly ablation. INACTIVE and retrospective only.

All arms see identical matches. Market transforms are not new independent data.
A prior research season is a diagnostic segment, not a pristine unseen holdout.
"""
from __future__ import annotations
import argparse
from collections import defaultdict, deque
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import numpy as np
from scipy.optimize import minimize
from research_program.closing_market_ablation import parse_date, digest, metrics, objective
from research_program.elo_kelly_shadow import expectation, devig

ARMS = ('goals', 'goals_elo', 'goals_market', 'goals_market_kelly', 'goals_market_kelly_elo')
PROTOCOL = {
 'version': 'WCPP-ELO-KELLY-ABLATION-2',
 'leagues': {'E0':'eng.1','D1':'ger.1','SP1':'esp.1','I1':'ita.1','F1':'fra.1'},
 'seasons': ['1920','2021','2122','2223','2324','2425'], 'diagnostic_season':'2425',
 'excluded_seasons':['2526'], 'initial_rating':1500., 'k_factor':20., 'rating_scale':400.,
 'home_advantage':0., 'history_window':10, 'minimum_history':3, 'minimum_train':200,
 'ridge_alpha':.1, 'coefficient_bound':1., 'winsor_quantiles':[.005,.995], 'max_standardized_feature':5.,
 'refit':'UTC month boundary; expanding dates strictly before boundary',
 'target_bookmaker':'Bet365', 'reference_bookmakers':['Pinnacle','William Hill'],
 'book_columns':[['B365CH','B365CD','B365CA'],['PSCH','PSCD','PSCA'],['WHCH','WHCD','WHCA']],
 'total_columns':['PC>2.5','PC<2.5'], 'asof_certified':False, 'quoted_at':None,
 'venue_verified':False, 'explicit_venue_feature':False, 'active_comparator':False,
 'statistical_top1_not_comprehensive_primary':True, 'preexisting_research_season_not_pristine_holdout':True,
 'hyperparameter_search':False, 'promotion':False, 'active_modified':False,
}


def write(path, value):
    with Path(path).open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write('\n')


def market(row):
    try:
        books=[]; payouts=[]
        for columns in PROTOCOL['book_columns']:
            odds={s:float(row[c]) for s,c in zip(('home','draw','away'),columns)}
            p,payout=devig(odds); books.append([p['home'],p['draw'],p['away']]);payouts.append(payout)
        total=np.array([float(row[c]) for c in PROTOCOL['total_columns']])
        if not np.isfinite(total).all() or (total<=1).any():return None
        over=float((1/total[0])/(1/total).sum())
    except (KeyError,TypeError,ValueError):return None
    peers=np.mean(books[1:],axis=0); target=np.array([float(row[c]) for c in PROTOCOL['book_columns'][0]])
    kelly=(target*peers/payouts[0]-1).tolist()
    return {'books':books,'payouts':payouts,'over25':over,'kelly_relative':kelly}


def load(manifest_path):
    manifest=json.loads(Path(manifest_path).read_text(encoding='utf-8'));rows=[];audit=[];seen=set()
    for source in manifest['sources']:
        if source['season'] not in PROTOCOL['seasons']:continue
        league=source['league']
        if league not in PROTOCOL['leagues']:raise ValueError('unsupported league')
        raw=Path(source['path']).read_bytes();sha=hashlib.sha256(raw).hexdigest()
        if sha!=source['sha256']:raise ValueError('retained source checksum mismatch')
        for line,r in enumerate(csv.DictReader(raw.decode('utf-8-sig').splitlines()),2):
            if not r.get('FTHG') or not r.get('FTAG'):
                audit.append({'source':sha,'line':line,'reason':'missing_label'});continue
            if r['Div']!=league:raise ValueError('source league conflict')
            day=parse_date(r['Date']); year=int(day[:4]); start=2000+int(source['season'][:2])
            if year not in (start,start+1):raise ValueError('season/date mismatch')
            h,a=int(r['FTHG']),int(r['FTAG'])
            if min(h,a)<0 or r['HomeTeam']==r['AwayTeam']:raise ValueError('invalid fixture')
            mid=digest([league,day,r['HomeTeam'],r['AwayTeam']])
            if mid in seen:raise ValueError('duplicate fixture')
            seen.add(mid)
            rows.append({'id':mid,'league':league,'season':source['season'],'date':day,
                'home':r['HomeTeam'],'away':r['AwayTeam'],'hg':h,'ag':a,'market':market(r),
                'source_sha256':sha,'source_line':line,'captured_at':source.get('fetch_time'),
                'quoted_at':None,'first_known_at':None,'neutral':None})
    return rows,audit


def feature_rows(rows):
    history=defaultdict(lambda:deque(maxlen=PROTOCOL['history_window']));ratings={};groups=defaultdict(list);out=[]
    for r in rows:groups[(r['league'],r['date'])].append(r)
    for (league,day),block in sorted(groups.items()):
        changes={}
        for r in sorted(block,key=lambda v:v['id']):
            hk,ak=(league,r['home']),(league,r['away'])
            rh,ra=ratings.get(hk,PROTOCOL['initial_rating']),ratings.get(ak,PROTOCOL['initial_rating'])
            gap=(rh-ra)/PROTOCOL['rating_scale']
            if min(len(history[hk]),len(history[ak]))>=PROTOCOL['minimum_history']:
                means=[np.mean(history[k],axis=0).tolist() for k in (hk,ak)]
                out.append(r | {'goal_features':means,'elo_gap':gap,'cold_start':hk not in ratings or ak not in ratings})
            s=1 if r['hg']>r['ag'] else .5 if r['hg']==r['ag'] else 0
            delta=PROTOCOL['k_factor']*(s-expectation(rh-ra,PROTOCOL['rating_scale']))
            if hk in changes or ak in changes:raise ValueError('same team multiple same-day games')
            changes[hk]=rh+delta;changes[ak]=ra-delta
        ratings.update(changes)
        for r in block:
            history[(league,r['home'])].append((r['hg'],r['ag']))
            history[(league,r['away'])].append((r['ag'],r['hg']))
    return out


def design(row,arm):
    if arm not in ARMS:raise ValueError('unsupported arm')
    values=[]
    for own,opp in ((0,1),(1,0)):
        x=[*row['goal_features'][own],*row['goal_features'][opp]]
        if 'market' in arm:
            m=row['market'];i,j=(0,2) if own==0 else (2,0)
            # Include all underlying quotes/probabilities in the no-Kelly comparator.
            for p,ret in zip(m['books'],m['payouts']):x.extend([math.log(p[i]/p[j]),math.log(p[1]),ret])
            o=m['over25'];x.append(math.log(o/(1-o)))
        if 'kelly' in arm:
            k=row['market']['kelly_relative'];x.extend([k[0 if own==0 else 2],k[2 if own==0 else 0],k[1]])
        if 'elo' in arm:x.append(row['elo_gap']*(1 if own==0 else -1))
        values.append(x)
    return np.asarray(values,float)


def fit(rows,arm):
    x=np.concatenate([design(r,arm) for r in rows]);y=np.asarray([[r['hg'],r['ag']] for r in rows]).ravel()
    lo,hi=np.quantile(x,PROTOCOL['winsor_quantiles'],axis=0);bounded=np.clip(x,lo,hi)
    mean=bounded.mean(0);scale=bounded.std(0);scale[scale==0]=1
    z=np.clip((bounded-mean)/scale,-PROTOCOL['max_standardized_feature'],PROTOCOL['max_standardized_feature'])
    # Global finite predictor bound follows from the feature and coefficient contract.
    bound=PROTOCOL['coefficient_bound']  # identical shared-parameter bounds across arms
    result=minimize(objective,np.r_[math.log(max(y.mean(),.01)),np.zeros(x.shape[1])],
        args=(z,y,PROTOCOL['ridge_alpha']),jac=True,method='L-BFGS-B',
        bounds=[(-4,4)]+[(-bound,bound)]*x.shape[1],options={'maxiter':1500,'ftol':1e-12,'gtol':1e-8})
    if not result.success:raise RuntimeError('Poisson fit did not converge')
    return {'arm':arm,'coef':result.x.tolist(),'mean':mean.tolist(),'scale':scale.tolist(),
        'lo':lo.tolist(),'hi':hi.tolist(),'train_n':len(rows),'train_end':max(r['date'] for r in rows),
        'train_id_sha256':digest([r['id'] for r in rows]),'max_standardized_feature':PROTOCOL['max_standardized_feature']}


def predict(model,row):
    x=design(row,model['arm']);z=np.clip((np.clip(x,model['lo'],model['hi'])-model['mean'])/model['scale'],
                -model['max_standardized_feature'],model['max_standardized_feature'])
    lam=np.exp(model['coef'][0]+z@np.asarray(model['coef'][1:]))
    if not np.isfinite(lam).all() or (lam<=0).any():raise ValueError('nonfinite rates')
    return lam


def summary(predictions):
    result={};groups=defaultdict(list)
    for r in predictions:
        groups['all'].append(r);groups[r['league']].append(r)
    for key,rs in groups.items():
        arms={a:{'n':len(rs),'hits':sum(r['models'][a]['metrics']['top1'] for r in rs),
             **{m:float(np.mean([r['models'][a]['metrics'][m] for r in rs])) for m in ('score_logloss','wdl_brier','wdl_acc','tail4_brier')},
             'rates_gt_10':sum(max(r['models'][a]['lambda'])>10 for r in rs),
             'one_one_recommendations':sum(r['models'][a]['top1']==[1,1] for r in rs)} for a in ARMS}
        comparisons={}
        for base,cand in [('goals','goals_elo'),('goals','goals_market'),('goals_market','goals_market_kelly'),('goals_market_kelly','goals_market_kelly_elo')]:
            blocks=defaultdict(list)
            for r in rs:blocks[r['date']].append(r['models'][cand]['metrics']['top1']-r['models'][base]['metrics']['top1'])
            vals=np.asarray([[sum(v),len(v)] for v in blocks.values()]);rng=np.random.default_rng(20261112)
            ix=rng.integers(len(vals),size=(2000,len(vals)));s=vals[ix].sum(1);ci=np.quantile(s[:,0]/s[:,1],[.025,.975]).tolist()
            comparisons[cand+'_vs_'+base]={'net_hits':arms[cand]['hits']-arms[base]['hits'],'net_rate_ci95_date_blocks':ci}
        result[key]={'arms':arms,'comparisons':comparisons}
    return result


def run(manifest_path,out):
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    write(out/'protocol.json',PROTOCOL | {'created_at':datetime.now(timezone.utc).isoformat(),
         'source_module_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
         'input_manifest_sha256':hashlib.sha256(Path(manifest_path).read_bytes()).hexdigest()})
    rows,exclusions=load(manifest_path);fs=feature_rows(rows);paired=[r for r in fs if r['market'] is not None]
    predictions=[];fits=[]
    for league in PROTOCOL['leagues']:
        cohort=[r for r in paired if r['league']==league]
        for month in sorted({r['date'][:7] for r in cohort if r['season']==PROTOCOL['diagnostic_season']}):
            train=[r for r in cohort if r['date'][:7]<month];targets=[r for r in cohort if r['date'][:7]==month and r['season']==PROTOCOL['diagnostic_season']]
            if len(train)<PROTOCOL['minimum_train']:continue
            models={a:fit(train,a) for a in ARMS};fits.append({'league':league,'month':month,'models':models})
            for r in targets:
                results={}
                for a,model in models.items():
                    mu=predict(model,r);results[a]={'lambda':mu.tolist(),'top1':[max(0,math.ceil(v)-1) for v in mu],'metrics':metrics(r,mu)}
                predictions.append({k:r[k] for k in ('id','league','date','hg','ag','source_sha256','source_line')} | {'models':results})
        print(league,'predicted',sum(r['league']==league for r in predictions),flush=True)
    # Final deployment candidates remain inactive; evaluation fits excluded their own month.
    final={league:{a:fit([r for r in paired if r['league']==league],a) for a in ARMS} for league in PROTOCOL['leagues']}
    write(out/'models.json',fits);write(out/'candidate-models.json',final);write(out/'predictions.json',predictions)
    predicted={r['id'] for r in predictions}; featured={r['id'] for r in fs}
    diagnostic=[r for r in rows if r['season']==PROTOCOL['diagnostic_season']]
    exclusions += [{'id':r['id'],'league':r['league'],'date':r['date'],
        'reason':'insufficient_prior_history' if r['id'] not in featured else 'missing_fixed_closing_panel' if r['market'] is None else 'insufficient_training'}
        for r in diagnostic if r['id'] not in predicted]
    write(out/'exclusions.json',exclusions)
    report={'status':'COMPLETED_RETROSPECTIVE_NOT_ASOF','coverage':{'raw_labeled':len(rows),'diagnostic_labeled_population':len(diagnostic),'prior_features':len(fs),'complete_panel':len(paired),'diagnostic_predictions':len(predictions)},
       'summary':summary(predictions),'active_modified':False,'future_validated':False,'promotion':False,
       'limitations':['Previously examined diagnostic season; not pristine holdout.','Retained closing odds have no certified quoted/first-known times.',
        'Statistics are research matrix modes, not comprehensive Primary or active Current/Test.','No explicit home advantage; neutral venue unknown.',
        'Kelly is a deterministic representation of the same bookmaker panel, not independent evidence.']}
    write(out/'report.json',report)
    write(out/'manifest.json',{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in out.iterdir() if p.is_file()})
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--manifest',required=True);p.add_argument('--out',required=True);a=p.parse_args();run(a.manifest,a.out)
