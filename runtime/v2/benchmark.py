"""Four chronological blocks: fitting, calibration, selection, untouched test."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from model import (FAMILIES, digest, fit_baseline, fit_nuisance, fit_strength,
                   get_matrix, metrics, partitions, rates, calibrate)
from evidence import bounded_market,no_vig

ROOT=Path(__file__).resolve().parents[2] / 'reports' / 'model-upgrade-v2'

def paired_interval(first,second,rows,draws=3000):
    delta=[]
    for row in rows:
        actual=(row['home_goals'],row['away_goals'])
        flags=[]
        for model,family,temp in (first,second):
            m=get_matrix(model,row,family,temp)
            flags.append(np.unravel_index(np.argmax(m),m.shape)==actual)
        delta.append(int(flags[0])-int(flags[1]))
    # Resample calendar dates, preserving within-day match clustering.
    dates=sorted({r['date'] for r in rows});groups=[[i for i,r in enumerate(rows) if r['date']==d] for d in dates]
    rng=np.random.default_rng(20261007);values=[]
    for _ in range(draws):
        sampled=rng.integers(0,len(groups),len(groups));indices=[i for g in sampled for i in groups[g]]
        values.append(float(np.mean(np.array(delta)[indices])))
    return {'difference':float(np.mean(delta)),'date_cluster_bootstrap_95_interval':list(map(float,np.quantile(values,[.025,.975]))),'draws':draws}

def market_experiment(model,family,temp,cal,select,test):
    def eligible(rows):return [r for r in rows if all(r.get('historical_odds',{}).get(k,0)>1 for k in ('B365H','B365D','B365A'))]
    def score(rows,alpha):
        hits=0
        for r in rows:
            m=get_matrix(model,r,family,temp)
            target=no_vig([r['historical_odds'][k] for k in ('B365H','B365D','B365A')])
            tilted,_=bounded_market(m,target,alpha)
            hits+=np.unravel_index(np.argmax(tilted),tilted.shape)==(r['home_goals'],r['away_goals'])
        return int(hits)
    cal,select,test=map(eligible,(cal,select,test))
    if min(map(len,(cal,select,test)))<20:return {'status':'INSUFFICIENT_QUOTES','enabled':False}
    candidates=[0.,.05,.1,.2];calhits={str(a):score(cal,a) for a in candidates}
    alpha=max(candidates,key=lambda a:(calhits[str(a)],-a))
    baseline=score(select,0.);selected=score(select,alpha)
    enabled=alpha>0 and selected>baseline
    return {'status':'RETROSPECTIVE_ONLY_QUOTE_TIMESTAMPS_UNVERIFIED','enabled_for_future':False,
            'selected_alpha':alpha,'selection_gate_passed':enabled,'calibration_n':len(cal),'calibration_hits':calhits,
            'selection_n':len(select),'selection_baseline_hits':baseline,'selection_market_hits':selected,
            'test_n':len(test),'test_baseline_hits':score(test,0.),'test_market_hits':score(test,alpha if enabled else 0.),
            'max_kl':.02,'reason':'Historical quote observation times cannot be reconstructed; prospective policy remains disabled'}

def run(path):
    rows=json.loads(path.read_text());train,cal,selection,test=partitions(rows)
    model=fit_nuisance(fit_strength(train),train);baseline=fit_baseline(train)
    candidates=[]
    for family in FAMILIES:
        temp=calibrate(model,cal,family)
        candidates.append({'family':family,'temperature':temp,'selection':metrics(model,selection,family,temp)})
    # Test results cannot affect family or temperature selection.
    winner=max(candidates,key=lambda c:(c['selection']['top1'],-c['selection']['score_log_loss']))
    for c in candidates:
        c['test_uncalibrated']=metrics(model,test,c['family'],1.)
        c['test']=metrics(model,test,c['family'],c['temperature'])
    base=metrics(baseline,test,'poisson',1.)
    interval=paired_interval((model,winner['family'],winner['temperature']),(baseline,'poisson',1.),test)
    market=market_experiment(model,winner['family'],winner['temperature'],cal,selection,test)
    cohort=rows[0]['cohort']
    report={'cohort':cohort,'mode':'RETROSPECTIVE_DATE_ORDER_RECONSTRUCTION','formal_validation':False,
            'data_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'dataset_rows':len(rows),
            'split':{name:{'n':len(block),'start':block[0]['date'],'end':block[-1]['date']} for name,block in zip(['train','calibration','selection','test'],[train,cal,selection,test])},
            'fixed_hyperparameters':{'half_life_days':365,'ridge':8},'nuisance':model['nuisance'],
            'selection_rule':'selection-block Top1, score log loss tie-break; test never selects',
            'candidates':candidates,'selected_family':winner['family'],'selected_temperature':winner['temperature'],
            'baseline_v01':base,'paired_top1_comparison':interval,'market_experiment':market,
            'context_status':'NOT_ENABLED_NO_VERIFIED_PREMATCH_FEATURE_HISTORY',
            'promotion':'DO_NOT_PROMOTE','limitations':['query times are current, not historical first-known times','static training fit; no in-test refit','unseen teams use prior and are explicitly counted','non-EPL coverage not independently certified','single split not evidence of sustained improvement']}
    ROOT.mkdir(parents=True,exist_ok=True)
    (ROOT/(cohort+'-benchmark.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2))
    # Full-history artifact is for future research; separate from test-era fit.
    future=fit_nuisance(fit_strength(rows),rows)
    future.update({'selected_family':winner['family'],'temperature':winner['temperature'],
        'market_policy':{'validated':False,'alpha':0.},'formal_freeze_eligible':False,
        'research_freeze_requires':'verified future prematch identity and complete context evidence',
        'promotion':'DO_NOT_PROMOTE','benchmark_report':str(ROOT/(cohort+'-benchmark.json'))})
    (ROOT/(cohort+'-artifact.json')).write_text(json.dumps(future,ensure_ascii=False,indent=2))
    print(json.dumps({'cohort':cohort,'selected':winner['family'],'baseline_top1':base['top1'],'candidate_top1':winner['test']['top1'],'baseline_wdl':base['wdl_accuracy'],'candidate_wdl':winner['test']['wdl_accuracy'],'baseline_ou':base['total_2_5_accuracy'],'candidate_ou':winner['test']['total_2_5_accuracy'],'interval':interval},ensure_ascii=False),flush=True)
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('histories',nargs='+');a=p.parse_args()
    for path in a.histories:run(Path(path))
