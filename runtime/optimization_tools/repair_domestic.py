"""Predeclared MLS inclusion and Saudi initial-fit repairs; no Canonical writes."""
import argparse,json,sqlite3,sys
from pathlib import Path
from collections import Counter
from datetime import datetime,timedelta
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'optimization'))
from schema import save,file_hash,now
from dataset import load
from audit import connect
from pipeline import plan,GRID

def export(db,original,out,protocol):
 out=Path(out);out.mkdir(exist_ok=False);old,ys,_,_=load(original);by={r['match_id']:r for r in old};c=connect(db);red=dict(c.execute('select old_id,new_id from team_redirects'))
 def resolve(t):
  seen=set()
  while t in red:
   if t in seen:raise ValueError('redirect cycle')
   seen.add(t);t=red[t]
  return t
 rows=[dict(r) for r in c.execute("select * from matches where competition in ('usa.1','ksa.1') order by date,match_id")]
 valid=[r for r in rows if r['training_eligible'] and r['h90'] is not None and r['a90'] is not None and r['result_status']!='conflict' and r['stage'] in ('regular','regular-season') and r['date']<now()[:10]]
 exact=Counter((r['competition'],r['season'],r['date'],resolve(r['home_id']),resolve(r['away_id'])) for r in valid);fs=[];labels=[];reject=[]
 counts=Counter((r['competition'],r['season']) for r in valid)
 for r in rows:
  code=r['competition'];reason=None;k=(code,r['season'],r['date'],resolve(r['home_id']),resolve(r['away_id']))
  if r not in valid:reason='NO_ACCEPTED_EXPLICIT_REGULAR_90_LABEL'
  elif exact[k]>1:reason='DUPLICATE_EXACT_PAIR_DAY'
  elif counts[(code,r['season'])]>(510 if code=='usa.1' else 306):reason='WHOLE_SEASON_OVERCAPACITY'
  if reason:reject.append(dict(match_id=r['match_id'],reason=reason));continue
  f=by.get(r['match_id']) or dict(match_id=r['match_id'],cohort=code,competition=code,season=r['season'],date=r['date'],home=resolve(r['home_id']),away=resolve(r['away_id']),neutral=None,stage=r['stage'],gender='male',age='senior',era='DOMESTIC_REGULAR',leg=None,score_basis='regulation_90',first_known_at=None,kickoff_at=r['kickoff_utc'],time_precision='instant' if r['kickoff_utc'] else 'date')
  fs.append(f);labels.append(dict(match_id=r['match_id'],home_goals=r['h90'],away_goals=r['a90'],first_known_at=None,source='Canonical:accepted90'))
 c.close();seasons=Counter((r['cohort'],r['season']) for r in fs)
 assert all(seasons[('usa.1',s)]==n for s,n in [('2023',493),('2024',493),('2025',510)])
 assert all(seasons[('ksa.1',s)]==306 for s in ('2023/24','2024/25'))
 for name,items in [('fixtures.jsonl',fs),('labels.jsonl',labels),('facts.jsonl',[])]:
  with (out/name).open('x') as stream:
   for item in items:stream.write(json.dumps(item,ensure_ascii=False)+'\n')
 c=sqlite3.connect(out/'research.sqlite');c.executescript('create table fixtures(match_id text primary key,cohort text,date text,payload text);create table labels(match_id text primary key,home_goals integer,away_goals integer,first_known_at text);create table facts(fact_id text primary key,match_id text,provider text,kind text,payload text);')
 c.executemany('insert into fixtures values(?,?,?,?)',[(r['match_id'],r['cohort'],r['date'],json.dumps(r)) for r in fs]);c.executemany('insert into labels values(?,?,?,?)',[(r['match_id'],r['home_goals'],r['away_goals'],None) for r in labels]);c.commit();c.close()
 save(out/'rejections.json',reject);save(out/'stage-contract.json',dict(reason='DATA_QUALITY_REPAIR',MLS='Explicit regular-season only; distinct dates remain legal even for repeated oriented opponents; only exact resolved pair/day duplicates quarantined',KSA='First development target after two complete 306-match seasons 2023/24 and2024/25; partial2022/23 retained as older fit history',core_only=True,not_new_unseen_validation=True,old_snapshot=str(original),old_manifest_sha256=file_hash(Path(original)/'manifest.json'),season_counts={f'{a}:{b}':n for (a,b),n in seasons.items()}))
 save(out/'manifest.json',dict(created_at=now(),canonical=str(db),canonical_modified=False,mode='RETROSPECTIVE_PRIOR_ONLY',active_cohorts=['usa.1','ksa.1'],cohort_counts=dict(Counter(r['cohort'] for r in fs)),xg_counts={},files={p.name:file_hash(p) for p in out.iterdir() if p.is_file()},exporter_sha256=file_hash(__file__)))
 plan(out,out/'protocol-draft.json');p=json.loads((out/'protocol-draft.json').read_text());p['candidates']=[cfg for cfg in GRID if cfg['group']=='core'];p['repair_reason']='DATA_QUALITY_REPAIR; predeclared MLS legality and KSA minimum initial seasons; original main003 retained; do not select original versus repair using score';p['repair_tool_sha256']=file_hash(__file__);p['lag_sensitivity']=[]
 for scope in p['cohorts']:
  rs=[r for r in fs if r['cohort']==scope['cohort']];start='2025-01-01' if scope['cohort']=='usa.1' else '2025-08-01';audit=(datetime.fromisoformat(max(r['date'] for r in rs))-timedelta(days=183)).date().isoformat();dates=sorted({r['date'] for r in rs if start<=r['date']<audit});assert len(dates)>=9
  edges=[dates[0],dates[len(dates)//3],dates[2*len(dates)//3],audit];scope['folds']=[dict(name='dev'+str(i+1) if i<3 else 'audit',start=a,end=edges[i+1] if i<3 else '9999-12-31',fit_ids=[r['match_id'] for r in rs if r['date']<a],target_ids=[r['match_id'] for r in rs if a<=r['date']<(edges[i+1] if i<3 else '9999-12-31')]) for i,a in enumerate(edges)];scope['initial_complete_seasons']=['2023','2024'] if scope['cohort']=='usa.1' else ['2023/24','2024/25']
  first=scope['folds'][0];assert all(r['match_id'] in first['fit_ids'] for r in rs if r['season'] in scope['initial_complete_seasons'])
 save(protocol,p);print(json.dumps(dict(counts=dict(Counter(r['cohort'] for r in fs)),added_fixture_ids=[r['match_id'] for r in fs if r['match_id'] not in by],folds=[dict(cohort=s['cohort'],folds=[dict(name=f['name'],start=f['start'],fit_n=len(f['fit_ids']),target_n=len(f['target_ids'])) for f in s['folds']]) for s in p['cohorts']]),ensure_ascii=False))
if __name__=='__main__':
 p=argparse.ArgumentParser();[p.add_argument('--'+s,required=True) for s in ('db','original','out','protocol')];a=p.parse_args();export(**vars(a))
