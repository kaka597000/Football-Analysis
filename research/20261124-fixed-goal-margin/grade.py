"""Grade the sealed ranking on identical fixture IDs using repository indicators."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

import numpy as np

from inputs import canonical, file_hash, load_rows, save
from study import anchor, checked_pair, picks, read_lock, signed_masses, top_indices
from research_program.continuation_report import indicators


METRICS=('first','matrix_three','wdl','total_full_win')
SETTLEMENTS=('FULL_WIN','HALF_WIN','PUSH','HALF_LOSS','FULL_LOSS')


def evaluate(entry,inputs,label,beta):
    actual=label['score']
    if not isinstance(actual,list) or len(actual)!=2 or any(type(x) is not int or x<0 for x in actual):
        raise ValueError('regulation score requires two nonnegative integers')
    h,a=actual
    m=anchor(inputs)
    if h>=m.shape[0] or a>=m.shape[1]:
        raise ValueError('score outside retained matrix support')
    line=label.get('observed_line')
    if line is not None and line!=entry['line']:
        raise ValueError('different original and graded total lines')
    record={'actual':actual,'line':line,'forecast':{}}
    result={'id':entry['id'],'date':entry['date'],'league':entry['league']}
    for key,g in zip(('base','candidate'),checked_pair(m,beta)):
        prediction=picks(g,entry['line'])
        if key=='candidate':
            prediction['wdl']=entry['base']['wdl']
        if prediction!=entry[key]:
            raise ValueError('reconstructed score/rank bytes do not match sealed forecast')
        top=prediction['ranking']
        record['forecast'][key]=dict(primary=top[0],top3=top[:3],wdl=prediction['wdl'],
                                     total_side=prediction['total_side'] if line is not None else None)
        scores,settle=indicators(record,key)
        idx=np.ravel_multi_index((h,a),g.shape)
        rank=int(np.flatnonzero(top_indices(g)==idx)[0])+1
        margin_mass=signed_masses(g)[h-a+g.shape[1]-1]
        primary_home,primary_away=map(int,top[0].split('-'))
        result[key]=dict(values=scores,settlement=settle,exact_rank=rank,
                         picks=[top[0],top[:3],prediction['wdl'],record['forecast'][key]['total_side']],
                         loss=float(-np.log(max(1e-300,g[h,a]))),
                         conditional_loss=float(-np.log(max(1e-300,g[h,a]/margin_mass))) if margin_mass else None,
                         mode_wrong_sign=bool(np.sign(primary_home-primary_away)!=np.sign(h-a)))
    return result


def summary(roster,scored):
    metrics={}
    day_counts=defaultdict(lambda:np.zeros(2,dtype=int))
    for row in scored:
        net=int(row['candidate']['values'][0])-int(row['base']['values'][0])
        day_counts[row['date']]+=[1,net]
    ci=None
    if len(day_counts)>=2:
        groups=np.asarray([day_counts[k] for k in sorted(day_counts)])
        sample=np.random.default_rng(20261124).integers(len(groups),size=(2000,len(groups)))
        n,net=groups[sample].sum(axis=1).T
        ci=np.quantile(100*net/n,[.025,.975]).tolist()
    for index,metric in enumerate(METRICS):
        pairs=[r for r in scored if all(r[a]['values'][index] is not None for a in ('base','candidate'))]
        bits=[(r['base']['values'][index],r['candidate']['values'][index]) for r in pairs]
        metrics[metric]=dict(n=len(pairs),base=sum(a for a,b in bits),candidate=sum(b for a,b in bits),
                             gained=sum(not a and b for a,b in bits),lost=sum(a and not b for a,b in bits),
                             changed=sum(r['base']['picks'][index]!=r['candidate']['picks'][index] for r in pairs))
    quality={}
    for arm in ('base','candidate'):
        values=[r[arm] for r in scored]
        settlements=Counter(r['settlement'] for r in values)
        exact_ranks=[r['exact_rank'] for r in values]
        conditional=[r['conditional_loss'] for r in values if r['conditional_loss'] is not None]
        quality[arm]=dict(
            score_log_loss=float(np.mean([r['loss'] for r in values])) if values else None,
            conditional_score_log_loss=float(np.mean(conditional)) if conditional else None,
            median_actual_rank=float(np.median(exact_ranks)) if values else None,
            rank_at_most={k:sum(v<=k for v in exact_ranks) for k in (1,3,5,10,20)},
            mode_wrong_sign=sum(r['mode_wrong_sign'] for r in values),
            settlements={k:settlements[k] for k in SETTLEMENTS},
        )
    return dict(roster=len(roster),predicted=sum(r['base'] is not None for r in roster),
                scored=len(scored),dates=len(day_counts),metrics=metrics,quality=quality,first_delta_pp_ci95=ci)


def grade(panel,inputs_path,fit_path,forecast_path,lock_path,labels_path):
    lock,forecasts=read_lock(lock_path,forecast_path,panel)
    if file_hash(inputs_path)!=lock['inputs_sha256'] or file_hash(fit_path)!=lock['fit_sha256']:
        raise ValueError('changed original inputs or coefficient')
    inputs={r['id']:r for r in load_rows(inputs_path)}
    labels=load_rows(labels_path)
    targets={r['id']:r for r in labels}
    if len(targets)!=len(labels) or set(targets)!=set(inputs):
        raise ValueError('complete original label roster required, use null for missing evidence')
    beta=json.loads(fit_path.read_bytes())['beta']
    results=[evaluate(f,inputs[f['id']],targets[f['id']],beta)
             for f in forecasts if f['base'] is not None and targets[f['id']]['score'] is not None]
    def grouped(field):
        def key(r):
            return r['date'][:4] if field=='year' else r['league']
        return {v:summary([r for r in forecasts if key(r)==v],[r for r in results if key(r)==v])
                for v in sorted({key(r) for r in forecasts})}
    return dict(panel=panel,overall=summary(forecasts,results),years=grouped('year'),leagues=grouped('league'),
                forecasts_sha256=lock['forecasts_sha256'],labels_sha256=file_hash(labels_path),
                actual_delivered_pairs={'first':0,'ordered_three':0,'preselected_three_batches':0})


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--panel',required=True,choices=('january',))
    for arg in ('inputs','fit','forecasts','lock','labels','out'):
        p.add_argument('--'+arg,required=True,type=Path)
    a=p.parse_args()
    output=grade(a.panel,a.inputs,a.fit,a.forecasts,a.lock,a.labels)
    save(a.out,output)
    print(canonical(output['overall']))
