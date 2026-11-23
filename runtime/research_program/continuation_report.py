"""Paired hit counts, date-cluster uncertainty and probability checks on frozen predictions."""
from collections import Counter, defaultdict
import hashlib

import numpy as np

from optimization.evaluate import settle_ou
from .all_scope_study import KPI
from .kpi_targets import ORIGINAL_TARGETS


def indicators(row,arm):
    p=row['forecast'].get(arm,{})
    h,a=row['actual'];exact=f'{h}-{a}';win='home' if h>a else 'away' if a>h else 'draw'
    vals=[exact==p['primary'] if p.get('primary') else None,
          exact in p['top3'] if p.get('top3') else None,win==p['wdl'] if p.get('wdl') else None,None]
    settlement=settle_ou(h+a,row['line'],p['total_side']) if p.get('total_side') else None
    if settlement not in (None,'PUSH'):
        vals[3]=settlement=='FULL_WIN'
    return vals,settlement


def bootstrap_days(daily):
    """Reproduce the registered 1,000 whole-date draws with bounded working memory."""
    rng = np.random.default_rng(20261123)
    totals = np.empty((1000, *daily.shape[1:]), dtype=daily.dtype)
    for start in range(0, len(totals), 32):
        batch = totals[start:start + 32]
        indices = rng.integers(len(daily), size=(len(batch), len(daily)))
        batch[:] = daily[indices].sum(axis=1)
    return totals


def rates(rows,arm):
    counts=np.zeros((4,2),int);settlements=Counter();loss=[];wdl_loss=[];pfirst=[]
    for r in rows:
        values,s=indicators(r,arm)
        if s:settlements[s]+=1
        for i,x in enumerate(values):
            if x is not None:
                counts[i]+=np.array([int(x),1])
        q=r.get('quality',{}).get(arm)
        if q:
            h,a=r['actual'];outcome=0 if h>a else 2 if a>h else 1
            loss.append(-np.log(max(1e-300,q['p_observed'])))
            wdl_loss.append(-np.log(max(1e-300,q['wdl'][outcome])))
            pfirst.append(q['p_first'])
    return dict(games=len(rows),counts=dict(zip(KPI,counts.tolist())),settlements=dict(settlements),
        score_log_loss=float(np.mean(loss)) if loss else None,wdl_log_loss=float(np.mean(wdl_loss)) if loss else None,
        mean_first_probability=float(np.mean(pfirst)) if pfirst else None)


def paired(rows,control,challenger):
    dates=defaultdict(lambda:np.zeros((4,4),int))
    for r in rows:
        old,_=indicators(r,control);new,_=indicators(r,challenger)
        for i,(a,b) in enumerate(zip(old,new)):
            if a is not None and b is not None:
                dates[r['date']][i]+=[int(a),int(b),1,int(a!=b)]
    if not dates:return {}
    ds=np.array([dates[k] for k in sorted(dates)])
    total=ds.sum(axis=0);boot=bootstrap_days(ds)
    result={}
    for i,name in enumerate(KPI):
        old,new,n,diff=map(int,total[i])
        legal=boot[:,i,2]>0
        ci=None
        if legal.any():
            delta=(boot[legal,i,1]-boot[legal,i,0])/boot[legal,i,2]
            ci=(100*np.quantile(delta,[.025,.975])).tolist()
        result[name]=dict(paired=n,control_hits=old,candidate_hits=new,net=new-old,disagreements=diff,delta_pp_ci95=ci)
    return result


def result(rows):
    arms=sorted({a for r in rows for a in r['forecast']})
    def summarize(group):
        scores={a:rates(group,a) for a in arms}
        comparisons={}
        if 'candidate' in arms:
            comparisons={a:paired(group,a,'candidate') for a in ('anchor','r6','market') if a in arms}
        if 'past_network' in arms:
            comparisons={a:paired(group,a,'past_network') for a in ('score_only','past_volume')}
        return dict(scores=scores,comparisons=comparisons)
    groups=defaultdict(list)
    for r in rows:groups[r['cohort']].append(r)
    by={code:summarize(group) for code,group in sorted(groups.items())}
    overall=summarize(rows)
    if 'candidate' in arms:
        def attained(report):
            counts = report['scores']['candidate']['counts']
            return all(counts[k][1] and counts[k][0] / counts[k][1] >= ORIGINAL_TARGETS[k] for k in KPI)
        overall['all_four_targets_met']=[c for c,v in by.items() if attained(v)]
    return dict(overall=overall,competitions=by,
        fixture_set_sha256=hashlib.sha256('\n'.join(sorted(r['cohort']+'|'+r['id'] for r in rows)).encode()).hexdigest())


def collect(root):
    """Read complete competition artifacts; keep test windows and different pricing horizons separate."""
    from .continuation_study import read_gzip
    grouped=defaultdict(list)
    for mode in ('main','early','additional','pre2010','t24','t1','passing'):
        for path in sorted((root/mode).rglob('forecasts.jsonl.gz')):
            for row in read_gzip(path):
                grouped[row['window']].append(row)
    return dict(grouped)


if __name__=='__main__':
    import argparse,json
    from pathlib import Path
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--studies',type=Path,required=True);ap.add_argument('--out',type=Path,required=True)
    a=ap.parse_args()
    scores={w:result(rows) for w,rows in collect(a.studies).items()}
    a.out.write_text(json.dumps(scores,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
