"""Summarize all registered fixtures, separate matched controls and process ablations."""
import argparse
from collections import Counter
from pathlib import Path
import json
import numpy as np

from optimization.evaluate import settle_ou
from . import all_scope_study as study
from .goal_state_study import read_lines,write_new
from .paired_kpi_inference import compare
from .score_gap_first import decisions

ARMS=('r5','local_anchor','structural','no_process','conditional','market','price_pair_proxy')
TARGETS=(.20,.40,.65,.70)


def summarize(rows, target_n=None):
    for r in rows:
        r['forecast'].update(decisions(r['forecast'],r['quality']))
    all_scores={arm:study._score(rows,arm) for arm in (*ARMS,'retained_first','agreement')}
    pairs={}
    for control in ARMS:
        if control=='conditional' or not rows:
            continue
        result=compare(rows,baseline=control,candidate='conditional',draws=2000,seed=20261123)
        pairs[control]={k:{s:v[s] for s in ('eligible','gained','lost','net_hits','date_cluster_ci')}
                        for k,v in result['paired'].items()}
    agreement_pairs={}
    for control in ('r5','conditional','retained_first','market','price_pair_proxy'):
        if rows:
            paired=compare(rows,baseline=control,candidate='agreement',draws=2000,seed=20261123)['paired']
            agreement_pairs[control]={k:{s:v[s] for s in ('eligible','gained','lost','net_hits','date_cluster_ci')}
                                      for k,v in paired.items()}
    quotes=[r for r in rows if r['line'] is not None]
    pushes=sum(settle_ou(sum(r['actual']),r['line'],'over')=='PUSH' for r in quotes)
    half_boundary=sum(settle_ou(sum(r['actual']),r['line'],'over') in ('HALF_LOSS','HALF_WIN') for r in quotes)
    ranks={}
    for label in ('r5','local_anchor','conditional','agreement','market'):
        counts=Counter()
        for r in rows:
            p=r['forecast'][label]
            if not p.get('top3'):
                continue
            score='%d-%d'%tuple(r['actual'])
            counts['n']+=1
            if p['primary']=='1-1':
                counts['pick_11']+=1
                counts['hit_11_among_11_picks']+=score=='1-1'
            counts['actual_11']+=score=='1-1'
            for j,v in enumerate(p['top3'],1):counts['rank_'+str(j)]+=v==score
            if sum(r['actual'])>=4:
                counts['actual_4plus']+=1
                counts['hit_4plus_in_top3']+=score in p['top3']
        ranks[label]=dict(counts)
    quality={}
    for label in ('local_anchor','structural','no_process','conditional','market','price_pair_proxy'):
        values=[r['quality'][label] for r in rows if label in r['quality']]
        if not values:continue
        q={k:float(np.mean([v[k] for v in values])) for k in ('p1','p3','wdl_max','score_nll','wdl_brier')}
        priced=[v for v in values if 'nonpush_mass' in v]
        q['expected_full_win']=sum(v['full_mass'] for v in priced)/sum(v['nonpush_mass'] for v in priced) if priced else None
        quality[label]=dict(n=len(values),**q)
    gate=bool(rows and target_n==len(rows) and all(
        d==len(rows)-(pushes if k=='actual_ou' else 0) and d and n/d>=goal
        for (k,(n,d)),goal in zip(all_scores['conditional'].items(),TARGETS)))
    agreement_pass=bool(rows and target_n==len(rows) and all(
        d==len(rows)-(pushes if k=='actual_ou' else 0) and d and n/d>=goal
        for (k,(n,d)),goal in zip(all_scores['agreement'].items(),TARGETS)))
    switches=sum(r['forecast']['r5']['primary']!=r['forecast']['agreement']['primary'] for r in rows)
    return dict(n=len(rows),known_schedule_n=target_n,agreement_all_four_pass=agreement_pass,
        agreement_first_switches=switches,agreement_paired=agreement_pairs,all_four_pass=gate,actual_lines=len(quotes),pushes=pushes,
        quarter_boundary=half_boundary,process_exposure=sum(r['process_exposure']>0 for r in rows),
        one_unseen_in_stage=sum(min(r['local_games'])==0 for r in rows),
        stage_cold_but_both_have_prior=sum(min(r['local_games'])==0 and min(r['pooled_games'])>0 for r in rows),
        market_basis=dict(Counter(r['market_basis'] for r in rows)),scores=all_scores,paired=pairs,rank_counts=ranks,
        probability_quality=quality)


def report(primary,early,scope,coverage):
    codes=[v['code'] for v in json.loads(scope.read_bytes())['competitions']]
    expected=json.loads(coverage.read_bytes())
    result={}
    for window in ('evaluation','recent','early'):
        groups={}
        population=[]
        for code in codes:
            source=(early if window=='early' else primary)/code/'predictions.jsonl'
            if window=='early' and not source.exists():continue
            rows=[r for r in read_lines(source) if r['window']==window]
            groups[code]=summarize(rows,expected[code].get('known_fixture_target') if window=='evaluation' else None)
            population.extend(rows)
        result[window]=dict(competitions=groups,aggregate=summarize(population))
    return result


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    for field in ('primary','early','scope','coverage','out'):
        ap.add_argument('--'+field,type=Path,required=True)
    a=ap.parse_args()
    write_new(a.out,report(a.primary,a.early,a.scope,a.coverage))
