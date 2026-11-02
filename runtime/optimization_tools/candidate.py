"""Full-history research refits and future candidate predictions; active registry untouched."""
import argparse,importlib.util,json,sys
from datetime import datetime,timedelta,timezone
from pathlib import Path
import numpy as np
import lightgbm as lgb
OPT=Path(__file__).resolve().parents[1]/'optimization'
sys.path.insert(0,str(OPT))
from schema import save,file_hash,digest,code_hash,now,fixture,validate_fixture,aware
from dataset import load
from features import History,NAMES,GROUPS
from models import fit_core,fit_residual,predict,matrix

def _loader(artifact):
 a=json.loads(Path(artifact).read_text())
 if a['finalizer_sha256']!=file_hash(__file__):raise ValueError('candidate tool source changed')
 if a['optimization_code_hash']!=code_hash():raise ValueError('research model source changed')
 if a['dataset_manifest_hash']!=file_hash(Path(a['dataset'])/'manifest.json'):raise ValueError('snapshot changed')
 b=None
 if a['booster_file']:
  p=Path(artifact).parent/a['booster_file']
  if file_hash(p)!=a['booster_sha256']:raise ValueError('booster hash changed')
  b=lgb.Booster(model_file=str(p))
 fs,labels,facts,manifest=load(a['dataset']);hist=History([r for r in fs if r['cohort']==a['cohort']],labels,facts,7)
 return a,b,hist

def calculate(artifact,target):
 if target.get('gender') not in ('male','men') or target.get('age')!='senior':raise ValueError('explicit adult male classification required')
 a,b,h=_loader(artifact)
 if target['cohort']!=a['cohort'] or target['competition']!=a['cohort'] or target['date']<=a['core']['fit_end']:raise ValueError('candidate competition/cohort/date mismatch')
 if target.get('stage') not in ('regular','regular-season'):raise ValueError('candidate only supports domestic regular phase')
 if a['cohort']=='kor.2' and target['date']>='2026-01-01':raise ValueError('K2 post2025 format is not trained; historical regular model cannot serve this format')
 target=validate_fixture(fixture(target))
 cfg=a['configuration'];mu=predict(a['core'],[target],h,b,cfg['group'],cfg['alpha'],target['date'])[0];m=matrix(mu);ij=tuple(map(int,np.unravel_index(m.argmax(),m.shape)));x,proof=h.vector(target)
 x=[[None if not np.isfinite(v) else float(v) for v in row] for row in x]
 return {'model':'WCPP-SYSTEMATIC-RESEARCH-0.1','candidate_configuration':cfg,'cohort':a['cohort'],'status':'PREDICTED','matrix':m.tolist(),'primary':list(ij),'primary_probability':float(m[ij]),'component_poisson_rates':mu.tolist(),'expected_goals':[float(np.arange(len(m))@m.sum(axis=1)),float(np.arange(len(m))@m.sum(axis=0))],'input_hash':digest({'fixture':target,'features':x,'provenance':proof}),'provenance':proof,'artifact_hash':file_hash(artifact),'training_n':a['core']['fit_n'],'trained_xg_channel':a.get('trained_xg_channel',False),'xg_training_status':a.get('xg_training_status','NOT_RECORDED'),'xg_oof_bilateral_targets_ge3':a.get('xg_oof_bilateral_targets_ge3',0),'xg_values_used_in_trees':a.get('xg_value_tree_splits',0)>0,'injury_status':'NOT_TRAINED_NO_ASOF_HISTORY','verified_accuracy':False,'active':False}

def finalize(run,out):
 run=Path(run);out=Path(out);out.mkdir(parents=True,exist_ok=False);protocol=json.loads((run/'protocol.json').read_text());fs,labels,facts,man=load(protocol['dataset']);index=[]
 for d in sorted(run.iterdir()):
  selection=d/'development-selection.json'
  if not selection.exists():continue
  cfg=json.loads(selection.read_text())['selected'];code=d.name;rows=[{**r,**labels[r['match_id']]} for r in fs if r['cohort']==code];h=History([r for r in fs if r['cohort']==code],labels,facts,7);cc={'half_life':cfg['half_life'],'ridge':cfg['ridge']};core=fit_core(rows,**cc)
  folder=out/code;folder.mkdir();booster=None;bmeta={};boostfile=None
  if cfg['group']!='core':
   booster,bmeta=fit_residual(rows,h,cc,cfg['group']);boostfile=folder/'residual.lgb.txt';booster.save_model(str(boostfile))
  xg_n=sum(v.get('both_sides_xg_ge3') or 0 for v in bmeta.get('oof_feature_coverage',[])) if cfg['group']=='xg' else 0
  importance=booster.feature_importance(importance_type='split').tolist() if booster is not None else []
  art={'forecast_scope':{'competition':code,'gender':'male','age':'senior','stages':['regular','regular-season'],'post2025_k2_format':'BLOCKED_NOT_TRAINED' if code=='kor.2' else 'NOT_APPLICABLE'},'xg_oof_bilateral_targets_ge3':xg_n,'trained_xg_channel':cfg['group']=='xg' and xg_n>0,'xg_value_tree_splits':sum(importance[i] for i in (22,23,25,26)) if cfg['group']=='xg' else 0,'xg_mask_tree_splits':sum(importance[i] for i in (24,27)) if cfg['group']=='xg' else 0,'feature_split_importance':importance,'xg_training_status':('DISABLED_IN_SELECTED_CONFIGURATION' if cfg['group']!='xg' else 'NOT_TRAINED_NO_BILATERAL_XG_HISTORY' if not xg_n else 'TRAINED_RESEARCH_UNDERSTAT_PRIOR_ONLY'),'version':'WCPP-SYSTEMATIC-RESEARCH-0.1','status':'RESEARCH_CANDIDATE_NOT_PROMOTED','cohort':code,'configuration':cfg,'core':core,'residual_metadata':bmeta,'booster_file':boostfile.name if boostfile else None,'booster_sha256':file_hash(boostfile) if boostfile else None,'dataset':protocol['dataset'],'dataset_manifest_hash':protocol['dataset_manifest_hash'],'optimization_code_hash':code_hash(),'finalizer_sha256':file_hash(__file__),'created_at':now(),'selection_source':str(selection),'selection_sha256':file_hash(selection),'audit_labels_not_used_for_selection':True,'refit_policy':'All accepted historical labels now used for final fit only; no reselection from audit','feature_names':[NAMES[i] for i in GROUPS[cfg['group']]],'active':False}
  path=folder/'artifact.json';save(path,art)
  # Deterministic serialize/reload smoke, synthetic identity only, never football performance.
  t=fixture(rows[-1]);t.update(gender='male',age='senior',match_id='SYNTHETIC-SERIALIZE-'+code,date=(datetime.fromisoformat(core['fit_end'])+timedelta(days=10)).date().isoformat());a=predict(core,[t],h,booster,cfg['group'],cfg['alpha'],t['date'])[0];b=calculate(path,t)
  if not np.array_equal(matrix(a),np.asarray(b['matrix'])):raise ValueError('serialize/reload differs')
  save(folder/'serialize-check.json',{'synthetic':True,'matrix_exactly_equal':True,'fixture':t,'matrix_sha256':digest(b['matrix'])})
  index.append({'cohort':code,'artifact':str(path),'sha256':file_hash(path),'train_n':len(rows),'configuration':cfg,'serialize_exact':True});print(now(),code,'FINAL_REFIT',len(rows),flush=True)
 save(out/'candidate-index.json',index);return index

def future(artifact,fixture_file,out,freeze_root=None):
 if Path(out).exists():raise FileExistsError('prediction output already exists')
 target=json.loads(Path(fixture_file).read_text());current=aware(now());cutoff=aware(target['cutoff_at']);kickoff=aware(target['kickoff_at'])
 if cutoff>current or kickoff<=current:raise ValueError('requires actual pre-kickoff cutoff')
 a=json.loads(Path(artifact).read_text())
 if aware(a['created_at'])>cutoff:raise ValueError('model was not available at cutoff')
 if aware(json.loads((Path(a['dataset'])/'manifest.json').read_text())['created_at'])>cutoff:raise ValueError('data snapshot not available at cutoff')
 result=calculate(artifact,target);spec=importlib.util.spec_from_file_location('_candidate_freeze',OPT.parent/'research/analysis_freeze.py');mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
 kwargs={'stage':'prematch','track':'systematic_candidate'}
 if freeze_root:kwargs['root']=freeze_root
 result['freeze']=mod.save(result,target,**kwargs);save(out,result);return {'primary':result['primary'],'freeze':result['freeze'],'active':False}

def main():
 p=argparse.ArgumentParser();s=p.add_subparsers(dest='command',required=True);f=s.add_parser('finalize');f.add_argument('--run',required=True);f.add_argument('--out',required=True);f=s.add_parser('predict');f.add_argument('--artifact',required=True);f.add_argument('--fixture',required=True);f.add_argument('--out',required=True);f.add_argument('--freeze-root');a=vars(p.parse_args());c=a.pop('command');print(json.dumps(finalize(**a) if c=='finalize' else future(a['artifact'],a['fixture'],a['out'],a['freeze_root']),ensure_ascii=False))
if __name__=='__main__':main()
