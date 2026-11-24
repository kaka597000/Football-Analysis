"""Fit the registered coefficient, seal forecasts, then grade their fixed identity."""

import argparse
from collections import Counter, defaultdict
import csv
import io
from datetime import date
import hashlib
import json
from math import log
from pathlib import Path

import numpy as np

from optimization.evaluate import settle_ou
from research_program.halftime_balance import (TeamHistory, adjusted, baseline, coordinates, fit,
                                               picks, training_example)
from history import HERE, PROTOCOL, historical_labels, packed, projected_games, sha, store

REGISTRATION = sha(HERE/'protocol.json')
ARMS = ('baseline','candidate')
METRICS = ('first','top3','wdl','total')


def rows(path):
    return [json.loads(line) for line in path.read_bytes().splitlines()]


def jsonout(path, value):
    store(path, (json.dumps(value,indent=2,sort_keys=True,allow_nan=False)+'\n').encode())


def fit_past(archive, out):
    samples = []
    counts = Counter()
    by_league = Counter()
    dates = []
    for r in projected_games(archive,training=True):
        counts['listed'] += 1
        if r['feature_status'] != 'available':
            counts['feature_gap'] += 1
        elif not r['market']:
            counts['market_gap'] += 1
        elif r['label'] is None:
            counts['outcome_gap'] += 1
        else:
            samples.append(training_example(baseline(r['market']),r['signal'],r['label']))
            dates.append(r['day'])
            by_league[r['league']] += 1
    artifact = dict(protocol_sha256=REGISTRATION,beta=fit(samples),eligible=len(samples),counts=dict(counts),
                    by_league=dict(by_league),first_fit_day=min(dates),last_fit_day=max(dates))
    if artifact['last_fit_day'] >= '2023-02-01':
        raise ValueError('fit overlaps evaluation')
    jsonout(out/'fit.json',artifact)
    print(artifact,flush=True)


def clocked_features(path):
    for r in rows(path):
        if r.get('feature_status')=='eligible_inputs':
            history = TeamHistory()
            for day,home,away,ht,ft in r.pop('prior_games'):
                history.observe(date.fromisoformat(day),home,away,tuple(ht) if ht else None,tuple(ft) if ft else None)
            r['signal'], r['feature_status'] = history.signal(date.fromisoformat(r['day']), *r.pop('clubs'))
        else:
            r['signal']=0.
        yield r


def matrix_hash(matrix):
    return hashlib.sha256(np.ascontiguousarray(matrix,dtype='<f8').tobytes()).hexdigest()


def forecasts(r, beta):
    a = baseline(r['market'])
    b = adjusted(a, r['signal'], beta)
    totals,_ = coordinates(a.shape)
    pa = np.bincount(totals,weights=a.ravel())
    pb = np.bincount(totals,weights=b.ravel())
    if not np.allclose(pa,pb,rtol=0,atol=2e-14) or not np.array_equal(a==0,b==0):
        raise ValueError('total probabilities or score support changed')
    return a,b


def prepare(archive, clocked_path, artifact_path, out):
    artifact = json.loads(artifact_path.read_bytes())
    if artifact['protocol_sha256'] != REGISTRATION:
        raise ValueError('unexpected fit protocol')
    population = list(projected_games(archive,training=False)) + list(clocked_features(clocked_path))
    ids=set()
    for r in population:
        if r['id'] in ids or 'label' in r or r['day'] <= artifact['last_fit_day']:
            raise ValueError('duplicate target, target label or overlap with fit')
        ids.add(r['id'])
        if r['market'] is None:
            r['status']=r['feature_status'] if r['feature_status'] in (
                'outside_regular_phase','missing_bound_original','excluded_R7') else 'no_verified_market'
            continue
        r['status']='paired'
        for arm,m in zip(ARMS,forecasts(r,artifact['beta'])):
            r[arm]=dict(picks(m,r['market']['total']['line']),sha256=matrix_hash(m))
        if r['baseline']['total'] != r['candidate']['total']:
            raise ValueError('full-win direction at the same line changed')
    data = b''.join(packed(r) for r in sorted(population,key=lambda r:(r['window'],r['day'],r['id'])))
    store(out/'forecasts.jsonl',data)
    lock=dict(fit_sha256=sha(artifact_path),protocol_sha256=REGISTRATION,clocked_inputs_sha256=sha(clocked_path),
              forecasts_sha256=hashlib.sha256(data).hexdigest(),listed=len(population),
              paired=sum(r['status']=='paired' for r in population))
    jsonout(out/'forecast-lock.json',lock)
    print(lock,flush=True)


def interval(pairs, metric):
    by_day=defaultdict(lambda: [0.,0])
    for r,b,c in pairs:
        if b[metric] is not None and c[metric] is not None:
            item=by_day[r['day']]
            item[0]+=float(c[metric])-float(b[metric])
            item[1]+=1
    if len(by_day)<5:
        return None
    data=np.array(list(by_day.values()))
    ids=np.random.default_rng(20261124).integers(len(data),size=(2000,len(data)))
    sampled=data[ids].sum(axis=1)
    return np.quantile(sampled[:,0]/sampled[:,1],[.025,.975]).tolist()


def summary(pairs, full=False):
    out={'n':len(pairs)}
    for metric in METRICS:
        counted=[p for p in pairs if p[1][metric] is not None and p[2][metric] is not None]
        out[metric]=dict(n=len(counted),baseline=sum(b[metric] for _,b,_ in counted),
                         candidate=sum(c[metric] for _,_,c in counted),
                         gained=sum(c[metric] and not b[metric] for _,b,c in counted),
                         lost=sum(b[metric] and not c[metric] for _,b,c in counted),
                         changed=sum(r['baseline'][metric]!=r['candidate'][metric] for r,_,_ in counted))
        if full:
            out[metric]['delta_95']=interval(pairs,metric)
    out['total']['settlements']={arm:dict(Counter(p[i]['settlement'] for p in pairs)) for i,arm in enumerate(ARMS,1)}
    out['log_loss']={arm:float(np.mean([p[i]['log_loss'] for p in pairs])) if pairs else None
                     for i,arm in enumerate(ARMS,1)}
    if full:
        out['log_loss']['delta_95']=interval(pairs,'log_loss')
    return out


def coverage_csv(windows):
    data = io.StringIO(newline='')
    writer = csv.writer(data, lineterminator='\n')
    writer.writerow(['window','competition','listed','paired','feature_available',
                     'first_baseline','first_candidate','first_gained','first_lost',
                     'matrix_top3_baseline','matrix_top3_candidate','wdl_baseline','wdl_candidate',
                     'totals_nonpush','full_wins_baseline','full_wins_candidate'])
    for window, summary in windows.items():
        for league, group in summary['by_league'].items():
            m = group['metrics']
            fields = (m['first']['baseline'], m['first']['candidate'], m['first']['gained'], m['first']['lost'],
                      m['top3']['baseline'], m['top3']['candidate'], m['wdl']['baseline'], m['wdl']['candidate'],
                      m['total']['n'], m['total']['baseline'], m['total']['candidate']) if m else ['']*11
            writer.writerow([window, league, group['listed'], group['graded'], group['eligible_signals'], *fields])
    return data.getvalue().encode()


def grade(archive, label_path, run, artifact_path, lockpath, out):
    artifact=json.loads(artifact_path.read_bytes())
    lock=json.loads(lockpath.read_bytes())
    if (sha(run/'forecasts.jsonl')!=lock['forecasts_sha256'] or sha(artifact_path)!=lock['fit_sha256']
            or REGISTRATION!=lock['protocol_sha256'] or artifact['protocol_sha256']!=REGISTRATION):
        raise ValueError('frozen predictions, fit or study identity mismatch')
    labels=historical_labels(archive)
    latest={r['id']:r for r in rows(label_path)}
    population=rows(run/'forecasts.jsonl')
    if set(latest)!={r['id'] for r in population if r['window']=='clocked'}:
        raise ValueError('clocked outcome roster differs from target roster')
    results={}
    for r in population:
        if r['status']!='paired':
            continue
        if r['window']=='clocked':
            label=latest[r['id']]
            if label['identity']!=r['identity'] or label['freeze_sha256']!=r['freeze_sha256']:
                raise ValueError('clocked result binding disagrees with input')
            score=label['score']
        else:
            score=labels[r['id']]
        if score is None:
            continue
        h,a=score
        truth=f'{h}-{a}'
        wdl='home' if h>a else ('away' if a>h else 'draw')
        arms=[]
        for arm,m in zip(ARMS,forecasts(r,artifact['beta'])):
            p=r[arm]
            if matrix_hash(m)!=p['sha256']:
                raise ValueError('forecast recreation differs from original model')
            settlement=settle_ou(h+a,r['market']['total']['line'],p['total'])
            prob=m[h,a] if h<m.shape[0] and a<m.shape[1] else 0.
            arms.append(dict(first=p['first']==truth,top3=truth in p['top3'],wdl=p['wdl']==wdl,
                             total=None if settlement=='PUSH' else settlement=='FULL_WIN',
                             settlement=settlement,log_loss=-log(max(float(prob),1e-300))))
        results[r['id']]=(r,*arms)

    scopes=[x['code'] for x in json.loads((HERE.parents[1]/'configs/analysis-scope.json').read_bytes())['competitions']]
    windows={}
    for window in ('historical','clocked'):
        group=[r for r in population if r['window']==window]
        pairs=[results[r['id']] for r in group if r['id'] in results]
        win={'listed':len(group),'graded':len(pairs),'prediction_status':dict(Counter(r['status'] for r in group)),
             'feature_status':dict(Counter(r['feature_status'] for r in group)),
             'all':summary(pairs,True),'feature_available':summary([p for p in pairs if p[0]['feature_status']=='available']),
             'by_league':{}}
        for league in scopes:
            roster=[r for r in group if r['league']==league]
            local=[results[r['id']] for r in roster if r['id'] in results]
            win['by_league'][league]={'listed':len(roster),'eligible_signals':sum(p[0]['feature_status']=='available' for p in local),
                                      'graded':len(local),'metrics':summary(local) if local else None}
        windows[window]=win
    hist=windows['historical']['all']
    gate={'at_least_500':hist['n']>=500,'first_lower_above_zero':hist['first']['delta_95'] is not None and hist['first']['delta_95'][0]>0,
          'top3_nonnegative':hist['top3']['candidate']>=hist['top3']['baseline'],
          'log_loss_nonworse':hist['log_loss']['candidate']<=hist['log_loss']['baseline']}
    output=dict(protocol=REGISTRATION,windows=windows,gate=gate,fit=artifact,
                decision='eligible_for_new_future_consideration' if all(gate.values()) else 'retire_recipe',
                delivered_pairs={'first':0,'ordered_three':0,'selected_batch':0})
    jsonout(out/'results.json',output)
    store(out/'coverage.csv',coverage_csv(windows))
    print(output['decision'],{w:v['graded'] for w,v in windows.items()},flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='job',required=True)
    for choice in ('fit','prepare','grade'):
        s=sub.add_parser(choice)
        s.add_argument('--archive',type=Path,required=True)
        s.add_argument('--out',type=Path,required=True)
        if choice in ('prepare','grade'):
            s.add_argument('--fit',type=Path,required=True)
        if choice=='prepare':
            s.add_argument('--clocked-inputs',type=Path,required=True)
        if choice=='grade':
            for opt in ('labels','run','lock'):
                s.add_argument('--'+opt,type=Path,required=True)
    a=p.parse_args()
    if a.job=='fit':
        fit_past(a.archive,a.out)
    elif a.job=='prepare':
        prepare(a.archive,a.clocked_inputs,a.fit,a.out)
    else:
        grade(a.archive,a.labels,a.run,a.fit,a.lock,a.out)
