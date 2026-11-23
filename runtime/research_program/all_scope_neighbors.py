"""Local, time-weighted score-cell corrections from similar earlier fixtures in the same phase."""
import argparse
from collections import defaultdict
from datetime import date
from itertools import groupby
import json
from pathlib import Path

import numpy as np

from . import all_scope_model as model
from . import all_scope_study as study
from .goal_state_process import combine_heads
from .goal_state_study import read_lines, sha, write_new
from .paired_kpi_inference import compare

VERSION='WCPP-NEIGHBOR-SCORE-1'
SCALES=np.array([.22,.065,.65])
PRIOR=36
NEIGHBORS=160
PAIR=('original','local')


def context(prepared):
    matrix=prepared['anchor']
    wdl=np.bincount(model.RESULT.ravel(),weights=matrix.ravel(),minlength=3)
    return np.array([wdl[0]-wdl[2],wdl[1],(matrix*(model.H+model.A)).sum()])


class Memory:
    """Cache a contiguous array of settled prior rows for one phase/venue per matchday."""
    def __init__(self,rows=()):
        self.rows=list(rows)
        self.array=None

    def append(self,day,features,goal_cell,expected):
        self.rows.append([day,*features.tolist(),goal_cell,*expected.tolist()])
        self.array=None

    def matrix(self,prepared,day,half_life):
        if not self.rows:
            return prepared['anchor']
        if self.array is None:
            self.array=np.asarray(self.rows,float)
        past=self.array
        if past[-1,0]>=day:
            raise ValueError('neighborhood contains an unsettled matchday')
        d2=np.square((past[:,1:4]-context(prepared))/SCALES).sum(axis=1)
        # Stable ordering preserves chronological/source-id order for exact distance ties.
        idx=np.argsort(d2,kind='stable')[:NEIGHBORS]
        weights=np.exp(-.5*d2[idx])*.5**((day-past[idx,0])/half_life)
        obs=np.bincount(past[idx,4].astype(int),weights=weights,minlength=25)
        expected=weights@past[idx,5:]
        prior=PRIOR*np.maximum(prepared['cell'],1e-7)
        ratio=np.clip((obs+prior)/(expected+prior),.5,1.8)
        out=prepared['anchor']*ratio[model.CELL.reshape(prepared['anchor'].shape)]
        return out/out.sum()


def memory_key(stage,neutral):
    return stage+'|'+('neutral' if neutral is True else 'venue')


def predict(memories,stage,neutral,prepared,day,half_life):
    m=memories.get(memory_key(stage,neutral))
    return model.pick(prepared['anchor'] if m is None else m.matrix(prepared,day,half_life),prepared['line'])


def walk(rows,competition,states,memories,records=None,policy=None,original_policy=None):
    hl=study.half_life(competition)
    forecasts=[]
    for stamp,games in groupby(rows,key=lambda r:r['date']):
        day=date.fromisoformat(stamp).toordinal()
        phases=defaultdict(list)
        for g in games:
            phases[g['stage']].append(g)
        for stage,group in phases.items():
            state=states.setdefault(stage,model.blank_state())
            model.advance(state,day,hl)
            prepared=[model.prepare(state,g,day,hl) for g in group]
            for g,p in zip(group,prepared):
                if records is None:
                    continue
                ref=records[g['id']]
                old=ref['forecast'].get('candidate') or combine_heads(ref['forecast'],original_policy)
                picks=dict(original=old,local=predict(memories,stage,g.get('neutral'),p,day,hl),
                           market=ref['forecast']['market'],team=ref['forecast']['team'])
                if policy is not None:
                    picks['candidate']=combine_heads(picks,policy)
                forecasts.append(dict(id=g['id'],cohort=competition,stage=stage,date=stamp,home=g['home'],
                    away=g['away'],actual=[g['home_goals'],g['away_goals']],line=p['line'],forecast=picks))
            for g,p in zip(group,prepared):
                mem=memories.setdefault(memory_key(stage,g.get('neutral')),Memory())
                mem.append(day,context(p),min(g['home_goals'],4)*5+min(g['away_goals'],4),p['cell'])
            model.update_day(state,[(g,p['team'],p['cell']) for g,p in zip(group,prepared)],day,hl)
    return forecasts


def record_map(path):
    return {g['id']:g for g in read_lines(path)}


def fit(panel,competition,out):
    rows=study.check_panel(panel,competition)
    base=json.loads((out/'lock.json').read_bytes())
    start,cut,_=study.boundaries(competition)
    train=[r for r in rows if r['date']<start]
    val=[r for r in rows if start<=r['date']<cut]
    memories={}
    states={}
    walk(train,competition,states,memories)
    selection=walk(val,competition,states,memories,record_map(out/'selection.jsonl'),original_policy=base['policy'])
    policy,counts=study.choose(selection,arms=PAIR)
    write_new(out/'neighbor-artifact.json',dict(version=VERSION,competition=competition,original_sha256=base['artifact_sha256'],
              fit_selection_sha256=base['fit_selection_sha256'],policy=policy,memories={k:m.rows for k,m in memories.items()}))
    write_new(out/'neighbor-lock.json',dict(competition=competition,version=VERSION,policy=policy,validation_head_hits=counts,
              validation_n=len(val),artifact_sha256=sha(out/'neighbor-artifact.json'),original_sha256=base['artifact_sha256']))


def evaluate(panel,competition,out):
    rows=study.check_panel(panel,competition)
    base=json.loads((out/'artifact.json').read_bytes())
    artifact=json.loads((out/'neighbor-artifact.json').read_bytes())
    lock=json.loads((out/'neighbor-lock.json').read_bytes())
    if (artifact['original_sha256']!=sha(out/'artifact.json') or lock['artifact_sha256']!=sha(out/'neighbor-artifact.json')
            or base['competition']!=competition):
        raise ValueError('different locked challenger')
    states={k:model.load_state(s) for k,s in base['states'].items()}
    memories={k:Memory(v) for k,v in artifact['memories'].items()}
    _,cut,stop=study.boundaries(competition)
    report={}
    for label,period in [('evaluation',[r for r in rows if cut<=r['date']<stop]),('recent',[r for r in rows if r['date']>=stop])]:
        predictions=walk(period,competition,states,memories,record_map(out/(label+'.jsonl')),policy=artifact['policy'])
        study.store_lines(out/('neighbor-'+label+'.jsonl'),predictions)
        result=dict(population=len(predictions),metrics={n:study._score(predictions,n) for n in ('candidate','original','local','market','team')})
        for tag in ('original','market','team'):
            if not predictions:
                result['vs_'+tag]={}
                continue
            paired=compare(predictions,baseline=tag,candidate='candidate')
            result['vs_'+tag]={k:{n:v[n] for n in ('eligible','gained','lost','net_hits','date_cluster_ci')} for k,v in paired['paired'].items()}
        report[label]=result
    write_new(out/'neighbor-history.json',dict(competition=competition,panel_sha256=sha(panel),policy=artifact['policy'],
              memories={k:m.rows for k,m in memories.items()}))
    write_new(out/'neighbor-report.json',report)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['fit','evaluate'])
    p.add_argument('--competition',required=True)
    p.add_argument('--panel',required=True,type=Path)
    p.add_argument('--out',required=True,type=Path)
    a=p.parse_args()
    {'fit':fit,'evaluate':evaluate}[a.action](a.panel,a.competition,a.out)
