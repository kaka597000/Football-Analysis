"""Baseline-anchored development experiment. No automatic promotion."""
import numpy as np
from scipy.optimize import minimize
try:
 from .ensemble import train,design,weights,mix,matrix
 from .evaluation import evaluate
except ImportError:
 from ensemble import train,design,weights,mix,matrix
 from evaluation import evaluate

def optimize(rows):
 old=train(rows,include_diagnostics=False)
 rows=sorted(rows,key=lambda r:(r['date'],r['match_id']))
 c1=old['split']['gate_fit_before'];c2=old['split']['temperature_fit_before']
 fit=[r for r in rows if r['date']<c1];cal=[r for r in rows if c1<=r['date']<c2];end=[r for r in rows if r['date']>=c2]
 k=len(fit[0]['matrices']);anchor=np.eye(k)[0]
 true=np.array([[matrix(m)[r['home_goals'],r['away_goals']] for m in r['matrices']] for r in fit])
 # Fit k static coefficients instead of (1+3k)*k gate coefficients.
 def loss(w):return -np.log(np.maximum(true@w,1e-15)).mean()+.1*np.square(w-anchor).sum()
 fitted=minimize(loss,anchor,method='SLSQP',bounds=[(0,1)]*k,
                 constraints={'type':'eq','fun':lambda w:w.sum()-1},options={'maxiter':300,'ftol':1e-10})
 if not fitted.success:raise ValueError('static optimizer failed')
 static=np.maximum(fitted.x,0);static/=static.sum()
 def predict(rs,kind,temp):
  if kind=='baseline':ws=np.tile(anchor,(len(rs),1))
  elif kind=='static':ws=np.tile(static,(len(rs),1))
  else:
   x=np.stack([design(r['matrices']) for r in rs]);x[:,1:]=(x[:,1:]-old['feature_mean'])/old['feature_scale']
   raw=weights(x,np.array(old['beta']))
   ws=raw if kind=='original_dynamic' else .75*anchor+.25*raw
  return [mix(r['matrices'],w,temp) for r,w in zip(rs,ws)]
 def score(rs,ms):return evaluate([dict(matrix=m,home_goals=r['home_goals'],away_goals=r['away_goals']) for r,m in zip(rs,ms)])
 base=score(cal,predict(cal,'baseline',1.))['metrics'];candidates=[]
 for kind in ('baseline','static','anchored_dynamic'):
  for temp in ((1.,) if kind=='baseline' else (.95,1.,1.05)):
   metrics=score(cal,predict(cal,kind,temp))['metrics']
   eligible=metrics['exact_top1']>=base['exact_top1'] and metrics['score_log_loss']<=base['score_log_loss']+.02
   candidates.append({'kind':kind,'temperature':temp,'calibration':metrics,'eligible':eligible})
 selected=min([c for c in candidates if c['eligible']],key=lambda c:(-c['calibration']['exact_top1'],c['calibration']['score_log_loss']))
 # Selection locked above; end labels are only used for diagnostic reporting below.
 return {'schema':'WCPP-V2-GUARDED-ENSEMBLE-1','split':old['split'],'static_weights':static.tolist(),
  'selected':selected,'calibration_candidates':candidates,
  'diagnostic':score(end,predict(end,selected['kind'],selected['temperature'])),
  'baseline_diagnostic':score(end,predict(end,'baseline',1.)),
  'old_dynamic_diagnostic':score(end,predict(end,'original_dynamic',old['temperature'])),
  'policy':{'baseline_anchor_in_dynamic':.75,'temperature_grid':[.95,1.,1.05],
            'selection':'calibration Top1 then Log Loss, loss guard +.02, baseline eligible'},
  'promotion':'DO_NOT_PROMOTE','historical_asof_certified':False,
  'notice':'Method changed after examining old diagnostic. All results are post-hoc development diagnostics, not fresh unseen validation.'}
