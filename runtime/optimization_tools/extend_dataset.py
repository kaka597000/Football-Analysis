"""Additional domestic league snapshot with explicit source-stage contracts.

Does not change the frozen first-run exporter or any Canonical data.
"""
import argparse,json,sqlite3,sys,re
from pathlib import Path
from collections import Counter,defaultdict
from datetime import datetime,timedelta
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'optimization'))
from schema import save,now,file_hash,fixture
from audit import connect
from pipeline import plan,GRID
SCOPE=['jpn.1','jpn.2','kor.1','kor.2','ger.2','ned.2']

def export(db,out,protocol):
 out=Path(out);out.mkdir(parents=True,exist_ok=False);c=connect(db);red=dict(c.execute('select old_id,new_id from team_redirects'))
 def team(t):
  seen=set()
  while t in red:
   if t in seen:raise ValueError('team redirect cycle')
   seen.add(t);t=red[t]
  return t
 rows=[dict(r) for r in c.execute('select * from matches order by date,match_id') if r['competition'] in SCOPE];raw_evidence=[]
 # Official K League stored raw payloads prove distinct gameId / roundId;
 # no network call or inference from final league tables is required here.
 for r in c.execute("select o.obs_id,o.source_key,o.competition,o.season,o.raw_json from observations o where o.provider='kleague' and o.competition in ('kor.1','kor.2')"):
  x=json.loads(r['raw_json']);raw_evidence.append({'obs_id':r['obs_id'],'source_key':r['source_key'],'competition':r['competition'],'season':r['season'],'leagueId':x.get('leagueId'),'roundId':x.get('roundId'),'gameId':x.get('gameId'),'homeTeam':x.get('homeTeam'),'awayTeam':x.get('awayTeam'),'isSemiPlayOff':x.get('isSemiPlayOff'),'raw_sha256':__import__('hashlib').sha256(r['raw_json'].encode()).hexdigest()})
 def stage_ok(r):
  s=r.get('stage');code=r['competition']
  return s in ('regular','regular-season') or (code=='jpn.1' and re.fullmatch(r'202[345]-japanese-j1-league',s or '') is not None)
 def expected(r):
  code=r['competition'];season=r['season']
  if code=='ger.2':return 306
  if code=='ned.2':return 380
  if code=='jpn.1':return 306 if season=='2023' else 380
  if code=='jpn.2':return 462 if season=='2023' else 380
  if code=='kor.1':return 198
  if code=='kor.2':return 273 if season=='2025' else 234
 counts=Counter((r['competition'],r['season']) for r in rows if r['training_eligible'] and stage_ok(r));identical=Counter((r['competition'],r['season'],r['date'],team(r['home_id']),team(r['away_id'])) for r in rows if r['training_eligible'] and stage_ok(r));fs=[];labels=[];reject=[]
 for r in rows:
  code=r['competition'];reason=None
  if not r['training_eligible'] or r['h90'] is None or r['a90'] is None or r['result_status']=='conflict':reason='NO_ACCEPTED_90_MINUTE_LABEL'
  elif r['date']>=now()[:10]:reason='CURRENT_OR_FUTURE_DAY'
  elif not stage_ok(r):reason='PHASE_NOT_SOURCE_REGULAR'
  elif code=='kor.2' and r['season'] not in ('2023','2024','2025'):reason='K2_POST2025_FORMAT_SEPARATED_NOT_ASSUMED_COMPATIBLE'
  elif counts[(code,r['season'])]>expected(r):reason='WHOLE_SEASON_OVERCAPACITY'
  elif identical[(code,r['season'],r['date'],team(r['home_id']),team(r['away_id']))]>1:reason='DUPLICATE_EXACT_PAIR_DAY'
  if reason:reject.append({'match_id':r['match_id'],'competition':code,'season':r['season'],'reason':reason});continue
  fs.append({'match_id':r['match_id'],'cohort':code,'competition':code,'season':r['season'],'date':r['date'],'home':team(r['home_id']),'away':team(r['away_id']),'neutral':None,'stage':'regular','source_stage':r['stage'],'era':'DOMESTIC_SOURCE_REGULAR_PRE2026' if code=='kor.2' else 'DOMESTIC_SOURCE_REGULAR','gender':'male','age':'senior','score_basis':'regulation_90','first_known_at':None,'kickoff_at':r['kickoff_utc'],'time_precision':'instant' if r['kickoff_utc'] else 'date'})
  labels.append({'match_id':r['match_id'],'home_goals':r['h90'],'away_goals':r['a90'],'first_known_at':None,'source':'Canonical:accepted90'})
 c.close();research=sqlite3.connect(out/'research.sqlite');research.executescript('create table fixtures(match_id text primary key,cohort text,date text,payload text); create table labels(match_id text primary key,home_goals integer,away_goals integer,first_known_at text); create table facts(fact_id text primary key,match_id text,provider text,kind text,payload text); create table rejections(match_id text,reason text,payload text); create table source_stage_evidence(obs_id text,source_key text,payload text);')
 for r in fs:research.execute('insert into fixtures values(?,?,?,?)',(r['match_id'],r['cohort'],r['date'],json.dumps(r)))
 for r in labels:research.execute('insert into labels values(?,?,?,?)',(r['match_id'],r['home_goals'],r['away_goals'],None))
 for r in reject:research.execute('insert into rejections values(?,?,?)',(r['match_id'],r['reason'],json.dumps(r)))
 for r in raw_evidence:research.execute('insert into source_stage_evidence values(?,?,?)',(r['obs_id'],r['source_key'],json.dumps(r)))
 research.commit();research.close()
 for name,rs in [('fixtures.jsonl',fs),('labels.jsonl',labels),('facts.jsonl',[])]:
  with (out/name).open('x') as f:
   for r in rs:f.write(json.dumps(r,ensure_ascii=False)+'\n')
 save(out/'rejections.json',reject);router=Path('/workspace/Football-Analysis/runtime/v4/competition_router.py')
 save(out/'stage-contract.json',{'basis':'Canonical stage and existing competition router regular slug mapping; official K League archived raw leagueId/roundId/gameId/isSemiPlayOff fields copied as evidence','router':str(router),'router_sha256':file_hash(router),'source_fields':['matches.stage','matches.competition','matches.season','observations.provider','observations.raw_json'],'K1':'Regular only; split_A/split_B/promotion_playoff excluded. Repeated directed opponents on distinct dates are legal official distinct games, not auto-deleted.','K2':'Only2023-2025 regular;2026 isolated pending separate format and training depth. No unverified double-round assertion.','JPN':'2023-2025 explicit year-japanese-j1-league normalized like existing router; centennial competitions not in scope.2026/27 ordinary source regular kept.','pair_rule':'Same resolved teams on same date quarantined. K official distinct roundId/gameId allows repeated oriented opponents on different days.','venue':'Unknown preserved, never False','process_xg':'No approved process/xG facts joined in additional batch; core only'})
 save(out/'manifest.json',{'created_at':now(),'mode':'RETROSPECTIVE_PRIOR_ONLY','canonical':str(db),'canonical_modified':False,'active_cohorts':SCOPE,'cohort_counts':dict(Counter(r['cohort'] for r in fs)),'xg_counts':{},'files':{p.name:file_hash(p) for p in out.iterdir() if p.is_file()},'exporter':str(Path(__file__).resolve()),'exporter_sha256':file_hash(__file__)})
 # plan() gives the shared frozen schema; customized boundaries are fixed here
 # before any training, preserving two complete initial seasons and ~six-month audit.
 draft=out/'protocol-draft.json';plan(out,draft);p=json.loads(draft.read_text());p['candidates']=[x for x in GRID if x['group']=='core'];p['extension_scope']='Nonactive six-league pure score core research; no empty xG/process ablation';p['legacy']='Original replay controls will abstain where no genuine venue evidence exists';p['resources']='One training process/one CPU; main batch concurrently one CPU; total limit4';p['maturity']='minimum2 complete source regular seasons initial fit; three chronological development blocks; last183days fixed-origin audit'
 starts={'jpn.1':'2025-01-01','jpn.2':'2025-01-01','kor.1':'2025-01-01','kor.2':'2025-01-01','ger.2':'2022-07-01','ned.2':'2025-07-01'}
 for scope in p['cohorts']:
  if scope['status']!='PLANNED':continue
  rs=[r for r in fs if r['cohort']==scope['cohort']];last=max(r['date'] for r in rs);audit=(datetime.fromisoformat(last)-timedelta(days=183)).date().isoformat();dates=sorted({r['date'] for r in rs if starts[scope['cohort']]<=r['date']<audit})
  if len(dates)<9:scope.update(status='INSUFFICIENT_TWO_FULL_SEASONS_PLUS_DEVELOPMENT_AND_AUDIT');continue
  edges=[dates[0],dates[len(dates)//3],dates[2*len(dates)//3],audit];folds=[]
  for i,start in enumerate(edges):
   end=edges[i+1] if i<3 else '9999-12-31';folds.append({'name':'dev'+str(i+1) if i<3 else 'audit','start':start,'end':end,'fit_ids':[r['match_id'] for r in rs if r['date']<start],'target_ids':[r['match_id'] for r in rs if start<=r['date']<end]})
  scope['folds']=folds;scope['warmup_evidence']='Two complete explicit-source regular seasons precede first target; see season counts'
 p['extension_exporter_hash']=file_hash(__file__);p['extension_stage_contract_hash']=file_hash(out/'stage-contract.json');save(protocol,p)
 return {'counts':p['cohorts'] and dict(Counter(r['cohort'] for r in fs)),'rejected':len(reject),'protocol':str(protocol)}
if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('--db',required=True);a.add_argument('--out',required=True);a.add_argument('--protocol',required=True);args=a.parse_args();print(json.dumps(export(args.db,args.out,args.protocol),ensure_ascii=False))
