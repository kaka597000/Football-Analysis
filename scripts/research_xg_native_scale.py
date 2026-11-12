"""Reproduce a pinned xG development study and one explicit scale experiment."""
import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from research_program.xg_native_scale import fit

REFERENCE_BLOB='7115767937a8d815aeeec5548f187780908b3621'
FEATURE_BLOB='5629ad8d7f7d978711cb71f8cb0b71d0a24e269b'
INPUT_SHA='8bf576f99b286b1cc084bfa69b2dc2aea3206d00ae5ba16aee4b8950dbe500bf'


def git_blob(path):
    b=Path(path).read_bytes()
    return hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()


def write(path,value):
    with path.open('x',encoding='utf-8') as f:
        json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n')


def run(records,reference,out):
    if git_blob(reference)!=REFERENCE_BLOB or git_blob(ROOT/'runtime/research_program/feature_ablation.py')!=FEATURE_BLOB:
        raise ValueError('pinned reference implementation differs')
    raw=Path(records).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=INPUT_SHA:raise ValueError('retained input identity mismatch')
    spec=importlib.util.spec_from_file_location('research_program.xg_reference',reference)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    protocol=copy.deepcopy(m.PROTOCOL)
    protocol.update(version='WCPP-XG-NATIVE-SCALE-20261112-1',
        scaling='bounded availability features use physical range; continuous goal/xG use training std',
        native_scale_module_sha256=hashlib.sha256((ROOT/'runtime/research_program/xg_native_scale.py').read_bytes()).hexdigest(),
        post_prediction_rate_clipping=False,prior_failed_protocol_preserved=True,
        fresh='same fixed freshness/missingness recipe; native availability scales',
        residual='same opponent residual recipe; native availability scales',
        selection='fixed development hypothesis; no candidate selection; no promotion')
    write(out/'predeclared-protocol.json',protocol)
    data=json.loads(raw)['records']
    original=m.run(data,out/'original',INPUT_SHA,mode='retrospective')
    original_fit=m.fit_model
    m.fit_model=lambda rows,variant,p: (original_fit(rows,variant,p) if variant in ('goals_only','original_xg')
                                      else fit(rows,variant,p,m.FEATURE_NAMES))
    native=m.run(data,out/'native',INPUT_SHA,mode='retrospective',protocol=protocol)
    def rows(folder):
        return [json.loads(s) for s in (folder/'predictions.jsonl').read_text().splitlines()]
    old,new=rows(out/'original'),rows(out/'native')
    if [r['match_id'] for r in old]!=[r['match_id'] for r in new]:raise ValueError('changed common population')
    for a,b in zip(old,new):
        for variant in ('goals_only','original_xg'):
            if a['models'][variant]!=b['models'][variant]:raise ValueError('control arm changed')
    result={}
    for variant in m.VARIANTS:
        summary={}
        for name,rs in [('original',old),('native',new)]:
            values=[r['models'][variant] for r in rs]
            summary[name]={'n':len(values),'hits':sum(v['top1_hit'] for v in values),
                'mean_score_log_loss':sum(v['score_log_loss'] for v in values)/len(values),
                'rates_gt_10':sum(max(v['lambda_home'],v['lambda_away'])>10 for v in values),
                'max_lambda':max(max(v['lambda_home'],v['lambda_away']) for v in values)}
        result[variant]=summary
    write(out/'comparison.json',{'input_sha256':INPUT_SHA,'paired_n':len(old),'arms':result,
        'same_control_predictions':True,'asof_certified':False,'prospective':False,
        'active_modified':False,'promotion':False,
        'limits':['Previously inspected development data; not pristine holdout.','Real timestamps still incomplete.',
                  'Numerical stability is not proof of future accuracy.','No post-prediction lambda clipping.']})
    print(json.dumps(result),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--records',required=True);p.add_argument('--reference',required=True);p.add_argument('--out',required=True);a=p.parse_args()
    run(a.records,a.reference,a.out)
