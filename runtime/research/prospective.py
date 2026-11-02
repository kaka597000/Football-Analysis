"""Append-only independent research ledger; never claims a formal Primary freeze."""
import json,hashlib,sys
from pathlib import Path
from datetime import datetime,timezone
from urllib.parse import urlparse
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'upset'))
import classifier as old

def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def save(bundle,root):
 current=bundle['current_model'];test=bundle['test_model'];fixture=current['input'];mid=bundle['match_id']
 if not current.get('version') or not test.get('version'):raise ValueError('explicit model versions required')
 if fixture['match_id']!=mid or test['input']!=fixture:raise ValueError('identical paired fixture required')
 cutoff=old.aware(fixture['cutoff_at']);kickoff=old.aware(fixture['kickoff_at']);now=datetime.now(timezone.utc)
 if not cutoff<=now<kickoff:raise ValueError('must record before kickoff')
 for row in (current,test):
  if not cutoff<=old.aware(row['generated_at'])<=now:raise ValueError('prediction generation time invalid')
  if row.get('input_hash') is not None and digest(row['input'])!=row['input_hash']:raise ValueError('fixture hash required')
  if digest(row['matrix'])!=row['matrix_hash']:raise ValueError('actual probability matrix required')
  parts=row['primary'].split('-')
  if len(parts)!=2 or not all(p.isdigit() for p in parts):raise ValueError('exact-score recommendation required')
 candidate=bundle.get('upset_candidate',{})
 if candidate.get('status')=='CANDIDATE_SHADOW_ONLY' and not cutoff<=old.aware(candidate['generated_at'])<kickoff:raise ValueError('candidate must precede kickoff')
 row={'scope':'INDEPENDENT_PROSPECTIVE_RESEARCH_NOT_FORMAL_PRIMARY_FREEZE','recorded_at':now.isoformat(),'match_id':mid,'bundle':bundle,'bundle_sha256':digest(bundle),'input_sha256':digest(fixture),'formal_kpi_eligible':False}
 out=Path(root);out.mkdir(parents=True,exist_ok=True);name=hashlib.sha256(mid.encode()).hexdigest()+'.json'
 with (out/name).open('x') as f:json.dump(row,f,ensure_ascii=False,indent=2,allow_nan=False)
 return str(out/name)

def review(root,results):
 observed={r['match_id']:r for r in results}
 if len(observed)!=len(results):raise ValueError('duplicate results')
 pairs=[];pending=[];abstentions=[];classifier_records=[];classifier_results=[];versions=set()
 for path in sorted(Path(root).glob('*.json')):
  r=json.loads(path.read_text());b=r['bundle'];mid=r['match_id'];fixture=b['current_model']['input']
  versions.add((b['current_model']['version'],b['test_model']['version']))
  if len(versions)>1:raise ValueError('different model versions require separate paired ledgers')
  if r['bundle_sha256']!=digest(b):raise ValueError('ledger changed')
  if mid not in observed:pending.append(mid);continue
  a=observed[mid]
  if a.get('score_basis')!='regulation_90' or any(type(a.get(k)) is not int or not 0<=a[k]<=30 for k in ('home_goals','away_goals')):raise ValueError('90-minute result required')
  if urlparse(a.get('source_url','')).scheme!='https' or old.aware(a['observed_at'])>datetime.now(timezone.utc):raise ValueError('verified result source/time required')
  from datetime import timedelta
  if old.aware(a['observed_at'])<old.aware(fixture['kickoff_at'])+timedelta(minutes=90):raise ValueError('result observed too early')
  score=f"{a['home_goals']}-{a['away_goals']}";pairs.append({'match_id':mid,'current_top1_hit':b['current_model']['primary']==score,'test_top1_hit':b['test_model']['primary']==score})
  c=b.get('upset_candidate',{})
  if c.get('status')!='CANDIDATE_SHADOW_ONLY':abstentions.append(mid);continue
  cr=dict(c,status='RESEARCH_ONLY');classifier_records.append(cr);classifier_results.append(a)
 n=len(pairs);s={'scope':'INDEPENDENT_PROSPECTIVE_RESEARCH_NOT_FORMAL_PRIMARY_FREEZE','paired_settled':n,'pending':pending,'current_top1_hits':sum(r['current_top1_hit'] for r in pairs),'test_top1_hits':sum(r['test_top1_hit'] for r in pairs),'classifier_abstained':abstentions,'formal_kpi_added':0,'pairs':pairs}
 s['current_top1_rate']=s['current_top1_hits']/n if n else None;s['test_top1_rate']=s['test_top1_hits']/n if n else None;s['classifier']=old.review(classifier_records,classifier_results)
 from collections import defaultdict
 import numpy as np
 groups=defaultdict(list)
 for c in classifier_records:
  a=observed[c['match_id']];side=c['favorite_side'];diff=(a['home_goals']-a['away_goals'])*(1 if side=='home' else -1);y=0 if diff>0 else 1 if diff==0 else 2
  quote=c['market_quote'];fav=old.favorite([quote[k] for k in ('home_odds','draw_odds','away_odds')])
  if not fav or fav[0]!=side:raise ValueError('frozen favorite definition mismatch')
  baseline=fav[1][[0,1,2] if side=='home' else [2,1,0]];prob=np.array([c['probabilities'][k] for k in old.CLASSES],float)
  if not np.isfinite(prob).all() or (prob<0).any() or abs(prob.sum()-1)>1e-6:raise ValueError('valid frozen class probabilities required')
  key=(c['cohort'],c['version'],c['artifact_sha256']);groups[key].append((y,prob,baseline))
 s['classifier_by_cohort_and_artifact']=[{'cohort':k[0],'version':k[1],'artifact_sha256':k[2],'candidate':old.metrics([r[0] for r in rows],np.stack([r[1] for r in rows])),'market':old.metrics([r[0] for r in rows],np.stack([r[2] for r in rows])),'automatic_promotion':False} for k,rows in groups.items()]
 return s
if __name__=='__main__':
 import argparse
 p=argparse.ArgumentParser();sp=p.add_subparsers(dest='cmd',required=True);f=sp.add_parser('record');f.add_argument('bundle');f.add_argument('--ledger',required=True);r=sp.add_parser('review');r.add_argument('--ledger',required=True);r.add_argument('--results',required=True);r.add_argument('--out',required=True);a=p.parse_args()
 if a.cmd=='record':print(save(json.loads(Path(a.bundle).read_text()),a.ledger))
 else:
  v=review(a.ledger,json.loads(Path(a.results).read_text()))
  with Path(a.out).open('x') as out:json.dump(v,out,ensure_ascii=False,indent=2)
