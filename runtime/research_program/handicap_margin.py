"""Reprice goal margins while holding same-book WDL and total-score evidence fixed."""
from functools import lru_cache

import numpy as np
from scipy.optimize import brentq

from . import all_scope_model as base

# Six joint cells: home/draw/away crossed with under/over 2.5.
CELL = base.RESULT*2 + (base.H + base.A > 2.5)
FLAT = CELL.ravel()


def devig(odds):
    prices = np.asarray(odds, dtype=float)
    if not np.all(np.isfinite(prices)) or np.any(prices <= 1):
        raise ValueError('decimal prices must be finite and above one')
    inv = 1/prices
    return inv/inv.sum()


def two_market(prior, wdl, total):
    """KL projection onto two marginals via a scalar over/under odds multiplier."""
    masses = np.bincount(FLAT,weights=prior.ravel(),minlength=6).reshape(3,2)
    if np.min(masses)<=0:
        raise ValueError('both total regions in every WDL class must have mass')
    target=devig(wdl)
    over=devig(total)[0]
    def excess(log_multiplier):
        odds = np.exp(log_multiplier)*masses[:,1]
        return target @ (odds/(masses[:,0]+odds)) - over
    t=np.exp(brentq(excess,-40,40,xtol=1e-12))
    joint=masses * [1.,t]
    joint=(joint/joint.sum(axis=1)[:,None])*target[:,None]
    return prior*(joint/masses).ravel()[CELL]


@lru_cache(maxsize=128)
def stake_codes(quarter):
    """Twice each fraction of the home stake won or lost, including quarter splits."""
    parts=(quarter,quarter) if quarter%2==0 else (quarter-1,quarter+1)
    d=4*(base.H-base.A)
    won=sum((d+part>0).astype(int) for part in parts)
    lost=sum((d+part<0).astype(int) for part in parts)
    levels,inverse=np.unique((won*3+lost).ravel(),return_inverse=True)
    return (levels//3)*.5,(levels%3)*.5,inverse.ravel()


def handicap_update(control, line, prices):
    if not np.isfinite(line) or not float(4*line).is_integer():
        raise ValueError('home Asian handicap must be a quarter line')
    p=devig(prices)[0]
    win,loss,level=stake_codes(round(4*line))
    signal=(1-p)*win - p*loss
    width=len(signal)
    buckets=np.bincount(FLAT*width+level,weights=control.ravel(),minlength=6*width).reshape(6,width)
    masses=buckets.sum(axis=1)
    if np.all(np.count_nonzero(buckets,axis=1)<=1):
        return control,0.,False
    def residual(theta):
        weights = buckets*np.exp(theta*signal)
        return masses @ ((weights @ signal)/weights.sum(axis=1))
    lo,hi=-4.,4.
    if residual(lo)>=0:
        theta=lo
    elif residual(hi)<=0:
        theta=hi
    else:
        theta=brentq(residual,lo,hi,xtol=1e-11)
    factor=np.exp(theta*signal)
    norm=masses/(buckets @ factor)
    out=control*(norm[FLAT]*factor[level]).reshape(control.shape)
    return out,float(theta),True
