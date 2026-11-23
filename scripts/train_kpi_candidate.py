"""Train a dated closing-proxy challenger, with a fixed comparison by season."""

import argparse
from collections import defaultdict, deque
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, timezone
import hashlib
from itertools import groupby
import json
from pathlib import Path
import sys

import lightgbm as lgb
import numpy as np
from scipy.optimize import minimize
from scipy.special import logsumexp

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'runtime'), str(ROOT/'runtime/v5'), str(ROOT)]
from research.analysis_freeze import digest
from research_program import kpi_score_candidate as model
from research_program.score_form import WINDOW, cell_index, poisson_matrix, summarize

SEASONS = {'fit': ('1920','2021','2122','2223','2324'), 'select': ('2425',),
           'known_holdout': ('2526',), 'final': ('2627',)}
DIRECTIONS = ('home','draw','away')
REGULARIZATION = .008


def read_examples(path):
    seasons = {s: group for group, seq in SEASONS.items() for s in seq}
    data = []
    for line in path.read_text().splitlines():
        r = json.loads(line)
        if r['cohort'] in model.LEAGUES and r['season'] in seasons:
            assert r['historical_proxy'] is True
            data.append(r)
    if len({r['match_id'] for r in data}) != len(data):
        raise ValueError('duplicate historical match identities')
    history = defaultdict(lambda: deque(maxlen=WINDOW))
    full = defaultdict(int)
    examples = []
    for day, values in groupby(sorted(data, key=lambda r: r['game_date']), key=lambda r: r['game_date']):
        matches = list(values)
        day_date = date.fromisoformat(day)
        for r in matches:
            split = seasons[r['season']]
            full[split] += 1
            recent = [summarize(list(reversed(history[r['cohort'],r[role]])),day_date) for role in ('home','away')]
            actual = [int(r['home_goals']),int(r['away_goals'])]
            if min(actual)<0 or max(actual)>=min(model.SHAPE):
                raise ValueError('historical result outside score support')
            examples.append((r, split, actual, model.form_features(r['cohort'], *recent)))
        for r in matches:
            xg = r.get('xg_understat') or r.get('xg') or {}
            for role,opp in (('home','away'),('away','home')):
                history[r['cohort'],r[role]].append(
                    {'date':day,'gf':r[role+'_goals'],'ga':r[opp+'_goals'],
                     'xgf':xg.get(role),'xga':xg.get(opp)})
    for k in ('fit','select','known_holdout','final'):
        if not full[k]:raise ValueError(f'missing date partition {k}')
    return examples, dict(full)


def means_fit(features, actual, use_xg):
    x = features.copy()
    if not use_xg:
        model.mask_xg(x)
    params={'objective':'poisson','num_leaves':12,'min_data_in_leaf':150,
            'learning_rate':.04,'lambda_l2':3.,'verbosity':-1,
            'num_threads':3,'seed':33,'deterministic':True,'force_col_wise':True}
    train = lgb.Dataset(x.reshape(-1,x.shape[-1]), label=actual.ravel())
    return lgb.train(params,train,num_boost_round=125).model_to_string()


def fit_tilt(matrix, actual):
    cols = model.market_context(matrix)
    cell = cell_index(matrix.shape[1:])
    scores = np.minimum(actual,4) @ [5,1]
    groups = np.stack([matrix[:,cell==i].sum(axis=-1) for i in range(25)],axis=1)
    logits_base=np.log(np.maximum(groups,1e-250))
    observed=np.eye(25)[scores]
    def objective(beta):
        b=beta.reshape(cols.shape[-1],25)
        logits=logits_base+cols@b
        lognorm=logsumexp(logits,axis=1,keepdims=True)
        loss=np.mean(lognorm[:,0]-logits[np.arange(len(scores)),scores])+.5*REGULARIZATION*np.sum(beta**2)
        grad=cols.T@(np.exp(logits-lognorm)-observed)/len(scores)+REGULARIZATION*b
        return loss,grad.ravel()
    solve=minimize(objective,np.zeros(cols.shape[-1]*25),method='L-BFGS-B',jac=True,
                   options={'maxiter':180,'gtol':1e-7,'ftol':1e-12})
    if not solve.success:
        raise ValueError(f'score calibration did not converge: {solve.message}')
    return solve.x.reshape(cols.shape[-1],25)


def score_arrays(matrices, actual, lines):
    flat=matrices.reshape(len(actual),-1)
    top=np.argsort(-flat,axis=1,kind='stable')[:,:3]
    home,away=np.indices(matrices.shape[-2:])
    true_index=actual[:,0]*matrices.shape[-1]+actual[:,1]
    true_total=actual.sum(axis=1)
    truth_wdl=np.where(actual[:,0]>actual[:,1],0,np.where(actual[:,0]==actual[:,1],1,2))
    dirp=np.stack([matrices[:,mask].sum(axis=1) for mask in (home>away,home==away,home<away)],axis=1)
    wdl=dirp.argmax(axis=1)
    total=home+away
    sides=np.empty(len(actual),int)
    settlements=np.empty(len(actual),int)
    for line in sorted(set(lines)):
        idx=np.flatnonzero(lines==line)
        picks=matrices[idx]
        legs=(line,line) if (float(line)*2).is_integer() else (line-.25,line+.25)
        settlegrid=sum(np.sign(total-leg).astype(int) for leg in legs)
        # Identical non-push denominator for both directions. Tie resolved by settlement balance.
        o=picks[:,settlegrid==2].sum(axis=-1);u=picks[:,settlegrid==-2].sum(axis=-1)
        net=np.sum(picks*settlegrid,axis=(1,2))
        is_over=(o-u>1e-12) | ((np.abs(o-u)<=1e-12) & (net>=-1e-12))
        sides[idx]=np.where(is_over,1,-1)
        settlements[idx]=sides[idx]*sum(np.sign(true_total[idx]-leg).astype(int) for leg in legs)
    return dict(top=top, exact=top[:,0]==true_index, three=(top==true_index[:,None]).any(axis=1),
                wdl=wdl, wdl_hit=wdl==truth_wdl, ou=sides, settlement=settlements,
                logloss=-np.log(np.maximum(flat[np.arange(len(actual)),true_index],1e-250)))


def summary(pack):
    n=len(pack['exact'])
    totals={name:int((pack['settlement']==code).sum()) for name,code in
            [('win',2),('half_win',1),('push',0),('half_loss',-1),('loss',-2)]}
    return dict(n=n,primary=int(pack['exact'].sum()),top3=int(pack['three'].sum()),
                wdl=int(pack['wdl_hit'].sum()),total_full_win=totals['win'],
                total_non_push=n-totals['push'],settlements=totals,
                joint_log_loss=float(pack['logloss'].mean()))


def signature(data):
    return hashlib.sha256(Path(data).read_bytes()).hexdigest()


def train(path, out, protocol, jobs):
    examples, population = read_examples(path)
    # Form training uses every match in the fit period, including rows missing prices.
    train_rows=[v for v in examples if v[1]=='fit']
    x=np.stack([r[3] for r in train_rows])
    y=np.array([r[2] for r in train_rows])
    f_goal=means_fit(x,y,False)
    f_xg=means_fit(x,y,True)
    data=[r for r in examples if r[0].get('market')]
    recs,splits,ys,xs=zip(*data)
    labels=np.array(splits)
    actual=np.array(ys)
    x=np.stack(xs)
    lines=np.array([r['market']['total']['line'] for r in recs])
    print('fit models; constructing same-quote markets',len(data),population,flush=True)
    if jobs>1:
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            m=np.stack(list(pool.map(model.quotes_matrix,recs,chunksize=84)))
    else:
        m=np.stack([model.quotes_matrix(r) for r in recs])
    fit=labels=='fit'
    calibration=fit_tilt(m[fit],actual[fit])
    tilt=model.recalibrate(m,calibration)
    goals_features=x.copy()
    model.mask_xg(goals_features)
    shapes=[np.stack([poisson_matrix(pair, model.SHAPE) for pair in model.expected_form(code, feats)])
            for code,feats in [(f_goal,goals_features),(f_xg,x)]]
    all_matrices=dict(market=m,market_tilt=tilt,form_goals=shapes[0],form_xg=shapes[1],
                      blend_25=.75*tilt+.25*shapes[1],blend_50=.50*tilt+.50*shapes[1])
    def eval_arm(arm,mat,mask):return score_arrays(mat[mask],actual[mask],lines[mask])
    mask=labels=='select'
    tuning={k:summary(eval_arm(k,v,mask)) for k,v in all_matrices.items()}
    def objective(name):
        z=tuning[name]
        return z['primary'], z['top3'], z['wdl'], z['total_full_win']/z['total_non_push'], -z['joint_log_loss']
    selected=max(tuning,key=objective)
    output={'version':model.VERSION,'selected_recipe':selected,'built_at':datetime.now(timezone.utc).isoformat(),
            'selection_end':max(r['game_date'] for r,l in zip(recs,labels) if l=='select'),
            'source_data_sha256':signature(path),'protocol_sha256':signature(protocol),
            'fit_end':max(r['game_date'] for r,split,*_ in train_rows if split=='fit'),
            'tilt':calibration.tolist(),'form_goals':f_goal,'form_xg':f_xg,
            'features':list(model.FEATURE_NAMES),'source_date_basis':'historical closing-time proxy'}
    output['artifact_sha256']=model.artifact_identity(output)
    out.mkdir(parents=True,exist_ok=True)
    (out/'artifact.json').write_text(json.dumps(output,indent=2,allow_nan=False)+'\n')
    # Execute final evaluation only after selecting the recipe and serializing its artifact.
    report={'version':model.VERSION,'selected_recipe':selected,'population':population,
            'training_xg_pair_count':sum(bool(r.get('xg_understat') or r.get('xg')) for r,*_ in train_rows),
            'pricing_method':'archival closing proxy; phase valid; total at actual source line',
            'time_partitions':SEASONS,'selection':tuning,'checks':{}}
    for split in ('known_holdout','final'):
        mask=labels==split
        results={k:eval_arm(k,v,mask) for k,v in all_matrices.items()}
        stats={k:summary(v) for k,v in results.items()}
        b=results['market']; z=results[selected]
        paired={}
        for name in ('exact','three','wdl_hit','settlement'):
            left,right=b[name],z[name]
            if name=='settlement':
                eligible=left!=0
                left,right=left[eligible]==2,right[eligible]==2
            gain=int((right & ~left).sum());loss=int((left & ~right).sum())
            paired[name]={'n':len(left),'gains':gain,'losses':loss,'net':gain-loss}
        report['checks'][split]={'all_trials':stats,'selected':stats[selected],'paired_market':paired}
        with (out/f'{split}-predictions.jsonl').open('w') as handle:
            for j,(r,goals,line) in enumerate(zip(np.array(recs,object)[mask],actual[mask],lines[mask])):
                handle.write(json.dumps(dict(id=r['match_id'],cohort=r['cohort'],date=r['game_date'],actual=goals.tolist(),
                    line=float(line),forecast={k:dict(top3=[f'{n//model.SHAPE[1]}-{n%model.SHAPE[1]}' for n in item['top'][j]],
                     wdl=DIRECTIONS[item['wdl'][j]],settlement=int(item['settlement'][j])) for k,item in results.items()}),
                    allow_nan=False)+'\n')
    (out/'evaluation.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    print(json.dumps(report,indent=2),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('data',type=Path)
    parser.add_argument('--protocol',required=True,type=Path)
    parser.add_argument('--out',required=True,type=Path)
    parser.add_argument('--jobs',type=int,default=4)
    args=parser.parse_args()
    train(args.data,args.out,args.protocol,args.jobs)
