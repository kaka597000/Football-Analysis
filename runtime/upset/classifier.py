"""Independent research classifier of strong market favorites' 90-minute outcomes."""
import json,math,hashlib
from pathlib import Path
from datetime import datetime,timezone
import numpy as np
VERSION='WCPP-UPSET-0.1'
CLASSES=('favorite_win','draw','favorite_loss')
FEATURES=('fair_favorite','fair_draw','fair_underdog','favorite_listed_home','favorite_elo_minus_underdog',
          'favorite_recent_ppg','underdog_recent_ppg','favorite_recent_gf','favorite_recent_ga',
          'underdog_recent_gf','underdog_recent_ga','favorite_league_gap_days','underdog_league_gap_days',
          'favorite_recent_n','underdog_recent_n','favorite_cold_start','underdog_cold_start')
CONFIG={'strong_favorite_min_fair_probability':.5,'recent_games':6,'elo_k':20.,'elo_listed_home_advantage':65.,
        'C_candidates':[.1,1.,10.],'split_fractions':[.65,.2,.15],'min_eligible_rows':250,
        'high_alert_min_calibration_count':20,'high_alert_min_recall':.15,'alert_candidates':[.2,.25,.3,.35,.4,.45,.5,.55,.6]}
def aware(t):
 d=datetime.fromisoformat(t.replace('Z','+00:00'))
 if d.tzinfo is None:raise ValueError('timezone required')
 return d

def code_hash():return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
def fair(odds):
 if len(odds)!=3 or any(type(v) not in (float,int) or not math.isfinite(v) or v<=1 or v>1000 for v in odds):raise ValueError('invalid decimal 1X2 odds')
 inv=np.array([1/v for v in odds]);return inv/inv.sum()
def favorite(odds):
 p=fair(odds);i=int(np.argmax(p))
 if i==1 or p[i]<CONFIG['strong_favorite_min_fair_probability']:return None
 return ('home' if i==0 else 'away'),p

def history_validate(rows):
 seen=set();cohorts=set()
 for r in rows:
  if r['match_id'] in seen:raise ValueError('duplicate history fixture')
  seen.add(r['match_id']);cohorts.add(r['cohort'])
  if r.get('score_basis')!='regulation_90' or r['home']==r['away']:raise ValueError('90-minute identity required')
  if any(type(r[k]) is not int or not 0<=r[k]<=30 for k in ('home_goals','away_goals')):raise ValueError('invalid history score')
  datetime.strptime(r['date'],'%Y-%m-%d')
  if r['date']>=datetime.now(timezone.utc).date().isoformat():raise ValueError('future/current-day result in training')
 if len(cohorts)!=1:raise ValueError('separate competition cohorts required')
 return sorted(rows,key=lambda r:(r['date'],r['match_id']))

class State:
 def __init__(self):self.elo={};self.games={}
 def features(self,fixture,odds):
  fav=favorite(odds)
  if fav is None:return None
  side,p=fav;f=fixture[side];u=fixture['away' if side=='home' else 'home'];fi=0 if side=='home' else 2
  v=[p[fi],p[1],p[2-fi],float(side=='home'),self.elo.get(f,1500)-self.elo.get(u,1500)]
  def stats(t):
   gs=self.games.get(t,[])[-CONFIG['recent_games']:];n=len(gs)
   if not n:return [math.nan]*4+[0.,1.]
   return [sum(x['points'] for x in gs)/n,sum(x['gf'] for x in gs)/n,sum(x['ga'] for x in gs)/n,
           min(30.,(datetime.fromisoformat(fixture['date'])-datetime.fromisoformat(gs[-1]['date'])).days),float(n),float(n<3)]
  fs,us=stats(f),stats(u)
  return np.array(v+[fs[0],us[0],fs[1],fs[2],us[1],us[2],fs[3],us[3],fs[4],us[4],fs[5],us[5]],dtype=float)
 def update_batch(self,batch):
  delta={}
  for r in batch:
   h,a=r['home'],r['away'];hg,ag=r['home_goals'],r['away_goals'];y=1. if hg>ag else .5 if hg==ag else 0.
   expected=1/(1+10**((self.elo.get(a,1500)-self.elo.get(h,1500)-CONFIG['elo_listed_home_advantage'])/400))
   step=CONFIG['elo_k']*(y-expected);delta[h]=delta.get(h,0)+step;delta[a]=delta.get(a,0)-step
   for t,gf,ga in [(h,hg,ag),(a,ag,hg)]:self.games.setdefault(t,[]).append({'date':r['date'],'gf':gf,'ga':ga,'points':3 if gf>ga else 1 if gf==ga else 0})
  for t,d in delta.items():self.elo[t]=self.elo.get(t,1500)+d

def items(rows):
 state=State();out=[]
 for date in sorted({r['date'] for r in rows}):
  batch=[r for r in rows if r['date']==date]
  for r in batch:
   odds=r.get('odds');fav=favorite(odds) if odds else None
   if not fav:continue
   x=state.features(r,odds);side,p=fav;win=(r['home_goals']-r['away_goals'])*(1 if side=='home' else -1)
   out.append({'row':r,'features':x,'label':0 if win>0 else 1 if win==0 else 2,'market':[p[0] if side=='home' else p[2],p[1],p[2] if side=='home' else p[0]]})
  state.update_batch(batch)
 return out

def fit_model(x,y,C):
 from sklearn.linear_model import LogisticRegression
 med=np.nanmedian(x,axis=0);med=np.nan_to_num(med,nan=0.);filled=np.where(np.isnan(x),med,x);mean=filled.mean(axis=0);scale=np.maximum(filled.std(axis=0),1e-6)
 model=LogisticRegression(C=C,max_iter=1000,solver='lbfgs',random_state=20261013).fit((filled-mean)/scale,y)
 if list(model.classes_)!=[0,1,2]:raise ValueError('all three outcomes required')
 return {'medians':med.tolist(),'means':mean.tolist(),'scales':scale.tolist(),'coefficients':model.coef_.tolist(),'intercepts':model.intercept_.tolist(),'C':C}
def probabilities(model,x):
 a=np.asarray(x,dtype=float);a=np.where(np.isnan(a),model['medians'],a);z=(a-model['means'])/model['scales'];logits=z@np.asarray(model['coefficients']).T+model['intercepts'];logits-=logits.max(axis=-1,keepdims=True);v=np.exp(logits);return v/v.sum(axis=-1,keepdims=True)
def metrics(y,p,threshold=None):
 from sklearn.metrics import log_loss,roc_auc_score
 y=np.asarray(y);one=np.eye(3)[y];o={'n':len(y),'class_counts':dict(zip(CLASSES,map(int,np.bincount(y,minlength=3)))),'log_loss':float(log_loss(y,p,labels=[0,1,2])),'multiclass_brier':float(np.mean(np.sum((p-one)**2,axis=1))),'loss_brier':float(np.mean((p[:,2]-(y==2))**2)),'loss_auc':float(roc_auc_score(y==2,p[:,2])) if len(set(y==2))==2 else None}
 if threshold is not None:
  alert=p[:,2]>=threshold;tp=int(np.sum(alert&(y==2)));o['loss_alert']={'threshold':threshold,'count':int(alert.sum()),'true_positives':tp,'precision':float(tp/alert.sum()) if alert.any() else None,'recall':float(tp/(y==2).sum()) if (y==2).any() else None,'coverage':float(alert.mean())}
 return o

def train(rows):
 rows=history_validate(rows);it=items(rows)
 if len(it)<CONFIG['min_eligible_rows']:raise ValueError('insufficient strong-favorite archive')
 dates=sorted({x['row']['date'] for x in it});d1=dates[int(len(dates)*.65)];d2=dates[int(len(dates)*.85)]
 groups=[[x for x in it if x['row']['date']<d1],[x for x in it if d1<=x['row']['date']<d2],[x for x in it if x['row']['date']>=d2]]
 if min(map(len,groups))<50:raise ValueError('insufficient temporal partition')
 xs=[np.stack([x['features'] for x in g]) for g in groups];ys=[np.array([x['label'] for x in g]) for g in groups];candidates=[]
 for C in CONFIG['C_candidates']:
  m=fit_model(xs[0],ys[0],C);vp=probabilities(m,xs[1]);candidates.append((metrics(ys[1],vp)['log_loss'],C,m,vp))
 _,C,m,vp=min(candidates,key=lambda x:x[0]);threshold=None;best=-1;prevalence=float(np.mean(ys[1]==2))
 for t in CONFIG['alert_candidates']:
  a=metrics(ys[1],vp,t)['loss_alert']
  if a['count']<20 or a['recall']<.15 or a['precision']<prevalence+.1:continue
  f1=2*a['precision']*a['recall']/(a['precision']+a['recall'])
  if f1>best:threshold=t;best=f1
 tp=probabilities(m,xs[2]);test=metrics(ys[2],tp,threshold);base=metrics(ys[2],np.array([x['market'] for x in groups[2]]),threshold)
 passed=len(groups[2])>=100 and test['loss_brier']<base['loss_brier'] and test['log_loss']<=base['log_loss'] and test['loss_auc'] is not None and test['loss_auc']>=.6
 artifact={'version':VERSION,'cohort':rows[0]['cohort'],'features':FEATURES,'config':CONFIG,'model':m,'history':rows,'train_end':max(x['row']['date'] for x in groups[0]),'calibration_end':max(x['row']['date'] for x in groups[1]),'history_end':rows[-1]['date'],'built_at':datetime.now(timezone.utc).isoformat(),'code_hash':code_hash(),'alert_threshold':threshold,'status':'RESEARCH_ONLY','acceptance_gate_passed':bool(passed),'timestamp_coverage':'historical pre-match odds columns; precise quote capture time unavailable','targets':{'favorite_loss':'market favorite loses in 90 minutes','favorite_nonwin':'draw or favorite loss; NOT the same target'}}
 report={'version':VERSION,'cohort':rows[0]['cohort'],'history_rows':len(rows),'strong_favorite_rows':len(it),'date_split':{'train_end_exclusive':d1,'test_start':d2,'counts':list(map(len,groups))},'selected_C':C,'calibration':metrics(ys[1],vp,threshold),'test':test,'market_baseline':base,'acceptance_gate_passed':bool(passed),'out_of_time_but_retrospective':True,'limitations':['Exact historical market timestamps unavailable','No official lineup/injury/motivation features trained','No AFC or cross-league fallback','Outcome accuracy is not exact-score Top1 accuracy','Held-out evaluation is not prospective validation']}
 artifact['report_hash']=hashlib.sha256(json.dumps(report,sort_keys=True).encode()).hexdigest()
 return artifact,report

def predict(artifact,fixture,quote):
 if artifact['version']!=VERSION or artifact['code_hash']!=code_hash() or artifact['config']!=CONFIG:raise ValueError('classifier artifact mismatch')
 if fixture['cohort']!=artifact['cohort']:raise ValueError('classifier cohort mismatch')
 cutoff=aware(fixture['cutoff_at']);kickoff=aware(fixture['kickoff_at'])
 if not cutoff<=datetime.now(timezone.utc)<kickoff or cutoff>=kickoff or aware(artifact['built_at'])>cutoff:raise ValueError('genuine future forecast with prior artifact required')
 if quote.get('market')!='1x2' or quote.get('match_id')!=fixture['match_id'] or quote.get('verified') is not True:raise ValueError('verified same-match quote required')
 from urllib.parse import urlparse
 u=urlparse(quote.get('source_url',''))
 if u.scheme!='https' or not u.netloc or u.username or u.password or not quote.get('bookmaker'):raise ValueError('named HTTPS quote required')
 if aware(quote['query_time'])>cutoff or quote.get('quoted_at') and (aware(quote['quoted_at'])>cutoff or aware(quote['quoted_at'])>aware(quote['query_time'])):raise ValueError('post-cutoff quote')
 odds=[quote[k] for k in ('home_odds','draw_odds','away_odds')];fav=favorite(odds)
 if not fav:return {'version':VERSION,'status':'ABSTAIN_NO_STRONG_MARKET_FAVORITE','match_id':fixture['match_id']}
 if fixture.get('neutral') is not False:return {'version':VERSION,'status':'ABSTAIN_NEUTRAL_VENUE_SCOPE','match_id':fixture['match_id']}
 rows=history_validate(artifact['history']);state=State();date=kickoff.date().isoformat()
 for day in sorted({r['date'] for r in rows if r['date']<date}):state.update_batch([r for r in rows if r['date']==day])
 x=state.features(dict(fixture,date=date),odds);p=probabilities(artifact['model'],x[None,:])[0];t=artifact['alert_threshold'];z=(np.where(np.isnan(x),artifact['model']['medians'],x)-artifact['model']['means'])/artifact['model']['scales'];effects=z*(np.array(artifact['model']['coefficients'])[2]-np.array(artifact['model']['coefficients'])[0]);order=np.argsort(-np.abs(effects))[:5]
 return {'version':VERSION,'status':'RESEARCH_ONLY','match_id':fixture['match_id'],'cohort':fixture['cohort'],'favorite_side':fav[0],'probabilities':dict(zip(CLASSES,map(float,p))),'favorite_nonwin_probability':float(p[1]+p[2]),'loss_probability_difference_vs_market':float(p[2]-fav[1][2 if fav[0]=='home' else 0]),'market_reference':{'favorite_win':float(fav[1][0 if fav[0]=='home' else 2]),'draw':float(fav[1][1]),'favorite_loss':float(fav[1][2 if fav[0]=='home' else 0])},'risk_label':'UNVALIDATED_NO_ACTION_ALERT' if not artifact['acceptance_gate_passed'] else 'HIGH_LOSS_RISK_RESEARCH' if t is not None and p[2]>=t else 'NO_HIGH_ALERT' if t is not None else 'ALERT_THRESHOLD_NOT_VALIDATED','alert_threshold':t,'research_threshold_exceeded':bool(t is not None and p[2]>=t),'association_factors':[{'feature':FEATURES[i],'value':None if math.isnan(x[i]) else float(x[i]),'loss_vs_win_logit_contribution':float(effects[i])} for i in order],'cold_start':{'favorite':bool(x[-2]),'underdog':bool(x[-1])},'acceptance_gate_passed':artifact['acceptance_gate_passed'],'data_cutoff':fixture['cutoff_at'],'generated_at':datetime.now(timezone.utc).isoformat(),'kickoff_at':fixture['kickoff_at'],'market_quote':quote,'probability_calibration':'RETROSPECTIVE_NOT_PROSPECTIVE','missing_modules':['official_lineup','injuries','mathematical_motivation','full_schedule','integrity'],'main_score_reordered':False,'formal_kpi_eligible':False}

def attach(fixture,registry_path='/workspace/WCPP/runtime/upset-classifier.json'):
 base={'version':VERSION,'match_id':fixture['match_id'],'main_score_reordered':False,'formal_kpi_eligible':False}
 p=Path(registry_path)
 if not p.exists():return dict(base,status='ABSTAIN_NO_CLASSIFIER_REGISTRY')
 registry=json.loads(p.read_text());route=registry.get('models',{}).get(fixture['cohort'])
 if not route:return dict(base,status='ABSTAIN_NO_TRAINED_COHORT')
 quote=fixture.get('wdl_quote')
 if not quote:return dict(base,status='ABSTAIN_MISSING_1X2_QUOTE')
 ap=Path(route['artifact_path'])
 if hashlib.sha256(ap.read_bytes()).hexdigest()!=route['artifact_sha256']:raise ValueError('classifier checksum mismatch')
 return predict(json.loads(ap.read_text()),fixture,quote)

def review(records,results):
 """Separate classifier outcome accounting; no mixing with score predictions."""
 result_map={x['match_id']:x for x in results}
 if len(result_map)!=len(results):raise ValueError('duplicate classifier result')
 seen=set();settled=[];pending=[];abstained=[]
 for r in records:
  mid=r['match_id']
  if mid in seen:raise ValueError('duplicate classifier forecast')
  seen.add(mid)
  if r['status']!='RESEARCH_ONLY':abstained.append(mid);continue
  if mid not in result_map:pending.append(mid);continue
  a=result_map[mid]
  if a.get('score_basis')!='regulation_90' or any(type(a.get(k)) is not int or not 0<=a[k]<=30 for k in ('home_goals','away_goals')):raise ValueError('verified 90 minute score required')
  from urllib.parse import urlparse
  from datetime import timedelta
  if urlparse(a.get('source_url','')).scheme!='https':raise ValueError('result source required')
  if not aware(r['data_cutoff'])<=aware(r['generated_at'])<aware(r['kickoff_at']):raise ValueError('classifier not generated before kickoff')
  if not aware(r['kickoff_at'])+timedelta(minutes=90)<=aware(a['observed_at'])<=datetime.now(timezone.utc):raise ValueError('result observation time invalid')
  if r['favorite_side'] not in ('home','away'):raise ValueError('invalid favorite side')
  diff=(a['home_goals']-a['away_goals'])*(1 if r['favorite_side']=='home' else -1);y=0 if diff>0 else 1 if diff==0 else 2
  settled.append({'match_id':mid,'actual_class':CLASSES[y],'favorite_loss':y==2,'favorite_nonwin':y!=0,'high_alert':r['risk_label']=='HIGH_LOSS_RISK_RESEARCH','version':r['version']})
 alerts=[r for r in settled if r['high_alert']];losses=sum(r['favorite_loss'] for r in settled);tp=sum(r['favorite_loss'] for r in alerts)
 return {'settled':len(settled),'pending':pending,'abstained':abstained,'favorite_losses':losses,'favorite_nonwins':sum(r['favorite_nonwin'] for r in settled),'high_alert_count':len(alerts),'high_alert_precision':tp/len(alerts) if alerts else None,'high_alert_recall':tp/losses if losses else None,'rows':settled,'formal_kpi_added':0}
