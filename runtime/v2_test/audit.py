"""Read-only phase-one V2 inventory; does not backtest or change activation."""
import argparse,collections,hashlib,json,sqlite3
from pathlib import Path
from datetime import datetime,timezone


def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        while b:=f.read(1024*1024):h.update(b)
    return h.hexdigest()


def profile_history(rows):
    dates=[r.get('date') for r in rows if r.get('date')]
    ids=[r.get('match_id') for r in rows if r.get('match_id')]
    keys=[tuple(r.get(k) for k in ('cohort','date','home','away')) for r in rows]
    return {'rows':len(rows),'date_min':min(dates) if dates else None,'date_max':max(dates) if dates else None,
        'missing_match_id':sum(not r.get('match_id') for r in rows),
        'duplicate_match_id_excess':len(ids)-len(set(ids)),
        'duplicate_fixture_key_excess':len(keys)-len(set(keys)),
        'missing_first_known_at':sum(not r.get('first_known_at') for r in rows),
        'missing_observed_at':sum(not r.get('observed_at') for r in rows),
        'observed_after_match_day':sum(bool(r.get('observed_at') and r.get('date') and r['observed_at'][:10]>r['date']) for r in rows),
        'invalid_goals':sum(any(type(r.get(k)) is not int or r[k]<0 for k in ('home_goals','away_goals')) for r in rows),
        'cohorts':dict(collections.Counter(str(r.get('cohort')) for r in rows)),
        'limitation':'Missing first-known time prevents certifying historical as-of availability; backfill is not by itself proof of leakage.'}


def database_profile(path):
    c=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True);c.execute('pragma query_only=on')
    queries={
      'match_summary':'''select count(*) rows, sum(training_eligible=1) training_eligible, min(date) date_min,max(date) date_max,
       sum(home_id=away_id) same_team,sum(h90<0 or a90<0) negative_goals,
       sum(training_eligible=1 and (h90 is null or a90 is null)) eligible_missing_score from matches''',
      'eligible_by_competition':'''select competition,count(*) rows,min(date) date_min,max(date) date_max from matches where training_eligible=1 group by competition''',
      'observation_summary':'''select count(*) rows,sum(first_known_at is null or first_known_at='') missing_first_known,
       sum(observed_at is null or observed_at='') missing_observed,
       sum(source_id not in (select source_id from sources)) orphan_source_id from observations''',
      'duplicate_observation_keys':'''select count(*) keys from (select provider,source_key,competition,count(*) n from observations group by provider,source_key,competition having count(*)>1)''',
    };out={}
    for name,q in queries.items():
        cursor=c.execute(q);cols=[x[0] for x in cursor.description];out[name]={'sql':q,'result':[dict(zip(cols,row)) for row in cursor.fetchall()]}
    out['quick_check']=c.execute('pragma quick_check').fetchone()[0];c.close();return out


def run(registry_path,db_path):
    reg=json.loads(Path(registry_path).read_text());routes=[]
    for cohort,route in reg['models'].items():
        models={}
        for prefix in ('','test_'):
            p=Path(route[prefix+'artifact_path']);a=json.loads(p.read_text());history=a.get('history') or a.get('anchor',{}).get('history') or []
            models[prefix or 'current']={'path':str(p),'actual_sha256':sha(p),'registered_sha256':route[prefix+'artifact_sha256'],
              'hash_matches':sha(p)==route[prefix+'artifact_sha256'],'version':a.get('version'),
              'engine':route.get(prefix+'engine','legacy_anchor' if not prefix else a.get('version')),
              'train_end':a.get('train_end') or a.get('core',{}).get('fit_end'),
              'history':profile_history(history),'history_embedded':bool(history),
              'declared_xg_training_rows':a.get('feature_xg_match_count'),
              'declared_process_training_rows':a.get('feature_process_match_count'),
              'alpha':a.get('alpha'),'stored_expert_weights':a.get('weights'),
              'stored_expert_weights_used_as_current':False if a.get('anchor') else None,
              'current_execution_recipe':a.get('anchor',{}).get('execution_recipe'),
              'acceptance':a.get('acceptance'),
              'coverage_note':'No embedded history is not zero training: inspect dataset manifests for this engine.'}
        routes.append({'cohort':cohort,'models':models})
    return {'schema':'WCPP-V2-PHASE1-AUDIT-1','generated_at':datetime.now(timezone.utc).isoformat(),
      'registry':str(Path(registry_path).resolve()),'registry_sha256':sha(registry_path),'registry_version':reg['version'],
      'routes':routes,'database':{'path':str(Path(db_path).resolve()),'profile':database_profile(db_path)},
      'claims':{'activation_changed':False,'new_model_trained':False,'dynamic_fusion_enabled':False,
                'historical_asof_leakage_cleared':False,'forecast_uplift_proven':False,
                'audit_includes_new_rolling_backtest':False}}


def main():
    p=argparse.ArgumentParser();p.add_argument('--registry',required=True);p.add_argument('--db',required=True);p.add_argument('--out',required=True);a=p.parse_args()
    out=Path(a.out)
    if out.exists():raise FileExistsError(out)
    result=run(a.registry,a.db);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({'cohorts':len(result['routes']),'artifact_hash_failures':sum(not v['hash_matches'] for r in result['routes'] for v in r['models'].values()),'db_check':result['database']['profile']['quick_check']}))
if __name__=='__main__':main()
