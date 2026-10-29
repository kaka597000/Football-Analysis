"""Past-date-only research features. Does not modify the active v0.1 classifier."""
import sys, math
from pathlib import Path
from collections import defaultdict
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'upset'))
import classifier as old
BASE=list(old.FEATURES)
SIDE=['venue_ppg','venue_gf','venue_ga','venue_n','league_games_7d','league_games_14d','result_variance','opponent_elo_mean','elo_result_residual','season_ppg','season_games','historical_market_residual','historical_market_n']
PROCESS=['shots','sot','corners','yellow','red','xg','research_xg']
FEATURES=BASE+['market_overround']+[p+k for p in ('favorite_','underdog_') for k in SIDE]+[p+k+s for p in ('favorite_','underdog_') for k in PROCESS for s in ('_for','_against','_coverage')]
GROUPS={'market':list(range(4))+[17], 'state':list(range(17))+list(range(18,44)), 'full':list(range(len(FEATURES)))}

def season_of(row):
 # Prefer explicit archive season. July boundary fallback is an approximation for these nine European cohorts only.
 return row.get('season') or str(int(row['date'][:4])-(int(row['date'][5:7])<7))

class State:
 def __init__(self):self.base=old.State();self.games=defaultdict(list)
 def features(self,r,odds):
  v=self.base.features(r,odds).tolist();side,p=old.favorite(odds);v.append(sum(1/o for o in odds)-1)
  f,u=r[side],r['away' if side=='home' else 'home'];today=np.datetime64(r['date']);features=[];proc=[]
  for team,venue in ((f,'home' if side=='home' else 'away'),(u,'away' if side=='home' else 'home')):
   gs=self.games[team];recent=gs[-6:];same=[g for g in gs if g['venue']==venue][-6:];season=[g for g in gs if g['season']==season_of(r)]
   def mean(xs):return float(np.mean(xs)) if xs else math.nan
   mr=[g['market_residual'] for g in recent if g['market_residual'] is not None]
   features += [mean([g['points'] for g in same]),mean([g['gf'] for g in same]),mean([g['ga'] for g in same]),len(same),sum(0<int((today-np.datetime64(g['date'])).astype('timedelta64[D]').astype(int))<=7 for g in gs),sum(0<int((today-np.datetime64(g['date'])).astype('timedelta64[D]').astype(int))<=14 for g in gs),float(np.var([g['gf']-g['ga'] for g in recent])) if recent else math.nan,mean([g['opponent_elo'] for g in recent]),mean([g['elo_residual'] for g in recent]),mean([g['points'] for g in season]),len(season),mean(mr),len(mr)]
   for k in PROCESS:
    pairs=[g['process'][k] for g in recent if k in g['process']]
    proc += [mean([x[0] for x in pairs]),mean([x[1] for x in pairs]),len(pairs)/max(1,len(recent))]
  return np.asarray(v+features+proc,float)
 def update(self,batch):
  for r in batch:
   h,a=r['home'],r['away'];hg,ag=r['home_goals'],r['away_goals'];market=old.fair(r['odds']) if r.get('odds') else None
   eh=self.base.elo.get(h,1500);ea=self.base.elo.get(a,1500);expected=1/(1+10**((ea-eh-65)/400))
   for team,opp,gf,ga,venue,opp_elo,exp,idx in ((h,a,hg,ag,'home',ea,expected,0),(a,h,ag,hg,'away',eh,1-expected,2)):
    y=1 if gf>ga else .5 if gf==ga else 0;proc={}
    source=r.get('process',{})
    for k in PROCESS:
     own=source.get(f'{venue}_{k}');other=source.get(f'{"away" if venue=="home" else "home"}_{k}')
     if all(type(x) in (float,int) and math.isfinite(x) and 0<=x<=100 for x in (own,other)):proc[k]=(own,other)
    self.games[team].append({'date':r['date'],'season':season_of(r),'venue':venue,'gf':gf,'ga':ga,'points':3 if gf>ga else 1 if gf==ga else 0,'opponent_elo':opp_elo,'elo_residual':y-exp,'market_residual':y-(market[idx]+.5*market[1]) if market is not None else None,'process':proc})
  self.base.update_batch(batch)

def items(rows):
 rows=old.history_validate(rows);state=State();groups=defaultdict(list);out=[]
 for r in rows:groups[r['date']].append(r)
 for date,batch in sorted(groups.items()):
  for r in batch:
   if not r.get('odds') or not old.favorite(r['odds']):continue
   side,p=old.favorite(r['odds']);diff=(r['home_goals']-r['away_goals'])*(1 if side=='home' else -1)
   out.append({'row':r,'features':state.features(r,r['odds']),'label':0 if diff>0 else 1 if diff==0 else 2,'market':p[[0,1,2] if side=='home' else [2,1,0]]})
  state.update(batch)
 return out
