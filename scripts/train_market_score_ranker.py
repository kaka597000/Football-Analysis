"""Time-isolated score-ranking and market-path experiments."""

import argparse
from collections import defaultdict, deque, Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timezone
from itertools import groupby
import json
from pathlib import Path
import sys

import lightgbm as lgb
import numpy as np

HERE=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(HERE/'runtime'),str(HERE/'runtime/v5'),str(HERE/'scripts'),str(HERE)]
from research_program import market_score_ranker as ranker
from research_program.future_validation import sha
from research_program.kpi_score_candidate import form_features, LEAGUES
from research_program.score_form import WINDOW, summarize
from train_kpi_candidate import score_arrays, summary, DIRECTIONS
SHAPE = ranker.SHAPE

MODERN={'fit':('1920','2021','2122','2223','2324'),'select':('2425',),'check_2526':('2526',),'check_2627':('2627',)}
LEGACY={'fit':('1516','1617'),'select':('1718',),'holdout_1819':('1819',)}


@dataclass
class Fixture:
    raw: dict
    split: str
    goals: list
    features: np.ndarray


def read_fixture_rows(path, partitions):
    labels={season:name for name,seasons in partitions.items() for season in seasons}
    games=[]
    for line in path.open():
        r=json.loads(line)
        if r['cohort'] in LEAGUES and r['season'] in labels:
            if not r['historical_proxy'] or r['score_basis']!='regulation_90' or r['stage']!='regular':
                raise ValueError('expected date-only completed top-flight regulation label')
            games.append(r)
    if len({r['match_id'] for r in games})!=len(games):
        raise ValueError('duplicate fixture IDs')
    history=defaultdict(lambda:deque(maxlen=WINDOW))
    output=[]
    for day,items in groupby(sorted(games,key=lambda r:r['game_date']), lambda r:r['game_date']):
        batch=list(items); target=date.fromisoformat(day)
        for r in batch:
            home,away=[summarize(list(reversed(history[r['cohort'],r[side]])),target)
                       for side in ('home','away')]
            f=Fixture(r,labels[r['season']],[r['home_goals'],r['away_goals']],form_features(r['cohort'],home,away))
            output.append(f)
        for r in batch:
            xg=r.get('xg_understat') or r.get('xg') or {}
            for side,opponent in (('home','away'),('away','home')):
                history[r['cohort'],r[side]].append({
                    'date':day,'gf':r[side+'_goals'],'ga':r[opponent+'_goals'],
                    'xgf':xg.get(side),'xga':xg.get(opponent)})
    for first, second in zip(partitions, list(partitions)[1:]):
        if max(f.raw['game_date'] for f in output if f.split==first) >= min(f.raw['game_date'] for f in output if f.split==second):
            raise ValueError('chronological bucket overlap')
    return output


def vectors(f):
    r=f.raw; main=r['market']
    historical_path=ranker.historical_path(main,(r.get('market_paths') or {}).get(main['bookmaker'],{}))
    regular=r.get('market_by_book') or {}
    qx,fx,market=ranker.design_row(r['cohort'],main,regular,f.features)
    pure_qx,pure_fx,_=ranker.design_row(r['cohort'],main,{},f.features)
    mats=[ranker.normalize_market(m) for m in (main,market)] if regular else [ranker.normalize_market(main)]*2
    return qx,fx,np.r_[fx,historical_path],pure_qx,pure_fx,np.r_[pure_fx,np.full(len(historical_path),np.nan)],*mats


def fit(x, y):
    params={'objective':'multiclass','num_class':25,'num_leaves':11,'min_data_in_leaf':150,
            'learning_rate':.04,'lambda_l2':4.,'verbosity':-1,'force_col_wise':True,
            'seed':20261123,'num_threads':3,'deterministic':True}
    return lgb.train(params,lgb.Dataset(x,label=y),num_boost_round=76).model_to_string()


def blend_models(market, x_market, x_form, x_path, trained):
    result={}
    for k,features in [('market',x_market),('form',x_form),('path',x_path)]:
        if k not in trained:
            continue
        for w in ranker.FRACTIONS:
            result[f'{k}_rank_{round(w*100)}']=ranker.mix(market,trained[k],features,w)
    return result


def train(source, protocol, out, legacy=False, jobs=4):
    out.mkdir(parents=True,exist_ok=True)
    plan=LEGACY if legacy else MODERN
    all_rows=read_fixture_rows(source,plan)
    dataset=[f for f in all_rows if f.raw.get('market')]
    print('Constructing aligned market score priors',len(dataset),flush=True)
    if jobs>1:
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            features=list(pool.map(vectors,dataset,chunksize=100))
    else:
        features=[vectors(f) for f in dataset]
    qx,fx,px,q_single,f_single,p_single,market,consensus=[np.stack(t) for t in zip(*features)]
    split=np.array([f.split for f in dataset])
    y=np.array([f.goals for f in dataset])
    lines=np.array([f.raw['market']['total']['line'] for f in dataset])
    capped=np.minimum(y,4)@[5,1]
    fit_mask=split=='fit'
    # A single-book row is the same match written without its auxiliary quotes.
    # Both views are included only during fitting so the native missing-book path is learned.
    boards=[('market',qx,q_single),('form',fx,f_single)]
    if not legacy:
        boards.append(('path',px,p_single))
    models={}
    for name,context,single in boards:
        print('Training',name,flush=True)
        models[name]=fit(np.concatenate((context[fit_mask],single[fit_mask])),np.tile(capped[fit_mask],2))
    all_arms={'market':market,'consensus':consensus,**blend_models(consensus,qx,fx,px,models)}
    # Scope of the runnable live branch is the form/market ranking without unobserved opening-time features.
    live_models={k:v for k,v in models.items() if k!='path'}
    live_arms={'market':market,**blend_models(market,q_single,f_single,p_single,live_models)}
    mask=split=='select'
    def collect(arms,mask):
        return {k:score_arrays(v[mask],y[mask],lines[mask]) for k,v in arms.items()}
    tune={k:summary(v) for k,v in collect(all_arms,mask).items()}
    live_tune={k:summary(v) for k,v in collect(live_arms,mask).items()}
    def decide(items,exclude=()):
        def key(x):
            return x['primary'],x['top3'],x['wdl'],x['total_full_win']/x['total_non_push'],-x['joint_log_loss']
        return max((k for k in items if k not in exclude),key=lambda k:key(items[k]))
    research=decide(tune)
    selected=decide(live_tune)
    # Preserve an independent candidate even when the market is selected.
    challenger=decide(live_tune,exclude=('market',))
    instant=datetime.now(timezone.utc).isoformat()
    artifact={'version':ranker.VERSION,'arm':challenger,'preferred_native_arm':selected,
              'built_at':instant,'selection_end':max(f.raw['game_date'] for f in dataset if f.split=='select'),
              'training_end':max(f.raw['game_date'] for f in dataset if f.split=='fit'),
              'market_model':models['market'],'form_model':models['form'],'protocol_sha256':sha(protocol),
              'source_sha256':sha(source),'path_models_research_only':True}
    artifact['artifact_sha256']=ranker.artifact_identity(artifact)
    write(out/'artifact.json',artifact)
    if 'path' in models:
        write(out/'path-model.json',{'version':ranker.VERSION,'training_source':sha(source),'model':models['path']})
    metadata={'input':str(source),'fit_policy':plan,'preselected_offline_arm':research,'preselected_live_arm':selected,
              'live_research_challenger':challenger,'split_population':dict(Counter(f.split for f in all_rows)),
              'fit_market_xg':sum(bool(f.raw.get('xg_understat') or f.raw.get('xg')) for f in dataset if f.split=='fit'),
              'selection_results':tune,'live_replica_selection_results':live_tune,'outcomes':{}}
    for phase in list(plan)[2:]:
        mask=split==phase
        all_predictions=collect(all_arms,mask)
        live_predictions=collect(live_arms,mask)
        metadata['outcomes'][phase]={'offline':{k:summary(v) for k,v in all_predictions.items()},
                                     'native_single_book':{k:summary(v) for k,v in live_predictions.items()}}
        with (out/(phase+'-paired.jsonl')).open('w') as file:
            for idx,f in enumerate(t for t in dataset if t.split==phase):
                cells={k:v for k,v in all_predictions.items()}
                cells.update({'native_'+k:v for k,v in live_predictions.items()})
                r={'id':f.raw['match_id'],'cohort':f.raw['cohort'],'date':f.raw['game_date'],
                   'actual':f.goals,'line':f.raw['market']['total']['line'],'forecast':{}}
                for k,v in cells.items():
                    top=[f'{n//SHAPE[1]}-{n%SHAPE[1]}' for n in v['top'][idx]]
                    r['forecast'][k]={'top3':top,'wdl':DIRECTIONS[v['wdl'][idx]],
                                      'settlement':int(v['settlement'][idx])}
                file.write(json.dumps(r,allow_nan=False)+'\n')
            for f in all_rows:
                if f.split==phase and not f.raw.get('market'):
                    r={'id':f.raw['match_id'],'cohort':f.raw['cohort'],'date':f.raw['game_date'],
                       'actual':f.goals,'line':None,'forecast':{}}
                    file.write(json.dumps(r,allow_nan=False)+'\n')
    write(out/'summary.json',metadata)
    print(json.dumps({k:v for k,v in metadata.items() if k not in ('selection_results','live_replica_selection_results')},indent=2),flush=True)


def write(path,data):
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+'\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('--protocol',required=True,type=Path)
    parser.add_argument('--out',required=True,type=Path)
    parser.add_argument('--legacy',action='store_true')
    parser.add_argument('--jobs',type=int,default=4)
    args=parser.parse_args()
    train(args.source,args.protocol,args.out,args.legacy,args.jobs)
