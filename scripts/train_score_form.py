"""Train league xG form candidates and reserve a complete later season for one evaluation."""
import argparse
from collections import defaultdict, deque
from datetime import date, datetime, timezone
from itertools import groupby
import hashlib
import json
import math
from pathlib import Path
import sys

import numpy as np
from sklearn.linear_model import PoissonRegressor

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from research.analysis_freeze import digest
from research_program import score_form as form
from research_program.three_arm_model import score_picks, top_three_scores

LEAGUES={'eng.1':'EPL','ger.1':'Bundesliga','esp.1':'La_liga','ita.1':'Serie_A','fra.1':'Ligue_1'}
SHAPE=(21,21)


def load_seasons(directory, league):
    games=[]; sources=[]
    for season in range(2020,2026):
        src=directory/league/str(season)/'league-data.json'
        data=src.read_bytes()
        sources.append({'file':str(src.relative_to(directory)), 'sha256':hashlib.sha256(data).hexdigest()})
        for r in json.loads(data)['dates']:
            if r['isResult'] is not True: continue
            goals=[int(r['goals'][s]) for s in ('h','a')]
            xg=[float(r['xG'][s]) if r['xG'][s] is not None else None for s in ('h','a')]
            if min(goals)<0 or any(v is not None and (not math.isfinite(v) or not 0<=v<=30) for v in xg):
                raise ValueError('invalid archived match')
            if (xg[0] is None)!=(xg[1] is None):xg=[None,None]
            games.append({'id':r['id'],'date':r['datetime'][:10],'season':season,
                          'home':r['h']['id'],'away':r['a']['id'],'goals':goals,'xg':xg})
    if len({g['id'] for g in games})!=len(games):raise ValueError('duplicate source match ID')
    games.sort(key=lambda g:(g['date'],g['id']))
    return games,sources


def examples(games):
    history=defaultdict(lambda: deque(maxlen=form.WINDOW)); items=[]
    for day,block in groupby(games,key=lambda g:g['date']):
        matches=list(block)
        for g in matches:
            home,away=[form.summarize(list(reversed(history[g[s]])),date.fromisoformat(day)) for s in ('home','away')]
            items.append((g,form.features(home,away)))
        for g in matches:
            for index,side in enumerate(('home','away')):
                other=1-index
                history[g[side]].append({'date':day,'gf':g['goals'][index],'ga':g['goals'][other],
                                         'xgf':g['xg'][index],'xga':g['xg'][other]})
    return items


def fit(rows, names):
    x=np.concatenate([v[:,:len(names)] for _,v in rows])
    y=np.concatenate([r['goals'] for r,_ in rows])
    m=PoissonRegressor(alpha=.03,max_iter=300,tol=1e-9).fit(x,y)
    return {'features':list(names),'intercept':float(m.intercept_),'coefficients':m.coef_.tolist()}


def score(matrix, g):
    actual='-'.join(map(str,g['goals']))
    ranked=top_three_scores(matrix)
    wdl=score_picks(matrix)['wdl']
    h,a=g['goals']
    return {'scores':ranked,'wdl':wdl,'primary_hit':actual==ranked[0],'three_hit':actual in ranked,
            'wdl_hit':wdl==('home' if h>a else 'away' if h<a else 'draw'),
            'score_log_loss':-math.log(float(matrix[h,a]))}


def tally(rows):
    n=len(rows)
    return {'n':n,**{k:sum(r[k] for r in rows) for k in ('primary_hit','three_hit','wdl_hit')},
            'score_log_loss':sum(r['score_log_loss'] for r in rows)/n}


def train(cohort, games, sources, built_at):
    items=examples(games)
    train_rows=[v for v in items if v[0]['season']<2023]
    calibration,selection,holdout=([v for v in items if v[0]['season']==s] for s in (2023,2024,2025))
    if not all((train_rows,calibration,selection,holdout)):raise ValueError('all chronological partitions required')
    for earlier,later in zip((train_rows,calibration,selection),(calibration,selection,holdout)):
        if max(g['date'] for g,_ in earlier)>=min(g['date'] for g,_ in later): raise ValueError('overlapping season partitions')
    goals=fit(train_rows,form.GOALS_FEATURES); xg=fit(train_rows,form.FEATURES)
    observed=np.zeros(25);expected=np.zeros(25);cells=form.cell_index(SHAPE)
    for g,x in calibration:
        m=form.poisson_matrix(form.rates(xg,x),SHAPE)
        expected+=np.bincount(cells.ravel(),weights=m.ravel(),minlength=25)
        h,a=g['goals'];observed[5*min(4,h)+min(4,a)]+=1
    prior=380*expected/len(calibration)+1
    factors=(observed+prior)/(expected+prior)
    trials={v:[] for v in (0,.5,1)}
    for g,x in selection:
        m=form.poisson_matrix(form.rates(xg,x),SHAPE)
        for w,rows in trials.items():rows.append(score(form.calibrate(m,factors,w),g))
    summaries={w:tally(rs) for w,rs in trials.items()}
    def objective(w):
        r=summaries[w]
        return (r['primary_hit'],r['three_hit'],-r['score_log_loss'],-w)
    weight=max(summaries,key=objective)
    model={'version':form.VERSION,'cohort':cohort,'built_at':built_at,'fit_end':train_rows[-1][0]['date'],
           'cell_fit_end':calibration[-1][0]['date'],'selection_end':selection[-1][0]['date'],'goals':goals,'xg':xg,
           'weight':weight,'cell_factors':factors.tolist(),'source_files':sources[:5]}
    model['artifact_sha256']=digest(model)
    records=[]
    for g,x in holdout:
        base=form.poisson_matrix(form.rates(goals,x),SHAPE); expert=form.poisson_matrix(form.rates(xg,x),SHAPE)
        arms={'goals':base,'xg':expert,'xg_calibrated':form.calibrate(expert,factors,weight)}
        records.append({'id':g['id'],'date':g['date'],'cohort':cohort,'actual':g['goals'],
                        'arms':{k:score(m,g) for k,m in arms.items()}})
    metrics={'partitions':{'fit':len(train_rows),'cell_calibration':len(calibration),'selection':len(selection),'reserved':len(holdout)},
             'genuine_xg_training_matches':sum(None not in g['xg'] for g,_ in train_rows),
             'selection':summaries,'weight':weight,'holdout':{k:tally([r['arms'][k] for r in records]) for k in records[0]['arms']}}
    return model,records,metrics


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--understat-root',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();a.out.mkdir(parents=True,exist_ok=True)
    stamp=datetime.now(timezone.utc).isoformat()
    all_rows=[]; report={};files={}
    for cohort,league in LEAGUES.items():
        games,src=load_seasons(a.understat_root,league)
        model,records,metrics=train(cohort,games,src,stamp)
        (a.out/(cohort+'.json')).write_text(json.dumps(model,indent=2,allow_nan=False)+'\n')
        report[cohort]=metrics;files[cohort]=src;all_rows+=records
    with (a.out/'holdout.jsonl').open('w') as f:
        for r in all_rows:f.write(json.dumps(r,allow_nan=False)+'\n')
    overall={k:tally([r['arms'][k] for r in all_rows]) for k in all_rows[0]['arms']}
    summary={'built_at':stamp,'historical_source_availability':'retrospective source snapshot; first-known times unproven',
             'league_results':report,'total_holdout':overall,'sources':files,
             'historical_source_timed_ou_n':0,'holdout_season':'2025/26'}
    (a.out/'evaluation.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(json.dumps(overall))
if __name__=='__main__':main()
