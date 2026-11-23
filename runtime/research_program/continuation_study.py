"""Fit and replay a frozen distribution recipe on identical competition-specific fixtures."""
import argparse
from datetime import date
from itertools import groupby
from pathlib import Path
import gzip
import json

import numpy as np

from . import all_scope_model as base
from . import all_scope_study as old
from . import conditional_scores as r6
from . import continuation_scores as model
from . import score_gap_study as previous
from .goal_state_study import read_lines, sha, write_new
from .score_gap_first import decisions


def read_gzip(path):
    with gzip.open(path,'rt',encoding='utf8') as stream:
        return [json.loads(line) for line in stream]


def put(path,rows):
    if path.exists():
        raise FileExistsError(path)
    with path.open('xb') as out, gzip.GzipFile(filename='',mode='wb',fileobj=out,mtime=0) as stream:
        for row in rows:
            stream.write((json.dumps(row,ensure_ascii=False,allow_nan=False,separators=(',',':'))+'\n').encode())


def r6_refit(games,code,bounds,observations):
    """Apply the archived R5 and R6 rules afresh when this window has no issued comparator."""
    reference,_=previous.refit_r5(games,code,bounds)
    state=r6.blank();examples={'goals':[],'process':[]}
    result={};beta=None;hl=old.half_life(code)
    for stamp,group in groupby(games,key=lambda g:g['date']):
        day=date.fromisoformat(stamp).toordinal();group=list(group)
        r6.advance(state,group,day,hl)
        inputs=[(g,r6.components(state,g,day,hl)) for g in group]
        if stamp>=bounds[0] and beta is None:
            beta=previous._weights(examples)
        for g,p in inputs:
            if stamp<bounds[0]:
                y=[g['home_goals'],g['away_goals']]
                examples['goals'].append(r6.example(p['structural'],p['goal_signal'],y))
                examples['process'].append(r6.example(p['process_anchor'],p['signal'],y))
            if bounds[1]<=stamp<bounds[2]:
                picks,quality=r6.forecast(g,p,beta)
                picks['r5']=reference[g['id']]['forecast']['candidate']
                result[g['id']]=decisions(picks,quality)['agreement']
        r6.update(state,inputs,observations,day,hl)
    return result


def unpack_controls(folder):
    forecasts=previous.index(read_lines(folder/'predictions.jsonl'))
    decisions_by_id=previous.index(read_lines(folder/'first-policy.jsonl'))
    if forecasts.keys()!=decisions_by_id.keys():
        raise ValueError('R6 controls have different match sets')
    return {mid:(r,decisions_by_id[mid]['forecast']['agreement']) for mid,r in forecasts.items()}


def market_control(game,prior):
    hp=base._prices(game.get('h2h'),base.SIDES)
    ou=base._prices(game.get('total'),('over','under'))
    line=base.actual_line(game)
    q=dict(kind='paired' if hp and ou and game.get('paired_market') else 'partial',
           hprob=base._wdl(hp) if hp else None,over=ou[1]/sum(ou) if ou and line is not None else None,line=line)
    return base.market_picks(q,game,{'anchor':base.pick(prior,line)})


def evaluate_matrices(games,prior,alternatives,policy):
    n=len(games);ix=np.arange(n)
    h,a=np.array([[g['home_goals'],g['away_goals']] for g in games]).T
    chosen=(1-policy['mix'])*prior+policy['mix']*alternatives[policy['arm']]
    blended={arm:(m+prior)*.5 for arm,m in alternatives.items()}
    all_matrices={'anchor':prior,'linear_half':blended['linear'],'tree_half':blended['tree'],'candidate':chosen}
    outputs=[dict(forecast={},quality={}) for _ in games]
    for arm,ms in all_matrices.items():
        for i,m in enumerate(ms):
            g=games[i];picks=base.pick(m,base.actual_line(g))
            outputs[i]['forecast'][arm]=picks
            outputs[i]['quality'][arm]=dict(p_observed=float(m[h[i],a[i]]),
                p_first=float(m.max()),p_three=float(np.partition(m.ravel(),-3)[-3:].sum()),
                wdl=[float(m[mask].sum()) for mask in (base.H>base.A,base.H==base.A,base.H<base.A)])
    for i in ix:
        outputs[i]['forecast']['market']=market_control(games[i],prior[i])
    return outputs


def run(panel,code,out,*,bounds,mode,control=None,totals=None,observations=None):
    rows=old.check_panel(panel,code)
    if totals:
        previous.add_totals(rows,totals)
    if mode!='main':
        rows=[g for g in rows if g['date']<bounds[2]]
    idx0=sum(g['date']<bounds[0] for g in rows)
    idx1=sum(g['date']<bounds[1] for g in rows)
    if not rows[idx1:]:
        raise ValueError('missing test window for '+code)
    scores=np.array([[g['home_goals'],g['away_goals']] for g in rows])
    matrices,features,state=model.inputs(rows,code)
    fitted=model.fit(model.hazard_inputs(matrices[:idx0],features[:idx0]),scores[:idx0])
    val=matrices[idx0:idx1]
    alternatives=model.arm_matrices(val,features[idx0:idx1],fitted) if len(val) else {}
    policy=model.choose(val,alternatives,scores[idx0:idx1])
    test=rows[idx1:];prior=matrices[idx1:]
    alternatives=model.arm_matrices(prior,features[idx1:],fitted)
    predictions=evaluate_matrices(test,prior,alternatives,policy)
    if control:
        controls=unpack_controls(control)
    elif mode in ('additional','pre2010'):
        refit=r6_refit(rows,code,bounds,observations or {})
        controls={mid:(None,p) for mid,p in refit.items()}
    else:
        controls=None
    records=[]
    for g,result in zip(test,predictions):
        y=[g['home_goals'],g['away_goals']];line=base.actual_line(g)
        window=('evaluation' if g['date']<bounds[2] else 'recent') if mode=='main' else mode
        if controls is not None:
            ref,pick=controls[g['id']]
            if ref is not None and (y!=ref['actual'] or line!=ref['line'] or ref['stage']!=g['stage']
                    or ref['home']!=g['home'] or ref['away']!=g['away'] or ref['window']!=window):
                raise ValueError('identity, label or market line differs across candidate/control')
            result['forecast']['r6']=pick
        records.append(dict(id=g['id'],cohort=code,window=window,stage=g['stage'],date=g['date'],
                            home=g['home'],away=g['away'],actual=y,line=line,
                            source_sha256=g['source_sha256'],**result))
    if controls is not None and {r['id'] for r in records}!=controls.keys():
        raise ValueError('candidate/control coverage differs')
    out.mkdir(parents=True,exist_ok=True)
    put(out/'forecasts.jsonl.gz',records)
    metadata=dict(version=model.VERSION,competition=code,bounds=bounds,training_games=idx0,
                  training_through=rows[idx0-1]['date'] if idx0 else None,selection=policy,source_panel_sha256=sha(panel))
    if mode in ('additional','pre2010'):
        metadata['r6_process_matches']=sum(g['id'] in (observations or {}) and
            r6.process_proxy(observations[g['id']]) is not None for g in rows)
    else:
        metadata['refit_without_process']=False
    metadata['training']=fitted
    write_new(out/'fit.json',metadata)
    if mode=='main':
        final=model.fit(model.hazard_inputs(matrices,features),scores)
        write_new(out/'forward.json',dict(version=model.VERSION,competition=code,through=rows[-1]['date'],
            n=len(rows),model=final,policy=policy,state=model.dump(state),fit_sha256=sha(out/'fit.json')))
    return dict(rows=len(records),fit=idx0,validation=idx1-idx0,choice=policy)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('panel','out','control','totals','process'):
        p.add_argument('--'+key,type=Path,required=key in ('panel','out'))
    p.add_argument('--code',required=True)
    p.add_argument('--mode',choices=['main','early','additional','pre2010','t24','t1'],required=True)
    a=p.parse_args()
    calendars=dict(main=old.boundaries(a.code),early=previous.early_bounds(a.code),
        additional=('2012-07-01','2013-07-01','2014-07-01'),pre2010=('2008-07-01','2009-07-01','2010-07-01'),
        t24=('2022-07-01','2023-07-01','2026-07-01'),t1=('2022-07-01','2023-07-01','2026-07-01'))
    totals=previous.index(read_lines(a.totals)) if a.totals else None
    observations=previous.index(read_lines(a.process)) if a.process else None
    print(json.dumps(run(a.panel,a.code,a.out,bounds=calendars[a.mode],mode=a.mode,control=a.control,totals=totals,observations=observations)))
