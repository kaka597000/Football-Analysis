"""Train isolated development challengers from hash-verified retained histories."""
import argparse
from datetime import datetime,timezone
import hashlib,json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from competition_scope import require
from research_program.challengers import experiment
from prediction_engine import source_identity

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def train(registry_path,out,cohorts):
    registry=json.loads(Path(registry_path).read_text(encoding='utf-8'));out=Path(out)
    out.mkdir(parents=True,exist_ok=False);summary=[]
    for cohort in cohorts:
        require({'cohort':cohort});route=registry['models'][cohort];path=Path(route['artifact_path'])
        before=sha(path)
        if before!=route['artifact_sha256']:raise ValueError('source artifact checksum mismatch')
        source=json.loads(path.read_text(encoding='utf-8'));history=source.get('history') or source.get('anchor',{}).get('history')
        if not history:raise ValueError('retained source history missing')
        result=experiment(history)
        result['provenance']={'source_artifact_sha256':before,'source_registry_sha256':sha(registry_path),
            'source_identity':source_identity(),'history_training_end':max(r['date'] for r in history),
            'missing_first_known':sum(not r.get('first_known_at') for r in history),
            'missing_season_identity':sum(not r.get('season') for r in history)}
        pathout=out/(cohort.replace(':','-')+'.json');pathout.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
        if sha(path)!=before:raise ValueError('source artifact changed during independent experiment')
        summary.append({'cohort':cohort,'history_rows':len(history),'history_end':result['provenance']['history_training_end'],
                        'selected_family':result['selected']['family'],'parameter':result['selected']['parameter'],
                        'diagnostic':result['diagnostic']['metrics'],'dynamic_poisson':result['dynamic_poisson_diagnostic']['metrics'],
                        'candidate_sha256':sha(pathout),'status':'DEVELOPMENT_NOT_PROMOTED',
                        'historical_asof_certified':False,'compared_with_actual_Current':False})
        print(cohort,flush=True)
    (out/'summary.json').write_text(json.dumps({'generated_at':datetime.now(timezone.utc).isoformat(),'cohorts':summary,'active_registry_changed':False,'accuracy_improvement_proven':False},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return summary
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--registry',required=True);p.add_argument('--out',required=True);p.add_argument('--cohorts',nargs='+',required=True);a=p.parse_args()
    train(a.registry,a.out,a.cohorts)
