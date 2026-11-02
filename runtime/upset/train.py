"""Train nine separate domestic league research classifiers from audited archive odds."""
import sys,json,sqlite3,hashlib
from pathlib import Path
from datetime import datetime,timezone
from collections import Counter
sys.path.insert(0,str(Path(__file__).resolve().parent))
from classifier import train,VERSION,CONFIG,FEATURES
COHORTS=('eng.1','esp.1','ger.1','ita.1','fra.1','eng.2','ger.2','ned.1','por.1')
def extract(db):
 c=sqlite3.connect(Path(db).as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');c.execute('BEGIN')
 query="""SELECT m.match_id,m.competition cohort,m.date,m.stage,m.home_id home,m.away_id away,m.h90 home_goals,m.a90 away_goals,o.observed_at,o.raw_json,s.url source_url FROM observations o JOIN match_evidence me ON me.obs_id=o.obs_id JOIN matches m ON m.match_id=me.match_id JOIN sources s ON s.source_id=o.source_id WHERE o.provider='football-data' AND o.score_scope='regulation_90' AND m.training_eligible=1 AND o.h90=m.h90 AND o.a90=m.a90 ORDER BY o.observed_at"""
 out={};invalid=Counter()
 for row in c.execute(query):
  r=dict(row)
  if r.get('stage') not in ('regular','regular-season'):
   invalid['nonregular_stage_excluded']+=1;continue
  if r['cohort'] not in COHORTS or r['date']>=datetime.now(timezone.utc).date().isoformat():continue
  raw=json.loads(r.pop('raw_json'));odds=None
  try:
   values=[float(raw.get(k,'')) for k in ('B365H','B365D','B365A')]
   if all(1<v<=1000 for v in values):odds=values
   else:invalid['nonpositive_or_invalid_odds']+=1
  except (ValueError,TypeError):invalid['missing_odds']+=1
  r.update(score_basis='regulation_90',odds=odds,market_bookmaker='bet365',market_columns=['B365H','B365D','B365A'],market_capture_time=None)
  out[r['match_id']]=r
 c.close();return sorted(out.values(),key=lambda r:(r['cohort'],r['date'],r['match_id'])),dict(invalid)
def main():
 db='/workspace/WCPP/data/unified/WCPP_统一赛事库.sqlite';root=Path(sys.argv[1]);root.mkdir(parents=True,exist_ok=True)
 rows,missing=extract(db);(root/'history.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
 prereg={'version':VERSION,'created_before_fit':datetime.now(timezone.utc).isoformat(),'targets':['strong favorite win','draw','strong favorite loss'],'definition':'largest de-vigged home/draw/away probability is home or away and at least 0.50','config':CONFIG,'features':FEATURES,'cohorts':COHORTS,'comparison':'same held-out outcomes vs de-vigged market 1X2','separate_from_existing_500_plus_200':True,'do_not_use_test_to_select_hyperparameters_or_alert_threshold':True}
 (root/'pre-fit-protocol.json').write_text(json.dumps(prereg,ensure_ascii=False,indent=2)+'\n');reports=[];registry={'version':VERSION,'status':'RESEARCH_SIDECAR_ONLY','models':{},'main_prediction_reordered':False}
 for cohort in COHORTS:
  subset=[r for r in rows if r['cohort']==cohort]
  try:a,r=train(subset)
  except ValueError as e:reports.append({'cohort':cohort,'status':'NOT_TRAINED','reason':str(e)});continue
  d=root/cohort;d.mkdir(exist_ok=True);path=d/'artifact.json';path.write_text(json.dumps(a,ensure_ascii=False,indent=2,allow_nan=False)+'\n');(d/'report.json').write_text(json.dumps(r,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
  registry['models'][cohort]={'artifact_path':str(path),'artifact_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'report_path':str(d/'report.json'),'acceptance_gate_passed':a['acceptance_gate_passed'],'research_only':True};reports.append(r)
  print(json.dumps({'cohort':cohort,'rows':r['history_rows'],'favorites':r['strong_favorite_rows'],'test_n':r['test']['n'],'gate':r['acceptance_gate_passed'],'loss_auc':r['test']['loss_auc']},ensure_ascii=False),flush=True)
 (root/'training-summary.json').write_text(json.dumps({'data_rows':len(rows),'odds_missing_observation_counts':missing,'cohorts':reports},ensure_ascii=False,indent=2)+'\n');(root/'registry.json').write_text(json.dumps(registry,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({'models':len(registry['models']),'data_rows':len(rows),'registry':str(root/'registry.json')},ensure_ascii=False))
if __name__=='__main__':main()
