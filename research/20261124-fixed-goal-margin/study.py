"""Fit on earlier outcomes and seal both rankings without opening test results."""
import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path

import numpy as np

from inputs import HERE, PROTOCOL, canonical, digest, file_hash, load_rows, save, store_rows
from recipe import conditional_mass, geometry, fit, predict
from research_program.score_form import poisson_matrix
from research_program.three_arm_model import _matrix, score_picks


def anchor(row):
    if row.get('matrix') is not None:
        return _matrix(row['matrix'])
    rates=row.get('rates')
    return None if rates is None else poisson_matrix(np.asarray(rates,dtype=float),(31,31))


def top_indices(m):
    return np.argsort(-m.ravel(),kind='stable')


def signed_masses(m):
    index,_=geometry(m.shape)
    return np.bincount(index.ravel(),weights=m.ravel(),minlength=sum(m.shape)-1)


def checked_pair(m,beta):
    candidate=predict(m,beta)
    if not np.allclose(signed_masses(m), signed_masses(candidate), rtol=0,atol=1e-12):
        raise ValueError('a signed goal margin changed')
    return m,candidate


def picks(m,line):
    result=score_picks(m,line=line)
    order=top_indices(m)[:20]
    return dict(ranking=['%d-%d'%np.unravel_index(i,m.shape) for i in order],
                wdl=result['wdl'],wdl_probability=list(result['wdl_probabilities'].values()),
                total_side=result['total']['direction'] if result['total'] else None,
                matrix_sha256=sha256(np.ascontiguousarray(m.round(12),dtype='<f8')).hexdigest())


def development(path):
    if file_hash(path)!=PROTOCOL['train_bytes_sha256']:
        raise ValueError('wrong original pre-selected training projection')
    rows=load_rows(path)
    probs,indicators=[],[]
    ranks,errors=Counter(),Counter()
    observed,expected,cond_expected=Counter(),Counter(),Counter()
    for row in rows:
        base=anchor(row)
        if base is None or row.get('score') is None:
            continue
        h,a=row['score']
        rank_order=top_indices(base)
        rank=int(np.flatnonzero(rank_order==np.ravel_multi_index((h,a),base.shape))[0])+1
        ih,ia=np.unravel_index(rank_order[0],base.shape)
        if rank==1:
            category='exact'
        elif np.sign(ih-ia)==np.sign(h-a):
            category='score_miss_with_right_mode_direction'
        else:
            category='wrong_mode_direction'
        errors[category]+=1
        ranks.update({k:int(rank<=k) for k in (1,3,5,10,20)})
        idx,ones=geometry(base.shape)
        raw_diff=idx-(base.shape[1]-1)
        group=min(3,abs(h-a))
        target=int(min(h,a)==1)
        p=float(conditional_mass(base)[h-a+base.shape[1]-1])
        for margin in (0,1,2,3):
            choose=(np.abs(raw_diff)==margin) if margin<3 else (np.abs(raw_diff)>=3)
            expected[margin]+=float(base[choose & ones].sum())
        observed[group]+=target
        cond_expected[group]+=p
        probs.append(p)
        indicators.append(target)
    beta=fit(probs,indicators)
    mass={str(k):{'common_one_observed':observed[k],'unconditional_expected':expected[k],
                  'expected_given_actual_signed_margin':cond_expected[k]} for k in (0,1,2,3)}
    return dict(protocol_sha256=file_hash(HERE/'protocol.json'),training_sha256=file_hash(path),
                roster=len(rows),training_pairs=len(probs),beta=beta,odds_ratio=float(np.exp(beta)),
                development=dict(rank_at_most=ranks,mode_errors=errors,
                                 common_one_by_absolute_margin=mass,observed=sum(indicators),
                                 expected_given_actual_signed_margin=sum(probs)))


def seal(inputs,fitted,out,panel):
    fit_object=json.loads(fitted.read_bytes())
    if fit_object['protocol_sha256']!=file_hash(HERE/'protocol.json'):
        raise ValueError('protocol mismatch')
    rows=load_rows(inputs)
    ids=sorted(r['id'] for r in rows)
    if not ids or len(set(ids))!=len(ids):
        raise ValueError('duplicate or empty forecast roster')
    if out.exists() and any(out.iterdir()):
        raise FileExistsError('use an empty destination for a forecast lock')
    out.mkdir(exist_ok=True,parents=True)
    forecasts=[]
    for row in rows:
        if {'score','actual','observed_line','FTHG','FTAG'}.intersection(row):
            raise ValueError('outcome/settled market field in pregrading projection')
        entry={k:row[k] for k in ('id','league','date','line')}
        m=anchor(row)
        entry.update(base=None,candidate=None)
        if m is not None:
            old,new=checked_pair(m,fit_object['beta'])
            entry['base'],entry['candidate']=[picks(grid,row['line']) for grid in (old,new)]
            # A numerical tie in W/D/L retains the shared original W/D/L selection.
            entry['candidate']['wdl']=entry['base']['wdl']
        forecasts.append(entry)
    store_rows(out/'forecasts.jsonl',forecasts)
    lock=dict(panel=panel,n=len(ids),roster_sha256=digest(ids),n_issued=sum(f['base'] is not None for f in forecasts),
              inputs_sha256=file_hash(inputs),forecasts_sha256=file_hash(out/'forecasts.jsonl'),
              protocol_sha256=file_hash(HERE/'protocol.json'),fit_sha256=file_hash(fitted))
    save(out/'lock.json',lock)
    return lock


def read_lock(path,forecast,panel):
    lock=json.loads(Path(path).read_bytes())
    if (lock['panel']!=panel or lock['protocol_sha256']!=file_hash(HERE/'protocol.json')
            or lock['forecasts_sha256']!=file_hash(forecast)):
        raise ValueError('forecast/registration lock mismatch')
    rows=load_rows(forecast)
    ids=sorted(r['id'] for r in rows)
    if digest(ids)!=lock['roster_sha256'] or len(set(ids))!=len(ids) or len(ids)!=lock['n']:
        raise ValueError('sealed identity roster mismatch')
    return lock,rows


if __name__=='__main__':
    p=argparse.ArgumentParser()
    sub=p.add_subparsers(dest='mode',required=True)
    learn=sub.add_parser('fit')
    learn.add_argument('--train',type=Path,required=True)
    learn.add_argument('--out',type=Path,required=True)
    sealing=sub.add_parser('seal')
    for arg in ('inputs','fit','out'):
        sealing.add_argument('--'+arg,type=Path,required=True)
    sealing.add_argument('--panel',choices=('january','original'),required=True)
    args=p.parse_args()
    if args.mode=='fit':
        value=development(args.train)
        save(args.out,value)
    else:
        value=seal(args.inputs,args.fit,args.out,args.panel)
    print(canonical(value))
