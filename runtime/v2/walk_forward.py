"""Fixed monthly expanding-window diagnostic. Does not reselect the distribution."""
import json
import math
from pathlib import Path
import numpy as np
from model import fit_strength,fit_nuisance,fit_baseline,get_matrix,metrics,partitions

ROOT=Path(__file__).resolve().parents[2] / 'reports' / 'model-upgrade-v2'

def run(path):
    rows=json.loads(path.read_text());cohort=rows[0]['cohort']
    previous=json.loads((ROOT/(cohort+'-benchmark.json')).read_text())
    *_,test=partitions(rows);start=test[0]['date'];family=previous['selected_family'];temp=previous['selected_temperature']
    months=sorted({r['date'][:7] for r in test});folds=[]
    for month in months:
        evalrows=[r for r in test if r['date'][:7]==month]
        first=evalrows[0]['date'];train=[r for r in rows if r['date']<first]
        model=fit_nuisance(fit_strength(train),train);base=fit_baseline(train)
        assert max(r['date'] for r in train)<min(r['date'] for r in evalrows)
        folds.append({'month':month,'train_n':len(train),'train_end':train[-1]['date'],
            'baseline':metrics(base,evalrows,'poisson',1.),'candidate':metrics(model,evalrows,family,temp)})
    def aggregate(key):
        n=sum(f[key]['n'] for f in folds);out={'n':n}
        for metric in ['primary_hits','wdl_hits','total_2_5_hits','unseen_team_matches']:
            out[metric]=sum(f[key][metric] for f in folds)
        for metric in ['top1','wdl_accuracy','total_2_5_accuracy','score_log_loss','wdl_brier','total_2_5_brier']:
            out[metric]=sum(f[key][metric]*f[key]['n'] for f in folds)/n
        return out
    report={'cohort':cohort,'mode':'RETROSPECTIVE_MONTHLY_EXPANDING_WINDOW_DIAGNOSTIC',
        'family':family,'temperature':temp,'formal_validation':False,'promotion':'DO_NOT_PROMOTE',
        'configuration':'refit strength and distribution nuisance before first test match each month; fixed 365-day decay and ridge8',
        'caveat':'Added after inspecting static test; not a new untouched holdout, no tuning against these outcomes',
        'baseline':aggregate('baseline'),'candidate':aggregate('candidate'),'folds':folds}
    (ROOT/(cohort+'-walk-forward.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({k:report[k] for k in ['cohort','baseline','candidate']},ensure_ascii=False),flush=True)

if __name__=='__main__':
    import sys
    for path in sys.argv[1:]:run(Path(path))
