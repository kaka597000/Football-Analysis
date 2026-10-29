"""Prior-only side features; fixed-origin evaluation never consumes audit labels."""
from collections import defaultdict
from datetime import date
import numpy as np
from schema import digest, fixture, validate_fixture

NAMES=['listed_home','venue_unknown','goals_for','goals_against','points','same_cohort_gap','history_n','opp_goals_for','opp_goals_against','opp_points','opp_gap','opp_history_n', 'shots_for','shots_against','sot_for','sot_against','process_n','opp_shots_for','opp_shots_against','opp_sot_for','opp_sot_against','opp_process_n', 'understat_xg','understat_xga','understat_n','opp_understat_xg','opp_understat_xga','opp_understat_n']
GROUPS={'core':list(range(12)), 'process':list(range(22)), 'xg':list(range(28))}
class History:
 def __init__(self,fixtures,labels,facts,lag_days=7):
  self.fixtures={r['match_id']:r for r in fixtures};self.labels=labels;self.lag=lag_days
  self.games=defaultdict(list);self.facts=defaultdict(list)
  for r in fixtures:
   if r['match_id'] in labels:
    for t in (r['home'],r['away']): self.games[t].append(r)
  for x in self.games.values():x.sort(key=lambda r:(r['date'],r['match_id']))
  for fact in facts:
   r=self.fixtures.get(fact['match_id'])
   if r:
    for t in (r['home'],r['away']):self.facts[(t,fact['kind'],fact['provider'])].append(fact)
  for x in self.facts.values():x.sort(key=lambda r:(r['date'],r['fact_id']))
 def vector(self,target,origin=None):
  validate_fixture(target);day=min(target['date'],origin or target['date']);used=set();source=[]
  def side(t):
   games=[r for r in self.games[t] if r['date']<day][-6:]
   gf=[];ga=[];pts=[]
   for r in games:
    y=self.labels[r['match_id']];h=r['home']==t;a,b=(y['home_goals'],y['away_goals']) if h else (y['away_goals'],y['home_goals'])
    gf.append(a);ga.append(b);pts.append(3 if a>b else 1 if a==b else 0);used.add(r['match_id'])
   n=len(games);base=[np.mean(gf) if n else np.nan,np.mean(ga) if n else np.nan,np.mean(pts) if n else np.nan,min(60,(date.fromisoformat(target['date'])-date.fromisoformat(games[-1]['date'])).days) if n else np.nan,n]
   additions=[]
   for kind,provider,fields in [('process','football-data.co.uk',['shots','sot']),('xg','understat',['xg'])]:
    fs=[f for f in self.facts[(t,kind,provider)] if (date.fromisoformat(day)-date.fromisoformat(f['date'])).days>self.lag][-6:]
    vals=[]
    for fld in fields:
     vfor=[];vagainst=[]
     for f in fs:
      r=self.fixtures[f['match_id']];h=r['home']==t
      a=f.get(('home_' if h else 'away_')+fld);b=f.get(('away_' if h else 'home_')+fld)
      if a is not None and b is not None:vfor.append(a);vagainst.append(b);source.append(f['fact_id'])
     vals.extend([float(np.mean(vfor)) if vfor else np.nan,float(np.mean(vagainst)) if vagainst else np.nan])
    additions.extend(vals+[len(fs)])
   return base,additions[:5],additions[5:]
  h,hp,hx=side(target['home']);a,ap,ax=side(target['away'])
  unknown=float(target.get('neutral') is None)
  x=np.asarray([[1.,unknown]+h+a+hp+ap+hx+ax,[0.,unknown]+a+h+ap+hp+ax+hx],float)
  return x,{'historical_match_ids':sorted(used),'fact_ids':sorted(set(source)),'origin':day,'lag_days':self.lag,'provider_channels':['football-data.co.uk:process','understat:xg'],'first_known_at':None,'time_track':'RETROSPECTIVE_PRIOR_ONLY'}
 def batch(self,rows,origin=None):
  return np.vstack([self.vector(fixture(r),origin)[0] for r in rows])
