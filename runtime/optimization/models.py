"""Independent regularized Poisson strengths and OOF residual learners."""
import math
from datetime import date
import numpy as np
from scipy import sparse
from scipy.optimize import minimize
from scipy.stats import poisson
import lightgbm as lgb
from features import GROUPS
from schema import fixture

def matrix(rates):
 rates=np.clip(np.asarray(rates,float),.08,8.)
 size=max(16,int(poisson.isf(1e-13,float(max(rates))))+1)
 p=[poisson.pmf(np.arange(size),r) for r in rates];m=np.outer(*p);m/=m.sum()
 return m

def design(rows,teams):
 index={t:i for i,t in enumerate(teams)};n=len(teams);rr=[];cc=[];vv=[]
 for i,r in enumerate(rows):
  for side,(t,o) in enumerate([(r['home'],r['away']),(r['away'],r['home'])]):
   vals={0:1.,1:float(side==0 and r.get('neutral') is not True),2:float(r.get('neutral') is None)}
   if t in index:vals[3+index[t]]=1.
   if o in index:vals[3+n+index[o]]=1.
   for k,v in vals.items():rr.append(2*i+side);cc.append(k);vv.append(v)
 return sparse.csr_matrix((vv,(rr,cc)),shape=(2*len(rows),3+2*n))

def fit_core(rows,half_life=365,ridge=20):
 if len(rows)<80:raise ValueError('insufficient core history')
 rows=sorted(rows,key=lambda r:(r['date'],r['match_id']));teams=sorted({r[k] for r in rows for k in ('home','away')});X=design(rows,teams)
 y=np.asarray([r[k] for r in rows for k in ('home_goals','away_goals')],float)
 end=date.fromisoformat(rows[-1]['date']);w=np.repeat([2**(-(end-date.fromisoformat(r['date'])).days/half_life) for r in rows],2)
 penalty=np.full(X.shape[1],float(ridge));penalty[:3]=[.001,1.,1.]
 def loss(b):
  eta=np.clip(X@b,-5,4);mu=np.exp(eta)
  return float(np.sum(w*(mu-y*eta))+.5*np.sum(penalty*b*b)),np.asarray(X.T@(w*(mu-y)))+penalty*b
 b=np.zeros(X.shape[1]);b[0]=math.log(max(.1,y.mean()))
 result=minimize(loss,b,jac=True,method='L-BFGS-B',options={'maxiter':200,'ftol':1e-9})
 if not result.success:raise ValueError('core convergence: '+result.message)
 return {'teams':teams,'coefficients':result.x.tolist(),'half_life':half_life,'ridge':ridge,'fit_end':rows[-1]['date'],'fit_n':len(rows)}

def rates(core,rows):return np.clip(np.exp(design(rows,core['teams'])@np.asarray(core['coefficients'])),.08,8.).reshape(-1,2)

def fit_residual(rows,history,core_config,group):
 # Baseline offsets for each training target come from an earlier fit only.
 dates=sorted({r['date'] for r in rows});edges=sorted(set(dates[int(len(dates)*p)] for p in (.40,.60,.80)))
 xx=[];yy=[];oo=[];folds=[];coverage=[]
 for i,start in enumerate(edges):
  end=edges[i+1] if i+1<len(edges) else '9999-12-31'
  prior=[r for r in rows if r['date']<start];targets=[r for r in rows if start<=r['date']<end]
  if len(prior)<120 or not targets:continue
  core=fit_core(prior,**core_config);offset=rates(core,targets).ravel();X=history.batch(targets,origin=start)[:,GROUPS[group]]
  coverage.append({'fit_end':prior[-1]['date'],'targets':len(targets),'both_sides_xg_ge3':int(np.sum(np.all(history.batch(targets,origin=start)[:,[24,27]].reshape(-1,2,2)[:,:,0]>=3,axis=1))) if group=='xg' else None});xx.append(X);oo.extend(offset);yy.extend(r[k] for r in targets for k in ('home_goals','away_goals'));folds.append({'fit_end':prior[-1]['date'],'target_start':start,'target_end':targets[-1]['date'],'targets':len(targets)})
 if len(yy)<200:raise ValueError('insufficient OOF residual targets')
 data=lgb.Dataset(np.vstack(xx),label=np.asarray(yy,float),init_score=np.log(oo),free_raw_data=False)
 booster=lgb.train({'objective':'poisson','learning_rate':.04,'num_leaves':7,'max_depth':3,'min_data_in_leaf':60,'lambda_l2':20.,'verbosity':-1,'num_threads':1,'seed':20261015,'deterministic':True,'force_col_wise':True},data,num_boost_round=70)
 return booster,{'oof_folds':folds,'oof_matches':len(yy)//2,'group':group,'oof_feature_coverage':coverage}

def predict(core,rows,history,booster=None,group='core',alpha=0.,origin=None):
 base=rates(core,rows)
 if booster is None:return base
 x=history.batch(rows,origin=origin)[:,GROUPS[group]]
 residual=np.clip(booster.predict(x,raw_score=True),-.45,.45).reshape(-1,2)
 return np.clip(base*np.exp(alpha*residual),.08,8.)
