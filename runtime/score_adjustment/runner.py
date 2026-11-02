#!/usr/bin/env python3
"""Train/apply research calibration with verified historical replay receipts."""
import os
for _key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):
    os.environ[_key] = '1'
import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import sys
import numpy as np
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from calibration import BASE, apply, date_split, digest, features, load, save, train
sys.path.insert(0, str(HERE.parent/'optimization'))
from ledger import read as read_receipt

REPORT = Path('/workspace/WCPP/reports/score-bias-adjustment-20261020/experiment')
MODELS = Path('/workspace/WCPP/models/score-calibration-20261020')
MANIFEST = Path('/workspace/WCPP/reports/systematic-optimization-20261015/all19-execution-manifest.json')
def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path, data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists(): raise FileExistsError(f'append-only output exists: {path}')
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
def now():
    return datetime.now(timezone(timedelta(hours=8))).isoformat()

def extract_sources():
    records=[]; seen=set(); source_protocols=[]; label_files=[]; rejected=[]
    runs=json.loads(MANIFEST.read_text())['runs']
    sources=[(Path(r['run']),Path(r['protocol'])) for r in runs]
    repair=Path('/workspace/WCPP/models/systematic-20261015-repair-001')
    sources.append((repair,repair/'protocol.json'))
    for root, protocol_path in sources:
        protocol=json.loads(protocol_path.read_text()); ph=file_hash(protocol_path)
        source_protocols.append({'path':str(protocol_path),'sha256':ph,'run':str(root),'labels':str(Path(protocol['dataset'])/'labels.jsonl')})
        label_files.append(Path(protocol['dataset'])/'labels.jsonl')
        for index in sorted(root.glob('*/dev[123]-receipt-index.json')):
            cohort=index.parent.name
            if cohort in ('usa.1','ksa.1') and root != repair:
                continue
            for receipt in json.loads(index.read_text()):
                if receipt['model'] != BASE: continue
                row=read_receipt(receipt)
                if row['status'] != 'PREDICTED':
                    rejected.append({'match_id':row['match_id'],'status':row['status'],'cohort':cohort});continue
                if row['match_id'] in seen: raise ValueError('duplicate calibration match')
                seen.add(row['match_id'])
                core=row['model_meta']['core']
                identity={'model':row['model'],'cohort':row['cohort'],'feature_group':row['model_meta']['feature_group'],'alpha':row['model_meta']['alpha'],'half_life':core['half_life'],'ridge':core['ridge'],'source_protocol_sha256':ph}
                records.append({'match_id':row['match_id'],'date':row['date'],'cohort':row['cohort'],'fold':row['fold'],'matrix':np.asarray(row['matrix'],float),'receipt':receipt,'base_identity':identity})
        print('verified',root.name,'cumulative_n',len(records),flush=True)
    return sorted(records,key=lambda r:(r['date'],r['match_id'])), source_protocols, label_files, rejected

def score_rows(rows, labels, artifact):
    results=[]
    for row in rows:
        m=apply(artifact,row['matrix'],base_identity=row['base_identity'])
        y=labels[row['match_id']];h,a=np.indices(m.shape);total=h+a
        pm=np.bincount(total.ravel(),weights=m.ravel(),minlength=2*len(m)-1)
        mode=tuple(int(x) for x in np.unravel_index(m.argmax(),m.shape))
        actual=y[0]+y[1]; cdf=np.cumsum(pm)
        p=float(m[y[0],y[1]]) if max(y)<len(m) else 0.
        results.append({'match_id':row['match_id'],'date':row['date'],'cohort':row['cohort'],'actual':y,'mode':list(mode),'hit':int(mode==tuple(y)),'nll':float(-np.log(max(p,1e-300))),'total_crps':float(np.sum((cdf-(np.arange(len(pm))>=actual))**2)),'mean_total':float(np.sum(total*m)),'mean_home':float(np.sum(h*m)),'mean_away':float(np.sum(a*m)),'mode_total':sum(mode),'actual_total':actual,'p_total_le2':float(pm[:3].sum()),'p_total_ge3':float(pm[3:].sum()),'p_total_ge4':float(pm[4:].sum()),'p_home_zero':float(m[0].sum()),'p_away_zero':float(m[:,0].sum())})
    return results

def summarize(rows):
    if not rows:return {'n':0}
    avg=lambda key:float(np.mean([r[key] for r in rows]))
    return {'n':len(rows),'hits':sum(r['hit'] for r in rows),'statistical_top1':avg('hit'),'score_logloss':avg('nll'),'total_crps':avg('total_crps'),'total_mean_bias':avg('mean_total')-avg('actual_total'),'mean_total':avg('mean_total'),'actual_mean_total':avg('actual_total'),'mode_mean_total':avg('mode_total'),'mode_total_le2_rate':float(np.mean([r['mode_total']<=2 for r in rows])),'actual_total_le2_rate':float(np.mean([r['actual_total']<=2 for r in rows])),'actual_total_ge4_rate':float(np.mean([r['actual_total']>=4 for r in rows])),**{k:avg(k) for k in ('p_total_le2','p_total_ge3','p_total_ge4','p_home_zero','p_away_zero')}}

def paired(base, candidate):
    if [r['match_id'] for r in base] != [r['match_id'] for r in candidate]:raise ValueError('unpaired rows')
    differences=np.array([c['hit']-b['hit'] for b,c in zip(base,candidate)])
    weeks={}
    for b,d in zip(base,differences):
        dt=datetime.fromisoformat(b['date'][:10]);key=dt.strftime('%G-W%V')
        item=weeks.setdefault(key,[0,0]);item[0]+=int(d);item[1]+=1
    values=np.array(list(weeks.values()),float);rng=np.random.default_rng(20261020)
    indices=rng.integers(0,len(values),size=(2000,len(values)))
    sums=values[indices].sum(axis=1);delta=sums[:,0]/sums[:,1]
    return {'n':len(base),'gains':int(np.sum(differences==1)),'losses':int(np.sum(differences==-1)),'net_hits':int(differences.sum()),'delta_top1':float(differences.mean()),'calendar_week_blocks':len(weeks),'week_cluster_bootstrap_95ci':np.quantile(delta,[.025,.975]).tolist(),'bootstrap_draws':2000,'seed':20261020,'limitation':'Shared calendar-week cluster bootstrap; descriptive internal-history interval, not selection-adjusted or future proof'}

def run_train(report,models):
    if (report/'results.json').exists() or (models/'locked-model.json').exists(): raise FileExistsError('experiment already completed or locked; do not repeat final selection')
    protocol=report/'protocol-amendment-001.json'
    checkpoint=json.loads((report/'checkpoint-preregistered.json').read_text())
    if checkpoint['protocol_sha256'] != file_hash(report/'protocol.json'):raise ValueError('preregistration hash mismatch')
    rows, sources, label_files, rejected=extract_sources()
    split, dates=date_split(rows)
    checkpoint_data={'saved_at_bjt':now(),'protocol_sha256':file_hash(protocol),'source_manifest_sha256':file_hash(MANIFEST),'sources':sources,'rejected':rejected,'segments':{k:{'n':len(v),'days':len(dates[k]),'start':dates[k][0],'end':dates[k][-1],'match_ids':[rows[i]['match_id'] for i in v]} for k,v in split.items()}}
    checkpoint_path=report/'source-and-split-checkpoint.json'
    if checkpoint_path.exists():
        existing=json.loads(checkpoint_path.read_text())
        if {k:v for k,v in existing.items() if k!='saved_at_bjt'} != {k:v for k,v in checkpoint_data.items() if k!='saved_at_bjt'}:
            raise ValueError('source checkpoint changed on resume')
    else:
        write(checkpoint_path,checkpoint_data)
    # Labels are separate targets. No label is passed to apply or features.
    labels={}
    for path in label_files:
        for line in path.open():
            y=json.loads(line); value=[int(y['home_goals']),int(y['away_goals'])]
            if y['match_id'] in labels and labels[y['match_id']] != value:raise ValueError('conflicting score labels')
            labels[y['match_id']]=value
    if any(r['match_id'] not in labels for r in rows):raise ValueError('missing label')
    binding={'model':BASE,'cohorts':sorted({r['cohort'] for r in rows}),'source_protocol_sha256':[s['sha256'] for s in sources],'required_configuration':{'feature_group':'core','half_life':365,'ridge':30,'alpha':0.0}}
    fitrows=[rows[i] for i in split['train']]
    artifacts={}
    for candidate in ('C0','C1','C2'):
        artifact=train([r['matrix'] for r in fitrows],[labels[r['match_id']] for r in fitrows],candidate=candidate,base_binding=binding,training_metadata={'segment':'train','start':dates['train'][0],'end':dates['train'][-1],'protocol_sha256':file_hash(protocol),'created_at_bjt':now(),'source_manifest_sha256':file_hash(MANIFEST)})
        artifact=save(artifact,models/(candidate+'.json'));artifacts[candidate]=artifact
        print('fit',candidate,artifact['theta'],flush=True)
    selectrows=[rows[i] for i in split['select']]
    select_scores={k:score_rows(selectrows,labels,a) for k,a in artifacts.items()}
    selection={k:summarize(v) for k,v in select_scores.items()};b=selection['C0']
    eligibility={}
    for key,s in selection.items():
        eligibility[key]={'strict_top1_improvement':s['hits']>b['hits'],'logloss_guard':s['score_logloss']<=b['score_logloss']+.02,'crps_guard':s['total_crps']<=b['total_crps']+.02,'abs_bias_guard':abs(s['total_mean_bias'])<=abs(b['total_mean_bias'])+.05}
    valid=[k for k in artifacts if k!='C0' and all(eligibility[k].values())]
    winner=sorted(valid,key=lambda k:(-selection[k]['hits'],selection[k]['score_logloss'],k))[0] if valid else 'C0'
    lock={'saved_at_bjt':now(),'winner':winner,'selection':selection,'guards':eligibility,'rule':'Top1 strictly higher with loss/CRPS/abs-bias guards; no final labels scored yet','artifact_sha256':artifacts[winner]['artifact_sha256'],'promotion':'DISABLED_RESEARCH_ONLY'}
    write(report/'selection-lock.json',lock);save(artifacts[winner],models/'locked-model.json')
    print('LOCKED',winner,flush=True)
    # Final scoring begins only after the immutable selection and artifact are saved.
    finals=[rows[i] for i in split['final']]
    final_scores={k:score_rows(finals,labels,a) for k,a in artifacts.items()}
    results={'completed_at_bjt':now(),'scope':'Previously inspected historical internal chronological review; no untouched or future KPI claim','protocol_sha256':file_hash(protocol),'sources':sources,'total_n':len(rows),'cohorts':binding['cohorts'],'segments':{k:{'n':len(v),'start':dates[k][0],'end':dates[k][-1],'days':len(dates[k])} for k,v in split.items()},'selection_lock':lock,'locked_candidate':winner,'final':{k:summarize(v) for k,v in final_scores.items()},'paired_final_vs_C0':{k:paired(final_scores['C0'],v) for k,v in final_scores.items()},'by_cohort_final':{c:{k:summarize([r for r in v if r['cohort']==c]) for k,v in final_scores.items()} for c in binding['cohorts']},'decision':'RESEARCH_ONLY_NO_PROMOTION','original_models_changed':False,'comprehensive_primary_changed':False}
    write(report/'results.json',results)
    with (report/'paired-final-rows.jsonl').open('x') as out:
        for i,row in enumerate(final_scores['C0']):
            out.write(json.dumps({'match_id':row['match_id'],'date':row['date'],'cohort':row['cohort'],'actual':row['actual'],'candidates':{k:v[i] for k,v in final_scores.items()}},ensure_ascii=False)+'\n')
    write(report/'receipt-provenance.json', [{'match_id':r['match_id'],'date':r['date'],'cohort':r['cohort'],'receipt':r['receipt'],'base_identity':r['base_identity']} for r in rows])
    print(json.dumps({'total_n':len(rows),'winner':winner,'final':results['final'],'paired':results['paired_final_vs_C0']},ensure_ascii=False,indent=2),flush=True)

def main():
    parser=argparse.ArgumentParser();commands=parser.add_subparsers(dest='command',required=True)
    p=commands.add_parser('train');p.add_argument('--report',type=Path,default=REPORT);p.add_argument('--models',type=Path,default=MODELS)
    p=commands.add_parser('apply');p.add_argument('--artifact',type=Path,required=True);p.add_argument('--input',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    if args.command=='train':run_train(args.report,args.models)
    else:
        payload=json.loads(args.input.read_text());artifact=load(args.artifact)
        q=apply(artifact,payload['matrix'],base_identity=payload['base_identity'])
        write(args.out,{'track':'INDEPENDENT_SCORE_CALIBRATION_RESEARCH','candidate':artifact['candidate'],'artifact_sha256':artifact['artifact_sha256'],'matrix':q.tolist(),'statistical_mode':list(map(int,np.unravel_index(q.argmax(),q.shape))),'comprehensive_primary':payload.get('primary'),'primary_origin':'copied unchanged input; no calibration-generated comprehensive Primary','base_identity':payload['base_identity']})
if __name__=='__main__':main()
