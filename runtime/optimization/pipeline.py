"""Auditable independent historical research CLI. Never mutates active models."""
import argparse,json,sys,time
from pathlib import Path
from collections import Counter,defaultdict
import numpy as np
from schema import save,now,digest,file_hash,code_hash,fixture
import dataset
from features import History,GROUPS,NAMES
from models import fit_core,fit_residual,predict,matrix
from ledger import record,read
from audit import csv_write

GRID=[dict(name=f'M2-h{h}-r{r}',half_life=h,ridge=r,group='core',alpha=0.) for h in (180,365) for r in (10,30)] + [dict(name=f'M3-{g}-a{a}',half_life=365,ridge=30,group=g,alpha=a) for g in ('process','xg') for a in (.25,.5)]

def plan(ds,out,cohorts=None):
 fs,labels,facts,manifest=dataset.load(ds);groups=defaultdict(list)
 for r in fs:groups[r['cohort']].append(r)
 routes=manifest['active_cohorts'];spec=[]
 for code in (cohorts or routes):
  rows=groups.get(code,[]);dates=sorted({r['date'] for r in rows})
  if len(rows)<700 or len(dates)<100:
   spec.append({'cohort':code,'status':'INSUFFICIENT_OR_PHASE_UNSUPPORTED','n':len(rows)});continue
  edges=[dates[int(len(dates)*p)] for p in (.5,.6,.7,.8)]
  folds=[]
  for i,start in enumerate(edges):
   end=edges[i+1] if i<3 else '9999-12-31'
   folds.append({'name':'dev'+str(i+1) if i<3 else 'audit','start':start,'end':end,'fit_ids':[r['match_id'] for r in rows if r['date']<start], 'target_ids':[r['match_id'] for r in rows if start<=r['date']<end]})
  spec.append({'cohort':code,'status':'PLANNED','n':len(rows),'folds':folds})
 value={'created_at':now(),'version':'SYSTEMATIC-PROTOCOL-1','dataset':str(Path(ds).resolve()),'dataset_manifest_hash':file_hash(Path(ds)/'manifest.json'),'code_hash':code_hash(),'time_track':'RETROSPECTIVE_PRIOR_ONLY','previously_inspected':'ALL_HISTORICAL_DATA_POTENTIALLY_INSPECTED; audit operationally withheld only','fixed_origin':True,'audit_update_policy':'No audit result is absorbed; all audit predictions use the single pre-audit fit and feature history. Not rolling live retraining.','lag_days':7,'lag_sensitivity':[2,14],'seed':20261015,'candidates':GRID,'cohorts':spec,'selection':'highest aggregate development Top1; tie score log loss then grid order; abs-bias noninferiority .05 and CRPS .02 versus M2-h365-r30','legacy':'M0 baseline equation; M1 fixed alpha .25 algorithm simulation, known-venue subset only','injury':'NOT_TRAINED_NO_ASOF_HISTORY','future_promotion':'disabled; prospective review >=500 matches over >=3 months and paired Top1 CI lower bound >0 with loss/bias guardrails','resources':'1 thread/booster; sequential cohorts'}
 save(out,value);return {s['cohort']:s['status'] for s in spec}

def _rows(ids,fs,labels):
 return [{**fs[i],**labels[i]} for i in ids]

def _metrics(receipts,labels):
 from evaluate import score_prediction,summarize
 scored=[score_prediction(read(r),labels.get(r['match_id'])) for r in receipts]
 return summarize(scored),scored

def _save_predictions(root,targets,model,fold,mu,meta,origin,history):
 receipts=[]
 for i,r in enumerate(targets):
  x,provenance=history.vector(r,origin);feature_values=[[None if not np.isfinite(v) else float(v) for v in row] for row in x]
  p={'feature_hash':digest(feature_values),'feature_values':feature_values,'provenance':provenance,'match_id':r['match_id'],'date':r['date'],'cohort':r['cohort'],'model':model,'fold':fold,'status':'PREDICTED','simulated_cutoff':r['date']+'T00:00:00+00:00','cutoff_at':r['date']+'T00:00:00+00:00','fit_origin':origin,'matrix':matrix(mu[i]).tolist(),'rates':mu[i].tolist(),'input_hash':digest({'fixture':fixture(r),'features':feature_values,'provenance':provenance}),'model_meta':meta}
  mm=np.asarray(p['matrix']);ij=tuple(map(int,np.unravel_index(np.argmax(mm),mm.shape)));p['primary']=list(ij);p['primary_probability']=float(mm[ij]);p['model_meta']['process_xg_lag_days']=history.lag
  receipts.append(record(root,p))
 return receipts

def train(protocol,out,only=None):
 p=json.loads(Path(protocol).read_text())
 if code_hash()!=p['code_hash']:raise ValueError('code changed since protocol registration')
 if file_hash(Path(p['dataset'])/'manifest.json')!=p['dataset_manifest_hash']:raise ValueError('dataset manifest changed')
 out=Path(out);out.mkdir(parents=True,exist_ok=False)
 save(out/'protocol.json',p)
 fs,labels,facts,man=dataset.load(p['dataset']);fm={r['match_id']:r for r in fs};all_receipts=[];results=[];statuses=[]
 started=time.time()
 for scope in p['cohorts']:
  code=scope['cohort']
  if only and code not in only:continue
  if scope['status']!='PLANNED':statuses.append(scope);continue
  print(now(),code,'START',scope['n'],flush=True)
  directory=out/code.replace(':','-');directory.mkdir();history=History([r for r in fs if r['cohort']==code],labels,facts,7)
  dev=defaultdict(list);trials=[];chosen=None
  for fold in scope['folds']:
   fitrows=_rows(fold['fit_ids'],fm,labels);targets=[fixture(fm[i]) for i in fold['target_ids']]
   name=fold['name'];core_cache={};boost_cache={};configs=p['candidates']
   if name=='audit':
    from evaluate import summarize
    stats={cfg['name']:summarize(dev[cfg['name']]) for cfg in configs}
    reference=stats['M2-h365-r30'];allowed=[]
    for cfg in configs:
     s=stats[cfg['name']]
     if abs(s['total_bias'])<=abs(reference['total_bias'])+.05 and s['total_crps']<=reference['total_crps']+.02:allowed.append(cfg)
    chosen=sorted(allowed,key=lambda cfg:(-stats[cfg['name']]['top1_rate'],stats[cfg['name']]['score_log_loss'],p['candidates'].index(cfg)))[0]
    save(directory/'development-selection.json',{'selected':chosen,'all_development_metrics':stats,'selected_before_audit_predictions':now()})
    # Selection is fixed before any audit labels are loaded for scoring.
    configs=[cfg for cfg in configs if cfg['name'] in {chosen['name'],'M2-h365-r30','M3-process-a0.5','M3-xg-a0.5'}]
   for cfg in configs:
    key=(cfg['half_life'],cfg['ridge']);cc={'half_life':key[0],'ridge':key[1]}
    if key not in core_cache:core_cache[key]=fit_core(fitrows,**cc)
    core=core_cache[key];booster=None;meta={'core':core,'feature_group':cfg['group'],'alpha':cfg['alpha'],'feature_names':[NAMES[j] for j in GROUPS[cfg['group']]],'time_track':p['time_track'],'first_known_at':None,'process_xg_lag_days':7}
    if cfg['group']!='core':
     if cfg['group'] not in boost_cache:boost_cache[cfg['group']]=fit_residual(fitrows,history,cc,cfg['group'])
     booster,bmeta=boost_cache[cfg['group']];meta.update(bmeta)
     booster.save_model(str(directory/(name+'-'+cfg['group']+'.lgb.txt')))
    mu=predict(core,targets,history,booster,cfg['group'],cfg['alpha'],fold['start'])
    receipts=_save_predictions(directory/'predictions',targets,cfg['name'],name,mu,meta,fold['start'],history);all_receipts.extend(receipts)
    # Only receipt reader crosses the label boundary, after all target predictions persist.
    summary,scored=_metrics(receipts,labels)
    if name!='audit':dev[cfg['name']].extend(scored)
    trials.append({'cohort':code,'fold':name,'model':cfg['name'],'summary':summary,'n':len(receipts)})
    if name=='audit':
     save(directory/(name+'-'+cfg['name']+'-scores.json'),scored)
     save(directory/(name+'-'+cfg['name']+'-artifact.json'),{'model':cfg,'core':core,'meta':meta,'protocol_hash':file_hash(protocol),'status':'RESEARCH_CANDIDATE','active':False})
    print(now(),code,name,cfg['name'],'n',len(receipts),'top1',summary['top1_rate'],flush=True)
   if name=='audit' and any(f['kind']=='xg' and f['match_id'] in set(fold['fit_ids']) for f in facts):
    for lag in (2,14):
     hlag=History([r for r in fs if r['cohort']==code],labels,facts,lag);cc={'half_life':365,'ridge':30};core=core_cache.get((365,30)) or fit_core(fitrows,**cc)
     booster,bmeta=fit_residual(fitrows,hlag,cc,'xg');mu=predict(core,targets,hlag,booster,'xg',.5,fold['start']);model='SENS-xg-lag'+str(lag)
     rr=_save_predictions(directory/'predictions',targets,model,name,mu,bmeta,fold['start'],hlag);all_receipts.extend(rr);summary,scored=_metrics(rr,labels)
     save(directory/('audit-'+model+'-scores.json'),scored);trials.append({'cohort':code,'fold':name,'model':model,'summary':summary,'n':len(rr)})
   # Fixed comparison replay, kept independent and explicitly restricted to venue evidence.
   from legacy import fit_legacy,predict_legacy
   for mode in ('M0','M1'):
    legacyfit=fit_legacy(fitrows,mode);rr=[]
    for target in targets:
     result=predict_legacy(legacyfit,target);pred={'match_id':target['match_id'],'date':target['date'],'cohort':code,'model':mode+'-LegacyReplay','fold':name,'simulated_cutoff':target['date']+'T00:00:00+00:00','fit_origin':fold['start'],'status':'ABSTAIN' if result.get('abstain') else 'PREDICTED','meta':result.get('meta',{}),'reason':result.get('reason')}
     if not result.get('abstain'):
      mm=np.asarray(result['matrix']);ij=tuple(map(int,np.unravel_index(np.argmax(mm),mm.shape)));pred.update(matrix=mm.tolist(),primary=list(ij),primary_probability=float(mm[ij]))
     rr.append(record(directory/'predictions',pred))
    all_receipts.extend(rr);summary,scored=_metrics(rr,labels);trials.append({'cohort':code,'fold':name,'model':mode+'-LegacyReplay','summary':summary,'n':len(rr)})
    if name=='audit':save(directory/(name+'-'+mode+'-LegacyReplay-scores.json'),scored)
   save(directory/(name+'-receipt-index.json'),[r for r in all_receipts if Path(r['path']).parent.parent==directory and read(r)['fold']==name])
  save(directory/'trials.json',trials);results.extend(trials);statuses.append({'cohort':code,'status':'TRAINED_RESEARCH','n':scope['n'],'selected':chosen,'audit_targets':len(scope['folds'][-1]['target_ids'])})
  print(now(),code,'DONE seconds',round(time.time()-started,1),flush=True)
 save(out/'receipt-index.json',all_receipts);save(out/'results.json',results);save(out/'cohort-status.json',statuses)
 save(out/'run-manifest.json',{'actual_generated_at':now(),'protocol':str(protocol),'protocol_hash':file_hash(protocol),'code_hash':code_hash(),'dataset_manifest_hash':p['dataset_manifest_hash'],'receipts':len(all_receipts),'elapsed_seconds':time.time()-started,'active_changed':False,'status':'RESEARCH_ONLY_NOT_PROSPECTIVE'})
 return statuses

def score(run,out):
 from evaluate import paired
 run=Path(run);out=Path(out);out.mkdir(parents=True,exist_ok=False);results=json.loads((run/'results.json').read_text());rows=[]
 for r in results:
  rows.append({'cohort':r['cohort'],'fold':r['fold'],'model':r['model'],**{k:v for k,v in r['summary'].items() if isinstance(v,(int,float,str)) or v is None}})
 csv_write(out/'paired-evaluation.csv',rows)
 comparisons=[]
 for d in sorted(run.iterdir()):
  if not d.is_dir() or not (d/'development-selection.json').exists():continue
  selected=json.loads((d/'development-selection.json').read_text())['selected']['name'];a=json.loads((d/('audit-'+selected+'-scores.json')).read_text())
  for baseline in ['M2-h365-r30','M0-LegacyReplay','M1-LegacyReplay','M3-process-a0.5','M3-xg-a0.5']:
   bpath=d/('audit-'+baseline+'-scores.json')
   if bpath.exists():comparisons.append({'cohort':d.name,'candidate':selected,'reference':baseline,**paired(json.loads(bpath.read_text()),a)})
 save(out/'paired-comparisons.json',comparisons)
 return {'rows':len(rows),'comparisons':len(comparisons)}

def verify(run,baseline):
 b=json.loads(Path(baseline).read_text());r=json.loads((Path(run)/'run-manifest.json').read_text());checked=0;files=[]
 for receipt in json.loads((Path(run)/'receipt-index.json').read_text()):read(receipt);checked+=1
 for item in b['files']:
  actual=file_hash(item['path']);files.append({'path':item['path'],'expected':item['sha256'],'actual':actual,'unchanged':actual==item['sha256']})
 dbpath=Path(baseline).parent/'database-baseline.json';db=json.loads(dbpath.read_text());actual=file_hash(db['database'])
 answer={'receipts_verified':checked,'run_code_current':r['code_hash']==code_hash(),'protected_files':files,'canonical_sha256':actual,'canonical_unchanged':actual==db['sha256'],'active_unchanged':all(x['unchanged'] for x in files)}
 if not answer['active_unchanged'] or not answer['canonical_unchanged'] or not answer['run_code_current']:raise ValueError('integrity verification failed: '+json.dumps(answer))
 return answer

def main():
 a=argparse.ArgumentParser();sub=a.add_subparsers(dest='command',required=True)
 for command in ['audit','export','plan','train','score','verify']:
  s=sub.add_parser(command)
  for arg in {'audit':['db','registry','out'],'export':['db','registry','staging','out'],'plan':['dataset','out'],'train':['protocol','out'],'score':['run','out'],'verify':['run','baseline']}[command]:s.add_argument('--'+arg,required=True)
 args=vars(a.parse_args());command=args.pop('command')
 if command=='audit':from audit import audit;answer=audit(**args)
 elif command=='export':answer=dataset.export(**args)
 elif command=='plan':answer=plan(args['dataset'],args['out'])
 else:answer=globals()[command](**args)
 print(json.dumps(answer,ensure_ascii=False))
if __name__=='__main__':main()
