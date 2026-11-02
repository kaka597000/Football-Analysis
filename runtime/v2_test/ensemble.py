"""Independent retrospective softmax gate and temperature calibration experiment.

Input base forecasts must be source-reviewed and fitted before each evaluation
date. This module cannot certify first-known data availability or promotion.
"""
import numpy as np
from datetime import date
from scipy.optimize import minimize, minimize_scalar
try:
    from .evaluation import evaluate
except ImportError:
    from evaluation import evaluate


def matrix(value):
    m=np.asarray(value,dtype=float)
    if m.ndim!=2 or not np.isfinite(m).all() or (m<0).any() or abs(m.sum()-1)>1e-9:
        raise ValueError('normalized finite score matrix required')
    return m


def design(matrices):
    features=[1.]
    for value in matrices:
        m=matrix(value);h,a=np.indices(m.shape)
        features.extend([float((h*m).sum()),float((a*m).sum()),
                         float(-(m*np.log(np.maximum(m,1e-15))).sum())])
    return np.asarray(features)


def weights(x,beta):
    z=x@beta;z-=z.max(axis=-1,keepdims=True)
    v=np.exp(z);return v/v.sum(axis=-1,keepdims=True)


def mix(matrices,w,temperature=1.):
    if not .5<=temperature<=2.:raise ValueError('bounded temperature required')
    arrays=[matrix(m) for m in matrices]
    if len({m.shape for m in arrays})!=1:raise ValueError('aligned matrix supports required')
    w=np.asarray(w,float)
    if w.shape!=(len(arrays),) or not np.isfinite(w).all() or (w<0).any() or abs(w.sum()-1)>1e-9:
        raise ValueError('valid mixture weights required')
    m=sum(a*b for a,b in zip(arrays,w))
    # Preserve structural zeroes and the complete joint matrix.
    m=m**(1/temperature);return m/m.sum()


def train(rows, *, regularization=.1):
    if not np.isfinite(regularization) or regularization<0:raise ValueError('nonnegative finite penalty required')
    if len(rows)<100:raise ValueError('at least 100 development observations required')
    if len({r['cohort'] for r in rows})!=1:raise ValueError('one league per experiment')
    if len({r['match_id'] for r in rows})!=len(rows):raise ValueError('duplicate matches')
    rows=sorted(rows,key=lambda r:(r['date'],r['match_id']))
    k=len(rows[0]['matrices'])
    if k<2:raise ValueError('multiple independent experts required')
    for r in rows:
        date.fromisoformat(r['date'])
        for d in r['base_fit_ends']:date.fromisoformat(d)
        if len(r['matrices'])!=k or not all(t<r['date'] for t in r['base_fit_ends']) or len(r['base_fit_ends'])!=k:
            raise ValueError('base fit must precede prediction date')
        if any(type(r[x]) is not int or r[x]<0 for x in ('home_goals','away_goals')):raise ValueError('score labels')
        for m in r['matrices']:
            m=matrix(m)
            if r['home_goals']>=m.shape[0] or r['away_goals']>=m.shape[1]:raise ValueError('label outside support')
        if len({matrix(m).shape for m in r['matrices']})!=1:raise ValueError('matrix alignment')
    dates=sorted({r['date'] for r in rows})
    if len(dates)<15:raise ValueError('insufficient independent dates')
    c1,c2=dates[int(len(dates)*.6)],dates[int(len(dates)*.8)]
    fit=[r for r in rows if r['date']<c1]
    calibration=[r for r in rows if c1<=r['date']<c2]
    diagnostic=[r for r in rows if r['date']>=c2]
    x=np.stack([design(r['matrices']) for r in fit]);mean=x[:,1:].mean(0);scale=x[:,1:].std(0);scale[scale<1e-8]=1
    def transform(rs):
        z=np.stack([design(r['matrices']) for r in rs]);z[:,1:]=(z[:,1:]-mean)/scale;return z
    x=transform(fit)
    true=np.array([[matrix(m)[r['home_goals'],r['away_goals']] for m in r['matrices']] for r in fit])
    def objective(flat):
        b=flat.reshape(x.shape[1],k)
        return -np.log(np.maximum((weights(x,b)*true).sum(1),1e-15)).mean()+regularization*(b*b).mean()
    fitted=minimize(objective,np.zeros(x.shape[1]*k),method='L-BFGS-B',options={'maxiter':300})
    if not fitted.success:raise ValueError('gate optimizer failed')
    beta=fitted.x.reshape(x.shape[1],k)
    def predict(rs,temp=1.,equal=False):
        ws=np.full((len(rs),k),1/k) if equal else weights(transform(rs),beta)
        return [mix(r['matrices'],w,temp) for r,w in zip(rs,ws)]
    def loss(temp):
        return -np.mean([np.log(max(m[r['home_goals'],r['away_goals']],1e-15)) for r,m in zip(calibration,predict(calibration,temp))])
    opt=minimize_scalar(loss,bounds=(.5,2.),method='bounded')
    if not opt.success:raise ValueError('temperature optimizer failed')
    # Include identity temperature; select only on calibration, never diagnostic.
    temperature=min((1.,float(opt.x)),key=loss)
    def score(ms):return evaluate([dict(matrix=m,home_goals=r['home_goals'],away_goals=r['away_goals']) for r,m in zip(diagnostic,ms)])
    return {'schema':'WCPP-V2-RETROSPECTIVE-GATE-1','cohort':rows[0]['cohort'],
        'beta':beta.tolist(),'feature_mean':mean.tolist(),'feature_scale':scale.tolist(),
        'temperature':temperature,'regularization':regularization,
        'split':{'fit_n':len(fit),'calibration_n':len(calibration),'diagnostic_n':len(diagnostic),
                 'gate_fit_before':c1,'temperature_fit_before':c2},
        'diagnostics':{'equal_weight':score(predict(diagnostic,equal=True)),
                       'dynamic':score(predict(diagnostic)),
                       'dynamic_calibrated':score(predict(diagnostic,temperature)),
                       'first_expert':score([matrix(r['matrices'][0]) for r in diagnostic])},
        'promotion':'DO_NOT_PROMOTE','historical_asof_certified':False,
        'notice':'Already-inspected retrospective development data; not untouched/prospective results. '
                 'No player-impact coefficients fitted; no active-model writes.'}
