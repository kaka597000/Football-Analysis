"""Train new continental routes using existing dual-model algorithms, without promotion."""
import json,hashlib,sys
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'v5'));import weighted_model as weighted
sys.path.insert(0,str(ROOT/'continental'));import ucl_ml as ml

def validate(rows):
 rows=weighted.legacy.history(rows)
 if not rows or any(r.get('competition')!='uefa.champions' for r in rows):raise ValueError('explicit UEFA Champions League scope required')
 phases={r.get('ucl_era') or r.get('era') for r in rows};stage={r['stage'] for r in rows}
 if len(phases)!=1 or len(stage)!=1:raise ValueError('eras and phases cannot mix')
 if any(r['date']>=datetime.now(timezone.utc).date().isoformat() for r in rows):raise ValueError('no current-day or future result allowed')
 if any(datetime.fromisoformat(r['kickoff_at'].replace('Z','+00:00')).date().isoformat()!=r['date'] for r in rows if r.get('kickoff_at')):raise ValueError('date/kickoff mismatch')
 # For two-leg routes, do not silently combine first and second legs.
 if any('leg' in r['stage'] for r in rows) and len({r.get('leg_number') for r in rows})!=1:raise ValueError('leg cohorts must be isolated')
 return rows

def main():
 import argparse
 p=argparse.ArgumentParser();p.add_argument('--inputs',nargs='+',required=True);p.add_argument('--out',required=True);a=p.parse_args();out=Path(a.out);out.mkdir(parents=True,exist_ok=False)
 sources={str(Path(f)):hashlib.sha256(Path(f).read_bytes()).hexdigest() for f in a.inputs};protocol={'created_at':datetime.now(timezone.utc).isoformat(),'before_fit':True,'inputs':sources,'scope':'independent UEFA research route additions','versions':{'current':weighted.legacy.VERSION,'test':ml.VERSION},'current_algorithm':'existing MAIN baseline anchor; weighted candidate diagnostic retained','test_algorithm':'UCL-specific LightGBM Poisson/DC with independent missing process fields; protected existing routes unchanged','splits':'existing 50/25/25 date split; rolling monthly prior histories','primary_metric':'exact-score Top1','formal_freeze':False,'legacy_500_plus_200_unchanged':True}
 (out/'pre-fit-protocol.json').write_text(json.dumps(protocol,indent=2)+'\n');routes={};reports=[]
 for f in a.inputs:
  raw=json.loads(Path(f).read_text());rows=raw if isinstance(raw,list) else raw['rows'];cohorts={r['cohort'] for r in rows}
  if len(cohorts)!=1:raise ValueError('input file is not one cohort')
  cohort=next(iter(cohorts));folder=out/cohort.replace(':','-');folder.mkdir()
  try:
   rows=validate(rows);artifact,report=weighted.train(rows);test,test_report=ml.train(rows)
  except ValueError as e:
   reports.append({'cohort':cohort,'status':'NOT_REGISTERABLE','reason':str(e),'rows':len(rows)});print(json.dumps(reports[-1]),flush=True);continue
  for name,obj in [('current-artifact',artifact),('current-report',report),('test-artifact',test),('test-report',test_report)]:
   (folder/(name+'.json')).write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
  routes[cohort]={'artifact_path':str(folder/'current-artifact.json'),'artifact_sha256':hashlib.sha256((folder/'current-artifact.json').read_bytes()).hexdigest(),'test_artifact_path':str(folder/'test-artifact.json'),'test_artifact_sha256':hashlib.sha256((folder/'test-artifact.json').read_bytes()).hexdigest(),'test_version':ml.VERSION,'scope':'UEFA_ERA_PHASE_ISOLATED_RESEARCH','report_path':str(folder/'current-report.json'),'test_report_path':str(folder/'test-report.json')}
  summary={'cohort':cohort,'status':'RESEARCH_READY','rows':len(rows),'dates':len({r['date'] for r in rows}),'current_test':report['test']['baseline'],'test_model_test':test_report['test']['ml'],'test_acceptance':test_report['acceptance'],'formal_validation':False,'training_end':rows[-1]['date'],'teams':len({r[k] for r in rows for k in ('home','away')})};reports.append(summary)
  print(json.dumps({k:v for k,v in summary.items() if k not in ('current_test','test_model_test')}),flush=True)
 (out/'queue-routes.json').write_text(json.dumps(routes,ensure_ascii=False,indent=2)+'\n');(out/'training-summary.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2)+'\n')
if __name__=='__main__':main()
