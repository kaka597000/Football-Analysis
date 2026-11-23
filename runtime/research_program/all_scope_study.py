"""Train, lock and evaluate the four score/market heads separately for every configured competition."""
import argparse
from collections import Counter, defaultdict
from datetime import date
from itertools import groupby
import json
from pathlib import Path

import lightgbm as lgb
import numpy as np

from research.analysis_freeze import digest
from . import all_scope_model as model
from .goal_state_process import combine_heads
from .goal_state_study import read_lines, sha, write_new
from .paired_kpi_inference import compare
from optimization.evaluate import settle_ou

CALENDAR = {'jpn.1','kor.1','usa.1','swe.1','nor.1','bra.1','jpn.league_cup'}
NATIONAL = {'uefa.nations','fifa.world'}
LAST = '2026-11-22'
KPI = ('score_top1','score_top3','wdl','actual_ou')


def boundaries(competition):
    if competition == 'fifa.world':
        return ('2022-01-01','2026-01-01','2027-01-01')
    if competition == 'uefa.nations':
        return ('2022-06-01','2024-07-01','2025-07-01')
    if competition in CALENDAR:
        return ('2024-01-01','2025-01-01','2026-01-01')
    return ('2024-07-01','2025-07-01','2026-07-01')


def half_life(competition):
    return 1461 if competition=='fifa.world' else 730 if competition=='uefa.nations' else 270


def check_panel(path,competition):
    rows=sorted(read_lines(path),key=lambda r:(r['date'],r['id']))
    seen=set()
    for r in rows:
        if (r['competition']!=competition or not r['stage'] or not r['id'] or r['id'] in seen
                or date.fromisoformat(r['date']).isoformat()!=r['date'] or r['date']>LAST
                or not r['home'] or not r['away'] or r['home']==r['away']
                or any(type(r.get(k))!=int or r[k]<0 for k in ('home_goals','away_goals'))):
            raise ValueError('invalid, duplicate or non-regulation fixture in '+competition+': '+str(r.get('id')))
        seen.add(r['id'])
    return rows


def choose(validation, arms=model.ARMS):
    def wins(name,head):
        hits=0
        for r in validation:
            p=r['forecast'][name]
            h,a=r['actual']
            if head=='first':
                hits+=p['primary']==f'{h}-{a}'
            elif head=='wdl':
                hits+=p['wdl']==('home' if h>a else 'away' if a>h else 'draw')
            elif p['total_side']:
                hits+=settle_ou(h+a,r['line'],p['total_side'])=='FULL_WIN'
        return hits
    counts={head:{n:wins(n,head) for n in arms} for head in ('first','wdl','total')}
    policy={k:max(arms,key=v.get) for k,v in counts.items()}
    def backed(name):
        return sum(f"{r['actual'][0]}-{r['actual'][1]}" in
                   combine_heads(r['forecast'],dict(policy,backups=name))['top3'] for r in validation)
    counts['backups']={n:backed(n) for n in arms}
    policy['backups']=max(arms,key=counts['backups'].get)
    return policy,counts


def tree_delta(trees,features):
    if not trees:
        return None
    return np.column_stack([t.predict(features,raw_score=True,num_threads=1) for t in trees])


def forecasts(state,prepared,correction,game,policy=None):
    matrices=model.matrices(state,prepared,correction)
    picks={n:model.pick(m,prepared['line']) for n,m in matrices.items()}
    if policy is not None:
        picks['candidate']=combine_heads(picks,policy)
    picks['market']=model.market_picks(prepared,game,picks)
    return picks


def walk(rows,competition,states,trees,*,fit_rows=None,policy=None):
    """Make every forecast on one calendar date before applying any of its scores."""
    halflife=half_life(competition)
    predictions=[]
    for stamp,same_day in groupby(rows,key=lambda r:r['date']):
        day=date.fromisoformat(stamp).toordinal()
        phases=defaultdict(list)
        for r in same_day:
            phases[r['stage']].append(r)
        for stage,games in phases.items():
            state=states.setdefault(stage,model.blank_state())
            model.advance(state,day,halflife)
            prepared=[model.prepare(state,g,day,halflife) for g in games]
            if fit_rows is not None:
                fit_rows[stage].extend((p['features'],p['team'],[g['home_goals'],g['away_goals']])
                                       for g,p in zip(games,prepared))
            else:
                deltas=tree_delta(trees.get(stage),[p['features'] for p in prepared])
                for i,(g,p) in enumerate(zip(games,prepared)):
                    picks=forecasts(state,p,None if deltas is None else deltas[i],g,policy)
                    predictions.append(dict(id=g['id'],cohort=competition,stage=stage,date=stamp,home=g['home'],
                        away=g['away'],actual=[g['home_goals'],g['away_goals']],line=p['line'],
                        market_basis=p['kind'],source_sha256=g['source_sha256'],forecast=picks))
            model.update_day(state,[(g,p['team'],p['cell']) for g,p in zip(games,prepared)],day,halflife)
    return predictions


def store_lines(path,rows):
    with path.open('x',encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(r,ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\n')


def encode_trees(trees):
    return {k:[t.model_to_string() for t in pair] if pair else None for k,pair in trees.items()}


def restore(artifact):
    states={k:model.load_state(s) for k,s in artifact['states'].items()}
    trees={k:[lgb.Booster(model_str=x) for x in v] if v else None for k,v in artifact['trees'].items()}
    return states,trees


def fit(panel,competition,out):
    rows=check_panel(panel,competition)
    select,cut,stop=boundaries(competition)
    train=[r for r in rows if r['date']<select]
    val=[r for r in rows if select<=r['date']<cut]
    states={}
    examples=defaultdict(list)
    walk(train,competition,states,{},fit_rows=examples)
    trees={stage:model.fit_trees(values) for stage,values in examples.items()}
    preds=walk(val,competition,states,trees)
    policy,counts=choose(preds)
    by_stage=Counter(r['stage'] for r in train)
    state={k:model.dump_state(s) for k,s in states.items()}
    artifact=dict(version=model.VERSION,competition=competition,selection_start=select,
        evaluation_start=cut,evaluation_stop=stop,fit_n=len(train),selection_n=len(val),
        trained_stage_counts=dict(by_stage),selection_stage_counts=dict(Counter(r['stage'] for r in val)),
        tree_stage_counts={k:(v[0].num_trees() if v else 0) for k,v in trees.items()},
        fit_selection_sha256=digest(train+val),policy=policy,validation_head_hits=counts,states=state,trees=encode_trees(trees))
    out.mkdir(parents=True,exist_ok=True)
    write_new(out/'artifact.json',artifact)
    store_lines(out/'selection.jsonl',preds)
    write_new(out/'lock.json',{k:v for k,v in artifact.items() if k not in ('states','trees')}|{'artifact_sha256':sha(out/'artifact.json')})


def _score(rows,role):
    hits=[0]*4
    total_n=[0]*4
    for r in rows:
        p=r['forecast'].get(role,{})
        h,a=r['actual']
        truth=('home' if h>a else 'away' if a>h else 'draw')
        score=f'{h}-{a}'
        if p.get('primary') is not None:
            total_n[0]+=1; hits[0]+=score==p['primary']
        if p.get('top3') is not None:
            total_n[1]+=1; hits[1]+=score in p['top3']
        if p.get('wdl') is not None:
            total_n[2]+=1; hits[2]+=truth==p['wdl']
        if p.get('total_side') is not None:
            settlement=settle_ou(h+a,r['line'],p['total_side'])
            if settlement!='PUSH':
                total_n[3]+=1; hits[3]+=settlement=='FULL_WIN'
    return dict(zip(KPI,([n,d] for n,d in zip(hits,total_n))))


def stats(rows):
    if not rows:
        return {'population':0,'metrics':{},'paired':{},'phases':{}}
    comp=compare(rows,baseline='market',candidate='candidate')
    team=compare(rows,baseline='team',candidate='candidate',draws=1000)
    phases=defaultdict(list)
    for r in rows:
        phases[r['stage']].append(r)
    return dict(population=len(rows),from_date=rows[0]['date'],through=rows[-1]['date'],
        market_inputs=dict(Counter(r['market_basis'] for r in rows)),priced_line=sum(r['line'] is not None for r in rows),
        pushes=comp['common_total_pushes'],metrics={n:_score(rows,n) for n in ('candidate','market',*model.ARMS)},
        paired={k:{n:v[n] for n in ('eligible','gained','lost','net_hits','date_cluster_ci')} for k,v in comp['paired'].items()},
        vs_team={k:{n:v[n] for n in ('eligible','net_hits','date_cluster_ci')} for k,v in team['paired'].items()},
        phases={phase:dict(population=len(rs),metrics={n:_score(rs,n) for n in ('candidate','market')})
                for phase,rs in phases.items()})


def evaluate(panel,competition,out):
    rows=check_panel(panel,competition)
    artifact=json.loads((out/'artifact.json').read_bytes())
    lock=json.loads((out/'lock.json').read_bytes())
    cut,stop=artifact['evaluation_start'],artifact['evaluation_stop']
    if (lock['artifact_sha256']!=sha(out/'artifact.json') or artifact['competition']!=competition
            or digest([r for r in rows if r['date']<cut])!=artifact['fit_selection_sha256']):
        raise ValueError('competition or locked development inputs have changed')
    states,trees=restore(artifact)
    results={}
    for label,period in [('evaluation',[r for r in rows if cut<=r['date']<stop]),('recent',[r for r in rows if r['date']>=stop])]:
        preds=walk(period,competition,states,trees,policy=artifact['policy'])
        store_lines(out/(label+'.jsonl'),preds)
        results[label]=stats(preds)
    latest={k:model.dump_state(s) for k,s in states.items()}
    write_new(out/'history-state.json',dict(competition=competition,through=max((r['date'] for r in rows),default=None),
              input_sha256=sha(panel),states=latest,artifact_sha256=lock['artifact_sha256']))
    write_new(out/'report.json',dict(competition=competition,fit=artifact['fit_n'],selection=artifact['selection_n'],
              policy=artifact['policy'],input_sha256=sha(panel),**results))


def refresh(panel,competition,out):
    """Refit the locked recipe for new fixtures. Historical evaluations use the original trees."""
    rows=check_panel(panel,competition)
    lock=json.loads((out/'lock.json').read_bytes())
    history=json.loads((out/'history-state.json').read_bytes())
    if (lock['competition']!=competition or history['input_sha256']!=sha(panel)
            or history['artifact_sha256']!=lock['artifact_sha256']):
        raise ValueError('refresh requires the historically evaluated panel and original artifact')
    states={}
    examples=defaultdict(list)
    walk(rows,competition,states,{},fit_rows=examples)
    trees={stage:model.fit_trees(values) for stage,values in examples.items()}
    # A refit changes only the trees. The original online state already saw all prior dates.
    serialized={k:model.dump_state(s) for k,s in states.items()}
    if digest(serialized)!=digest(history['states']):
        raise ValueError('fixed-recipe refit changed historical online state')
    write_new(out/'refresh.json',dict(version=model.VERSION,competition=competition,through=history['through'],
        original_sha256=lock['artifact_sha256'],history_state_sha256=sha(out/'history-state.json'),
        panel_sha256=sha(panel),policy=lock['policy'],fit_counts={k:len(v) for k,v in examples.items()},
        tree_sizes={k:v[0].num_trees() if v else 0 for k,v in trees.items()},trees=encode_trees(trees)))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['fit','evaluate','refresh'])
    p.add_argument('--competition',required=True)
    p.add_argument('--panel',required=True,type=Path)
    p.add_argument('--out',required=True,type=Path)
    a=p.parse_args()
    {'fit': fit, 'evaluate': evaluate, 'refresh': refresh}[a.action](a.panel,a.competition,a.out)
