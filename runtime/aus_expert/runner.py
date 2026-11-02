"""Independent Australian men's senior research expert; active model routes untouched."""
import argparse, collections, importlib.util, json, sqlite3, sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
import numpy as np
from scipy.optimize import minimize_scalar
from scipy.stats import poisson
import lightgbm as lgb
OPT=Path('/workspace/Football-Analysis/runtime/optimization')
sys.path.insert(0,str(OPT))
from schema import file_hash,code_hash,digest,save,now,fixture,validate_fixture,aware
from models import fit_core,fit_residual,predict as model_predict,rates,matrix
from features import History
from dataset import load
ROOT=Path('/workspace/WCPP'); REPORT=ROOT/'reports/aus-expert-20261016'; DB=ROOT/'data/unified/WCPP_统一赛事库.sqlite'

def dbconnect(path=DB,readonly=False):
 c=sqlite3.connect('file:'+str(path)+'?mode=ro' if readonly else str(path),uri=readonly);c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');return c

def import_rows(input_path):
 if (REPORT/'import-audit.json').exists():raise FileExistsError('import already audited; refusing ambiguous repeat')
 rows=[json.loads(s) for s in Path(input_path).read_text().splitlines()];c=dbconnect();before={r['match_id']:dict(r) for r in c.execute('SELECT * FROM matches')};seen=set();counts=collections.Counter();details=[];sources={};raw_hashes={};identities={}
 c.execute('BEGIN IMMEDIATE')
 try:
  for x in rows:
   key='espn:'+x['source_match_id'];assert key not in seen;seen.add(key)
   assert x['competition']=='aus.1' and x['gender']=='men' and x['date']>='2005-08-01'
   raw=Path(x['raw_path'])
   if str(raw) not in raw_hashes:raw_hashes[str(raw)]=file_hash(raw)
   sha=raw_hashes[str(raw)];assert sha==x['source_body_sha256']
   if str(raw) not in sources:
    sid=digest(['aus-expert-20261016',str(raw),sha]);sources[str(raw)]=sid
    c.execute('INSERT OR IGNORE INTO sources VALUES(?,?,?,?,?,?,?)',(sid,str(raw),x['source_url'],sha,x['observed_at_utc'],now(),'espn'))
   sid=sources[str(raw)];h='espn:'+str(x['home_provider_team_id']);a='espn:'+str(x['away_provider_team_id']);stage='regular-season' if x['stage']=='regular' else ('finals' if x['stage']=='finals' else 'unknown')
   prior=list(c.execute("SELECT * FROM observations WHERE provider='espn' AND source_key=?",(key,)))
   exact=list(c.execute("SELECT * FROM matches WHERE competition='aus.1' AND date=? AND home_id=? AND away_id=?",(x['date'],h,a)))
   conflict=None
   if prior and any((r['date'],r['home_id'],r['away_id'])!=(x['date'],h,a) for r in prior):conflict='EVENT_ID_IDENTITY_DATE_CONFLICT'
   good=bool(x['training_eligible'] and x['score_scope']=='90_minutes' and type(x['home_goals_90']) is int and type(x['away_goals_90']) is int)
   if exact and good and any(r['training_eligible'] and (r['h90'],r['a90'])!=(x['home_goals_90'],x['away_goals_90']) for r in exact):conflict='EXISTING_ACCEPTED_SCORE_CONFLICT'
   if exact and any(('regular' in (r['stage'] or '')) != (stage=='regular-season') for r in exact):conflict='STAGE_CONFLICT'
   if conflict:
    c.execute('INSERT OR IGNORE INTO quarantine VALUES(?,?,?,?,?,0)',(digest([conflict,key]),conflict,sid,key,json.dumps(x,ensure_ascii=False)));counts[conflict]+=1;continue
   for tid,name in [(h,x['home_team']),(a,x['away_team'])]:
    existing=c.execute('SELECT * FROM teams WHERE team_id=?',(tid,)).fetchone()
    if existing and existing['scope']!='AUS':raise ValueError('provider id scope collision')
    c.execute('INSERT OR IGNORE INTO teams VALUES(?,?,?)',(tid,'AUS',name))
    p_id=tid.split(':')[1];old=c.execute("SELECT team_id FROM provider_team_ids WHERE provider='espn' AND provider_id=?",(p_id,)).fetchone()
    if old and old[0]!=tid:raise ValueError('provider id mapping collision')
    c.execute('INSERT OR IGNORE INTO provider_team_ids VALUES(?,?,?,?,?)',('espn',p_id,tid,sid,'ESPN explicit team ID + AUS league raw; no fuzzy merge'))
    identities[tid]={'source_name':name,'canonical_name':existing['name'] if existing else name,'evidence':'explicit ESPN provider ID; no aliases merged'}
   row=dict(source_key=key,competition='aus.1',season=x['season_label'],date=x['date'],kickoff_utc=x['kickoff_utc'],home_id=h,away_id=a,orientation='home_away',status='completed' if x['status']=='played' else x['status'],h90=x['home_goals_90'] if good else None,a90=x['away_goals_90'] if good else None,extra_h=None,extra_a=None,pen_h=x.get('penalty_score_home'),pen_a=x.get('penalty_score_away'),quality='single_source' if good else 'regulation_unresolved',score_scope='regulation_90' if good else 'unknown',provider='espn',observed_at=x['observed_at_utc'],first_known_at=None,stage=stage)
   oid=digest([sid,row]);columns=list(row);c.execute('INSERT OR IGNORE INTO observations(obs_id,source_id,'+','.join(columns)+',raw_json) VALUES('+','.join('?' for _ in range(len(columns)+3))+')',[oid,sid]+list(row.values())+[json.dumps(x,ensure_ascii=False)])
   if exact:
    mid=exact[0]['match_id'];counts['existing_fixture_preserved']+=1
   else:
    mid=digest(('aus.1',x['date'],h,a,'home_away'))[:32]
    c.execute('INSERT INTO matches VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(mid,'aus.1',x['season_label'],x['date'],x['kickoff_utc'],h,a,'home_away',row['status'],row['h90'],row['a90'],'accepted' if good else 'unresolved',1,int(good),stage));counts['new_matches']+=1;counts['new_accepted90']+=int(good)
   c.execute('INSERT OR IGNORE INTO match_evidence VALUES(?,?)',(mid,oid));details.append({'event':key,'match_id':mid,'new':mid not in before,'eligible90':good,'stage':stage})
  assert not list(c.execute('PRAGMA foreign_key_check'))
  after={r['match_id']:dict(r) for r in c.execute('SELECT * FROM matches')};assert all(after[k]==v for k,v in before.items()),'old projection changed'
  c.commit()
 except BaseException:c.rollback();raise
 result={'counts':dict(counts),'old_matches_unchanged':len(before),'foreign_key_check':[],'integrity':c.execute('PRAGMA integrity_check').fetchone()[0],'input':str(input_path),'input_sha256':file_hash(input_path),'canonical_sha256_after':file_hash(DB),'details':details,'identities':identities}
 save(REPORT/'import-audit.json',result);c.close();return {k:v for k,v in result.items() if k not in ('details','identities')}

def export_dataset(out):
 out=Path(out);out.mkdir(exist_ok=False);c=dbconnect(readonly=True);fs=[];ys=[];rejections=[]
 for r in c.execute("SELECT m.*,h.name home_name,a.name away_name FROM matches m JOIN teams h ON h.team_id=m.home_id JOIN teams a ON a.team_id=m.away_id WHERE competition='aus.1' ORDER BY date,match_id"):
  r=dict(r)
  if not r['training_eligible'] or r['stage'] not in ('regular','regular-season') or r['date']>='2026-07-01':rejections.append(r);continue
  f={'match_id':r['match_id'],'cohort':'aus.1','competition':'aus.1','date':r['date'],'season':r['season'],'home':r['home_id'],'away':r['away_id'],'home_name':r['home_name'],'away_name':r['away_name'],'stage':'regular','gender':'male','age':'senior','neutral':None,'kickoff_at':r['kickoff_utc'],'score_basis':'regulation_90','first_known_at':None};fs.append(f);ys.append({'match_id':r['match_id'],'home_goals':r['h90'],'away_goals':r['a90'],'first_known_at':None,'source':'Canonical accepted90'})
 for name,rs in [('fixtures.jsonl',fs),('labels.jsonl',ys),('facts.jsonl',[])]:
  with (out/name).open('x') as f:
   for r in rs:f.write(json.dumps(r,ensure_ascii=False)+'\n')
 rc=sqlite3.connect(out/'research.sqlite');rc.executescript('CREATE TABLE fixtures(match_id TEXT PRIMARY KEY,cohort TEXT,date TEXT,season TEXT,home TEXT,away TEXT,payload TEXT);CREATE TABLE labels(match_id TEXT PRIMARY KEY,home_goals INT,away_goals INT,first_known_at TEXT,source TEXT);CREATE TABLE facts(fact_id TEXT PRIMARY KEY,match_id TEXT,provider TEXT,kind TEXT,date TEXT,first_known_at TEXT,payload TEXT);')
 for f,y in zip(fs,ys):rc.execute('INSERT INTO fixtures VALUES(?,?,?,?,?,?,?)',(f['match_id'],'aus.1',f['date'],f['season'],f['home'],f['away'],json.dumps(f)));rc.execute('INSERT INTO labels VALUES(?,?,?,?,?)',(y['match_id'],y['home_goals'],y['away_goals'],None,y['source']))
 rc.commit();rc.close();save(out/'rejections.json',rejections);save(out/'manifest.json',{'created_at':now(),'canonical_sha256':file_hash(DB),'canonical_modified_by_export':False,'mode':'RETROSPECTIVE_PRIOR_ONLY','first_known_at_policy':'missing; no contemporaneous as-of certification','cohort_counts':{'aus.1':len(fs)},'xg_status':'MISSING_NOT_IMPUTED','process_status':'ESPN_ARCHIVED_NOT_USED; provider mismatch with shared History channels','neutral_status':'UNKNOWN_PRESERVED','files':{p.name:file_hash(p) for p in out.iterdir()}});return len(fs)

def dc_matrix(mu,rho=0.):
 m=matrix(mu);l,v=mu
 tau=[1-l*v*rho,1+l*rho,1+v*rho,1-rho]
 if min(tau)<=0:raise ValueError('nonpositive DC tau')
 m[0,0]*=tau[0];m[0,1]*=tau[1];m[1,0]*=tau[2];m[1,1]*=tau[3];m/=m.sum();return m

def rho_fit(core,rows):
 mus=rates(core,rows)
 def loss(r):
  try:return -sum(np.log(max(dc_matrix(mu,r)[x['home_goals'],x['away_goals']],1e-300)) for mu,x in zip(mus,rows))
  except ValueError:return 1e100
 result=minimize_scalar(loss,bounds=(-.03,.015),method='bounded',options={'xatol':1e-8});assert result.success;return float(result.x)

def fit(cfg,rows,hist):
 if cfg=='constant':
  core={'fit_n':len(rows),'fit_end':rows[-1]['date'],'constant':[np.mean([r['home_goals'] for r in rows]),np.mean([r['away_goals'] for r in rows])]};return core,None,0.,{}
 core=fit_core(rows,365,20);b=None;meta={}
 if cfg=='ml':b,meta=fit_residual(rows,hist,{'half_life':365,'ridge':20},'core')
 return core,b,rho_fit(core,rows) if cfg=='dc' else 0.,meta

def matrices(cfg,core,b,rho,targets,hist,origin):
 mus=np.repeat([core['constant']],len(targets),axis=0) if cfg=='constant' else model_predict(core,targets,hist,b,'core',.35 if cfg=='ml' else 0.,origin)
 return [dc_matrix(mu,rho) for mu in mus]

def metrics(records):
 n=len(records);return {'n':n,'top1_hits':sum(x['hit'] for x in records),'top1':np.mean([x['hit'] for x in records]),'score_logloss':np.mean([x['logloss'] for x in records]),'total_bias':np.mean([sum(x['expected_goals'])-sum(x['actual']) for x in records]),'home_bias':np.mean([x['expected_goals'][0]-x['actual'][0] for x in records]),'away_bias':np.mean([x['expected_goals'][1]-x['actual'][1] for x in records]),'crps':np.mean([x['crps'] for x in records]),'mean_team_mae':np.mean([np.mean(np.abs(np.asarray(x['expected_goals'])-x['actual'])) for x in records]),'interval80_coverage':np.mean([np.mean([g['central80_interval'][0]<=v<=g['central80_interval'][1] for g,v in zip(x['team_goal_summary'],x['actual'])]) for x in records]),'wdl_accuracy':np.mean([x['wdl_hit'] for x in records])}

def evaluate(cfg,core,b,rho,targets,hist,origin,out,model_hash):
 ms=matrices(cfg,core,b,rho,targets,hist,origin);save(str(out)+'.predictions.json',{'configuration':cfg,'fit_end':core['fit_end'],'origin':origin,'model_hash':model_hash,'predictions':[{'match_id':r['match_id'],'date':r['date'],'matrix':m.tolist()} for r,m in zip(targets,ms)]});records=[]
 for r,m in zip(targets,ms):
  y=[r['home_goals'],r['away_goals']];pick=list(map(int,np.unravel_index(m.argmax(),m.shape)));marg=[m.sum(1),m.sum(0)];eg=[float(np.arange(len(p))@p) for p in marg];total_p=np.bincount(np.indices(m.shape).sum(axis=0).ravel(),weights=m.ravel(),minlength=2*len(m)-1);crps=np.sum((np.cumsum(total_p)-(np.arange(len(total_p))>=sum(y)))**2);wdl=[float(np.tril(m,-1).sum()),float(np.trace(m)),float(np.triu(m,1).sum())]
  records.append({'match_id':r['match_id'],'date':r['date'],'season':r['season'],'home':r['home'],'away':r['away'],'actual':y,'statistical_top1':pick,'hit':pick==y,'logloss':float(-np.log(max(m[tuple(y)] if max(y)<len(m) else 0.,1e-300))),'expected_goals':eg,'crps':float(crps),'wdl_probabilities':wdl,'wdl_hit':int(np.argmax(wdl))==(0 if y[0]>y[1] else 1 if y[0]==y[1] else 2),'matrix':m.tolist(),'origin':origin,'fit_end':core['fit_end'],'model_hash':model_hash,'feature_provenance':hist.vector(fixture(r),origin)[1],'team_goal_summary':goal_summary(m)})
 save(out,{'configuration':cfg,'fit_end':core['fit_end'],'rho':rho,'metrics':metrics(records),'by_season':{s:metrics([r for r in records if r['season']==s]) for s in sorted({x['season'] for x in records})},'records':records});return metrics(records),records

def train(dataset,out):
 out=Path(out);out.mkdir(exist_ok=False);fs,ys,facts,man=load(dataset);rows=[{**f,**ys[f['match_id']]} for f in fs];train=[r for r in rows if r['date']<'2021-07-01'];dev=[r for r in rows if '2021-07-01'<=r['date']<'2023-07-01'];test=[r for r in rows if '2023-07-01'<=r['date']<'2026-07-01'];h=History([fixture(r) for r in train],{r['match_id']:ys[r['match_id']] for r in train},[],7)
 protocol={'created_at':now(),'dataset':str(dataset),'dataset_manifest_hash':file_hash(Path(dataset)/'manifest.json'),'train_before':'2021-07-01','development_before':'2023-07-01','audit_before':'2026-07-01','audit_fit_before':'2023-07-01','audit_fit_policy':'selected fixed config refit train+development only; audit labels excluded','candidates':['constant','poisson','dc','ml'],'reference':'poisson','primary_metric':'statistical exact-score Top1; not comprehensive Primary','guardrails':'score_logloss <= reference+.02; abs(home/away bias)<=abs(reference)+.05; abs(total_bias)<=abs(reference)+.05; CRPS<=reference+.02','tie_break':'logloss then fixed order','feature_policy':'train-only historical labels; fixed train origin; no evaluation labels consumed; no xG/process','dc_rho_bounds':[-.03,.015],'ml':'OOF group core, alpha=.35, shared deterministic fixed hyperparameters','sizes':{'train':len(train),'development':len(dev),'audit':len(test)},'matrix_policy':'Poisson rates clipped [.08,8]; grid size=max(16,poisson.isf(1e-13,max_rate)+1), outer product normalized then DC tau normalized; argmax row-major first tie; total CRPS from exact matrix anti-diagonal sums; goal intervals discrete central80 with actual mass','holdout_claim':'operation holdout only; historical existing data may have been used in earlier research; not prospective untouched performance','promotion':'never automatic; requires future frozen Primary evidence'};save(out/'protocol.json',protocol)
 fits={};scores={};fit_hashes={}
 for cfg in protocol['candidates']:
  fits[cfg]=fit(cfg,train,h);core,b,rho,meta=fits[cfg]
  bp=out/(cfg+'-evaluation-booster.txt') if b else None
  if b:b.save_model(str(bp))
  fp=out/(cfg+'-evaluation-fit.json');save(fp,{'configuration':cfg,'core':core,'rho':rho,'residual_metadata':meta,'booster_file':bp.name if bp else None,'booster_sha256':file_hash(bp) if bp else None,'optimization_code_hash':code_hash(),'runner_sha256':file_hash(__file__),'dataset_manifest_hash':file_hash(Path(dataset)/'manifest.json')});fit_hashes[cfg]=file_hash(fp)
  scores[cfg],_=evaluate(cfg,core,b,rho,dev,h,'2021-07-01',out/(cfg+'-development.json'),fit_hashes[cfg]);print(cfg,scores[cfg],flush=True)
 ref=scores['poisson'];passed=[k for k,v in scores.items() if v['score_logloss']<=ref['score_logloss']+.02 and v['crps']<=ref['crps']+.02 and all(abs(v[x])<=abs(ref[x])+.05 for x in ('total_bias','home_bias','away_bias'))];selected=min(passed,key=lambda k:(-scores[k]['top1'],scores[k]['score_logloss'],protocol['candidates'].index(k))) if passed else 'poisson';save(out/'selection.json',{'selected':selected,'scores':scores,'passed_guardrails':passed,'selected_before_audit_scoring':True})
 # Independent, one-time operational holdout; no subsequent selection uses these results.
 audit={};audit_train=[r for r in rows if r['date']<'2023-07-01'];ah=History([fixture(r) for r in audit_train],{r['match_id']:ys[r['match_id']] for r in audit_train},[],7)
 for cfg in sorted(set([selected,'poisson','constant'])):
  core,b,rho,meta=fit(cfg,audit_train,ah);bp=out/(cfg+'-audit-booster.txt') if b else None
  if b:b.save_model(str(bp))
  fp=out/(cfg+'-audit-fit.json');save(fp,{'configuration':cfg,'core':core,'rho':rho,'residual_metadata':meta,'booster_file':bp.name if bp else None,'booster_sha256':file_hash(bp) if bp else None,'optimization_code_hash':code_hash(),'runner_sha256':file_hash(__file__),'dataset_manifest_hash':file_hash(Path(dataset)/'manifest.json')})
  audit[cfg],_=evaluate(cfg,core,b,rho,test,ah,'2023-07-01',out/(cfg+'-audit.json'),file_hash(fp))
 save(out/'audit-summary.json',{'selected':selected,'metrics':audit,'fixed_origin_limitation':'audit features and strengths fitted before 2023-07; multi-year stale-state pressure test, not realistic rolling forecasts','decision':'RESEARCH_ONLY_NOT_PROMOTED','targets70_80':'NOT_PROVEN'})
 # Full historical deployment fit is separate and never scored as holdout performance.
 hist=History(fs,ys,[],7);core,b,rho,meta=fit(selected,rows,hist);boost=out/'residual.txt' if b else None
 if b:b.save_model(str(boost))
 art={'version':'WCPP-AUS-EXPERT-0.1','status':'RESEARCH_ONLY_NOT_PROMOTED','created_at':now(),'active':False,'configuration':selected,'core':core,'rho':rho,'residual_metadata':meta,'booster_file':boost.name if boost else None,'booster_sha256':file_hash(boost) if boost else None,'dataset':str(dataset),'dataset_manifest_hash':file_hash(Path(dataset)/'manifest.json'),'optimization_code_hash':code_hash(),'runner_sha256':file_hash(__file__),'selection_hash':file_hash(out/'selection.json'),'audit_summary_hash':file_hash(out/'audit-summary.json'),'forecast_scope':{'competition':'aus.1','gender':'male','age':'senior','stage':'regular'},'trained_xg':False,'process_trained':False,'injuries_trained':False,'first_known_at_policy':'RETROSPECTIVE_PRIOR_ONLY','final_fit_claim':'all history refit for future research only; scores belong to earlier frozen fit'};save(out/'artifact.json',art)
 target=fixture(rows[-1]);target.update(match_id='SYNTHETIC-SERIALIZE-ONLY',gender='male',age='senior',date=(datetime.fromisoformat(core['fit_end'])+timedelta(days=10)).date().isoformat());expected=matrices(selected,core,b,rho,[target],hist,target['date'])[0];loaded=calculate(out/'artifact.json',target,clock_check=False);assert np.array_equal(expected,np.asarray(loaded['matrix']));save(out/'serialize-check.json',{'synthetic':True,'matrix_exactly_equal':True,'matrix_hash':digest(loaded['matrix']),'artifact_sha256':file_hash(out/'artifact.json')})
 save(REPORT/'aus-expert-registry.json',{'version':art['version'],'status':art['status'],'artifact':str(out/'artifact.json'),'artifact_sha256':file_hash(out/'artifact.json'),'entrypoint':str(Path(__file__)),'active_registry_modified':False,'scope':art['forecast_scope'],'unsupported':['finals','women','youth','non-aus.1'],'missing_evidence_action':'ABSTAIN','comprehensive_primary':'ABSTAIN_REQUIRES_MATCH_EVIDENCE; statistical candidate only'});return {'selected':selected,'development':scores[selected],'audit':audit,'artifact':str(out/'artifact.json')}

def goal_summary(m):
 result=[]
 for p in (m.sum(1),m.sum(0)):
  c=np.cumsum(p);lo=int(np.searchsorted(c,.1));hi=int(np.searchsorted(c,.9));result.append({'mean':float(np.arange(len(p))@p),'central80_interval':[lo,hi],'interval_probability_mass':float(p[lo:hi+1].sum()),'p_zero':float(p[0]),'p_ge2':float(p[2:].sum()),'p_ge3':float(p[3:].sum())})
 return result

def calculate(artifact,target,clock_check=True):
 a=json.loads(Path(artifact).read_text());assert file_hash(__file__)==a['runner_sha256'];assert code_hash()==a['optimization_code_hash'];assert file_hash(Path(a['dataset'])/'manifest.json')==a['dataset_manifest_hash']
 if target.get('competition')!='aus.1' or target.get('cohort')!='aus.1' or target.get('gender') not in ('male','men') or target.get('age')!='senior' or target.get('stage') not in ('regular','regular-season'):return {'status':'ABSTAIN','reason':'UNSUPPORTED_COMPETITION_GENDER_AGE_PHASE','active':False,'model':a['version'],'artifact_hash':file_hash(artifact),'cutoff_at':target.get('cutoff_at')}
 validate_fixture(target);assert target['date']>a['core']['fit_end'],'target within fitted history'
 known={t for f in load(a['dataset'])[0] for t in (f['home'],f['away'])}
 if any(target[t] not in known for t in ('home','away')):return {'status':'ABSTAIN','reason':'COLD_START_TEAM_NOT_TRAINED','teams':[target[t] for t in ('home','away') if target[t] not in known],'model':a['version'],'artifact_hash':file_hash(artifact),'cutoff_at':target.get('cutoff_at'),'active':False}
 if clock_check:
  assert aware(target['cutoff_at'])<=aware(now())<aware(target['kickoff_at']),'requires actual pre-kickoff cutoff';assert aware(a['created_at'])<=aware(target['cutoff_at']);assert target['date']==aware(target['kickoff_at']).date().isoformat()
 fs,ys,facts,man=load(a['dataset']);hist=History(fs,ys,[],7);b=None
 if a['booster_file']:
  path=Path(artifact).parent/a['booster_file'];assert file_hash(path)==a['booster_sha256'];b=lgb.Booster(model_file=str(path))
 m=matrices(a['configuration'],a['core'],b,a['rho'],[fixture(target)],hist,target['date'])[0];pick=list(map(int,np.unravel_index(m.argmax(),m.shape)));_,proof=hist.vector(fixture(target));return {'status':'PREDICTED_RESEARCH_ONLY','model':a['version'],'active':False,'artifact_hash':file_hash(artifact),'statistical_top1':pick,'primary_probability':float(m[tuple(pick)]),'comprehensive_primary':'ABSTAIN_REQUIRES_MULTISOURCE_MATCH_EVIDENCE','matrix':m.tolist(),'matrix_hash':digest(m.tolist()),'team_goal_summary':goal_summary(m),'cold_start_teams':[t for t in (target['home'],target['away']) if t not in known],'expected_goals':[float(np.arange(len(m))@m.sum(axis=k)) for k in (1,0)],'provenance':proof,'input_hash':digest(target),'xg_status':'MISSING_NOT_TRAINED','injuries_status':'NOT_TRAINED','cutoff_at':target.get('cutoff_at')}

def main():
 p=argparse.ArgumentParser();s=p.add_subparsers(dest='command',required=True)
 z=s.add_parser('import');z.add_argument('--input',required=True);z=s.add_parser('export');z.add_argument('--out',required=True);z=s.add_parser('train');z.add_argument('--dataset',required=True);z.add_argument('--out',required=True);z=s.add_parser('predict');z.add_argument('--artifact',required=True);z.add_argument('--fixture',required=True);z.add_argument('--out',required=True);z.add_argument('--freeze-root');args=p.parse_args()
 if args.command=='import':r=import_rows(args.input)
 elif args.command=='export':r=export_dataset(args.out)
 elif args.command=='train':r=train(args.dataset,args.out)
 else:
  if Path(args.out).exists():raise FileExistsError('prediction output exists')
  target=json.loads(Path(args.fixture).read_text());r=calculate(args.artifact,target);spec=importlib.util.spec_from_file_location('_aus_freeze',OPT.parent/'research/analysis_freeze.py');mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);kw={'stage':'prematch','track':'aus_expert_research'}
  if args.freeze_root:kw['root']=args.freeze_root
  r['freeze']=mod.save(r,target,**kw);save(args.out,r)
 print(json.dumps(r,ensure_ascii=False,allow_nan=False))
if __name__=='__main__':main()
