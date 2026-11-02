"""Independent candidate experiments; old inspected test is explicitly development only."""
import json,sys,hashlib,pickle,warnings
from pathlib import Path
from datetime import datetime,timezone
import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss
import lightgbm as lgb
from xgboost import XGBClassifier
from catboost import CatBoostClassifier
import optuna
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from upset_v2 import features as ft
old=ft.old
VERSION='WCPP-UPSET-0.2'
SEED=20261013
CODE_HASH=hashlib.sha256((Path(__file__).read_bytes()+Path(ft.__file__).read_bytes())).hexdigest()
optuna.logging.set_verbosity(optuna.logging.WARNING)

def normalized(p):
 p=np.maximum(np.asarray(p,float),1e-9);return p/p.sum(axis=1,keepdims=True)

def model(kind,params=None):
 p=params or {}
 if kind=='logistic':
  return make_pipeline(SimpleImputer(strategy='median',keep_empty_features=True,add_indicator=True),StandardScaler(),LogisticRegression(C=.1,max_iter=1500,random_state=SEED))
 if kind=='lightgbm':return lgb.LGBMClassifier(n_estimators=100,learning_rate=.025,num_leaves=p.get('num_leaves',5),min_child_samples=p.get('min_child_samples',60),reg_lambda=p.get('reg_lambda',30),random_state=SEED,n_jobs=1,verbosity=-1,deterministic=True,force_col_wise=True)
 if kind=='xgboost':return XGBClassifier(n_estimators=100,max_depth=2,learning_rate=.025,min_child_weight=20,reg_lambda=30,subsample=1,colsample_bytree=1,objective='multi:softprob',n_jobs=1,random_state=SEED)
 if kind=='catboost':return CatBoostClassifier(iterations=100,depth=3,learning_rate=.025,l2_leaf_reg=30,loss_function='MultiClass',thread_count=1,random_seed=SEED,verbose=False,allow_writing_files=False)
 raise ValueError(kind)

class TorchModel:
 def fit(self,x,y):
  import torch
  torch.manual_seed(SEED);torch.set_num_threads(1)
  self.pre=make_pipeline(SimpleImputer(strategy='median',keep_empty_features=True,add_indicator=True),StandardScaler());z=self.pre.fit_transform(x).astype('float32')
  self.net=torch.nn.Sequential(torch.nn.Linear(z.shape[1],16),torch.nn.Tanh(),torch.nn.Linear(16,3))
  optimizer=torch.optim.Adam(self.net.parameters(),lr=.005,weight_decay=.05)
  tx=torch.from_numpy(z);ty=torch.tensor(y,dtype=torch.long)
  for _ in range(100):
   optimizer.zero_grad();loss=torch.nn.functional.cross_entropy(self.net(tx),ty);loss.backward();optimizer.step()
  return self
 def predict_proba(self,x):
  import torch
  with torch.no_grad():return torch.softmax(self.net(torch.from_numpy(self.pre.transform(x).astype('float32'))),dim=1).numpy()


def score(y,p):
 r=old.metrics(y,normalized(p));loss=p[:,2];actual=np.asarray(y)==2
 bins=np.minimum((loss*10).astype(int),9)
 r['loss_ece_10_bins']=sum(abs(float(loss[bins==i].mean())-float(actual[bins==i].mean()))*int((bins==i).sum())/len(y) for i in range(10) if (bins==i).any())
 return r

def ci_by_date(y,p,b,dates):
 # Paired date-cluster bootstrap respects within-day dependence; diagnostic only.
 diff=(p[:,2]-(np.asarray(y)==2))**2-(b[:,2]-(np.asarray(y)==2))**2
 unique=np.unique(dates);sums=np.array([diff[dates==d].sum() for d in unique]);ns=np.array([(dates==d).sum() for d in unique]);rng=np.random.default_rng(SEED)
 index=rng.integers(len(unique),size=(1000,len(unique)));boot=sums[index].sum(axis=1)/ns[index].sum(axis=1)
 return {'candidate_minus_market':float(diff.mean()),'date_cluster_bootstrap_95ci':list(map(float,np.quantile(boot,[.025,.975])))}

def train(rows,old_report,out):
 it=ft.items(rows);ds=old_report['date_split'];start=ds['train_end_exclusive'];test_start=ds['test_start'];dates=np.array([i['row']['date'] for i in it]);x=np.stack([i['features'] for i in it]);y=np.array([i['label'] for i in it]);market=np.stack([i['market'] for i in it]);train=dates<start;valid=(dates>=start)&(dates<test_start);test=dates>=test_start
 raw_coverage=dict(zip(ft.FEATURES,map(float,np.isfinite(x).mean(axis=0))))
 untrained_columns=np.flatnonzero(~np.isfinite(x[train]).any(axis=0)).tolist()
 x[:,untrained_columns]=np.nan
 assert [int(train.sum()),int(valid.sum()),int(test.sum())]==ds['counts']
 train_dates=np.unique(dates[train]);cut1=train_dates[int(len(train_dates)*.5)];cut2=train_dates[int(len(train_dates)*.75)]
 inner=[(dates<cut1,(dates>=cut1)&(dates<cut2)),(dates<cut2,(dates>=cut2)&(dates<start))]
 def objective(trial):
  p={'num_leaves':trial.suggest_categorical('num_leaves',[3,5,7]),'min_child_samples':trial.suggest_categorical('min_child_samples',[40,80]),'reg_lambda':trial.suggest_categorical('reg_lambda',[10,30,60])};scores=[]
  for a,b in inner:
   m=model('lightgbm',p);m.fit(x[a],y[a]);scores.append(log_loss(y[b],normalized(m.predict_proba(x[b])),labels=[0,1,2]))
  return float(np.mean(scores))
 study=optuna.create_study(direction='minimize',sampler=optuna.samplers.TPESampler(seed=SEED));study.optimize(objective,n_trials=8)
 candidates=[];fitted={};predvalid={};predtest={}
 for group,cols in ft.GROUPS.items():
  for kind in ('logistic','lightgbm','xgboost','catboost','torch'):
   if kind=='torch' and group!='full':continue
   m=TorchModel() if kind=='torch' else model(kind,study.best_params if kind=='lightgbm' else None)
   # CatBoost requires missing values handled explicitly; fit preprocessing only on fit rows.
   if kind=='catboost':m=make_pipeline(SimpleImputer(strategy='median',keep_empty_features=True,add_indicator=True),m)
   m.fit(x[train][:,cols],y[train]);pv=normalized(m.predict_proba(x[valid][:,cols]));name=group+'-'+kind;fitted[name]=(m,cols);predvalid[name]=pv
   for alpha in (.25,.5,.75,1.):
    mixed=normalized((1-alpha)*market[valid]+alpha*pv)
    for temperature in (.85,1.,1.15):
     p=normalized(mixed**(1/temperature));candidates.append({'name':name,'alpha':alpha,'temperature':temperature,'validation':score(y[valid],p)})
 baseline={'name':'market','alpha':0.,'temperature':1.,'validation':score(y[valid],market[valid])};candidates.append(baseline)
 selected=min(candidates,key=lambda q:(q['validation']['log_loss'],q['validation']['loss_brier']))
 # Selection is locked before any final diagnostic predictions or metric access.
 (out/'selection-lock.json').write_text(json.dumps({'selected':selected,'locked_at':datetime.now(timezone.utc).isoformat(),'candidate_count':len(candidates)},indent=2)+'\n')
 if selected['name']=='market':pt=market[test];selected_model=None;cols=[]
 else:
  selected_model,cols=fitted[selected['name']];raw=normalized(selected_model.predict_proba(x[test][:,cols]));pt=normalized(((1-selected['alpha'])*market[test]+selected['alpha']*raw)**(1/selected['temperature']))
 diagnostic=score(y[test],pt);base=score(y[test],market[test]);ci=ci_by_date(y[test],pt,market[test],dates[test]);gate=len(y[test])>=100 and diagnostic['loss_brier']<base['loss_brier'] and diagnostic['log_loss']<=base['log_loss'] and diagnostic['loss_auc'] is not None and diagnostic['loss_auc']>=.6
 ablations={}
 for group in ft.GROUPS:
  options=[q for q in candidates if q['name'].startswith(group+'-')];best=min(options,key=lambda q:q['validation']['log_loss']);m,c=fitted[best['name']];raw=normalized(m.predict_proba(x[test][:,c]));p=normalized(((1-best['alpha'])*market[test]+best['alpha']*raw)**(1/best['temperature']));ablations[group]={'selected_on_validation':{k:best[k] for k in ('name','alpha','temperature')},'diagnostic':score(y[test],p)}
 # Importance: permutation over validation only, association, no causal inference.
 importances=[]
 if selected_model is not None:
  rng=np.random.default_rng(SEED);vx=x[valid][:,cols].copy();original=selected['validation']['log_loss']
  for j,column in enumerate(cols):
   v=vx.copy();v[:,j]=rng.permutation(v[:,j]);p=normalized(((1-selected['alpha'])*market[valid]+selected['alpha']*selected_model.predict_proba(v))**(1/selected['temperature']));importances.append({'feature':ft.FEATURES[column],'validation_logloss_increase':float(log_loss(y[valid],p,labels=[0,1,2])-original)})
  importances=sorted(importances,key=lambda q:q['validation_logloss_increase'],reverse=True)
 frozen={'version':VERSION,'cohort':rows[0]['cohort'],'selection':selected,'model':selected_model,'columns':cols,'feature_names':ft.FEATURES,'built_at':datetime.now(timezone.utc).isoformat(),'history':rows,'status':'CANDIDATE_NOT_ACTIVE','formal_kpi_eligible':False,'code_hash':CODE_HASH,'untrained_columns':untrained_columns}
 # Local trusted training output only. Never load external pickle artifacts.
 path=out/'candidate.pkl';path.write_bytes(pickle.dumps(frozen))
 records=[{'match_id':i['row']['match_id'],'date':i['row']['date'],'actual_class':int(i['label']),'candidate_probabilities':p.tolist(),'market_probabilities':b.tolist()} for i,p,b in zip([a for a,t in zip(it,test) if t],pt,market[test])]
 report={'version':VERSION,'cohort':rows[0]['cohort'],'history_rows':len(rows),'strong_favorite_rows':len(it),'features':ft.FEATURES,'split':ds,'selected':selected,'validation_candidates':candidates,'inner_optuna_trials':[{ 'params':t.params,'value':t.value} for t in study.trials],'diagnostic_test':diagnostic,'market_baseline':base,'development_gate_passed':bool(gate),'acceptance_gate_passed':False,'test_status':'REUSED_INSPECTED_TEST_DEVELOPMENT_ONLY','requires_untouched_future_validation':True,'paired_loss_brier':ci,'ablations':ablations,'validation_permutation_importance':importances[:15],'diagnostic_records':records,'untrained_features_masked':[ft.FEATURES[j] for j in untrained_columns],'raw_feature_coverage':raw_coverage,'feature_coverage':dict(zip(ft.FEATURES,map(float,np.isfinite(x).mean(axis=0)))),'limitations':['Historical publication timestamps unknown; replay not proven frozen snapshots','Same league schedule counts are not full cup/international rest or travel','Season PPG is partial archive state, not mathematical motivation','No historical pre-match lineup, injury or integrity labels trained','No trusted AFC candidate; no cross-cohort fallback','Inspected test cannot support promotion or prospective accuracy']}
 (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
 return report,path

def main():
 root=Path(sys.argv[1]);history=Path(sys.argv[2]);root.mkdir(parents=True,exist_ok=True)
 if (root/'pre-fit-protocol.json').exists():raise ValueError('experiment directory already used; preserve immutable runs')
 protocol={'version':VERSION,'created_at':datetime.now(timezone.utc).isoformat(),'history_sha256':hashlib.sha256(history.read_bytes()).hexdigest(),'features':ft.FEATURES,'models':['logistic','lightgbm','xgboost','catboost','torch'],'optuna_trials':8,'selection':'inner temporal tuning then validation logloss; diagnostic test never selects','prior_test':'already inspected; development only','future_acceptance':'prospective immutable forecasts and full denominators required','old_active_models_unchanged':True}
 (root/'pre-fit-protocol.json').write_text(json.dumps(protocol,ensure_ascii=False,indent=2)+'\n');rows=json.loads(history.read_text());registry={'version':VERSION,'status':'CANDIDATE_NOT_ACTIVE','models':{}};summaries=[]
 for cohort in sorted({r['cohort'] for r in rows}):
  subset=[r for r in rows if r['cohort']==cohort];d=root/cohort;d.mkdir();prior=json.loads(Path(f'/workspace/WCPP/models/upset-20261013/{cohort}/report.json').read_text());r,p=train(subset,prior,d)
  registry['models'][cohort]={'artifact_path':str(p),'artifact_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'report_path':str(d/'report.json'),'acceptance_gate_passed':False,'development_gate_passed':r['development_gate_passed']};summary={k:r[k] for k in ('cohort','history_rows','strong_favorite_rows','selected','diagnostic_test','market_baseline','development_gate_passed','paired_loss_brier')};summaries.append(summary);print(json.dumps(summary),flush=True)
 (root/'registry.json').write_text(json.dumps(registry,indent=2)+'\n');(root/'summary.json').write_text(json.dumps(summaries,ensure_ascii=False,indent=2)+'\n')
if __name__=='__main__':main()
