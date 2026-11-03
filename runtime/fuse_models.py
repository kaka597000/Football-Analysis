"""Unified current/test probability fusion reference, separate from active models.

Equal weights are an explicitly untrained research baseline, not a promoted
dynamic ensemble. Score classifiers and incomplete candidate models are not
joint-score experts and never receive an invented matrix or weight.
"""
import argparse
from datetime import datetime, timezone
import json
import re
from pathlib import Path
import numpy as np
from predict_active import run as run_active, object_sha256, legacy
from research.team_goal_analysis import summarize_matrix
from research.analysis_freeze import aware, save


def fuse(pair, *, now=None):
    now=now or datetime.now(timezone.utc)
    a,b=pair['current_model'],pair['test_model'];fixture=a['input']
    if b['input']!=fixture or fixture['match_id']!=pair['match_id']:
        raise ValueError('identical fixture, source and cutoff inputs required')
    if fixture.get('identity_verified') is not True or fixture.get('phase')!='PreMatch':
        raise ValueError('verified pre-match identity required')
    cutoff,kickoff=aware(fixture['cutoff_at']),aware(fixture['kickoff_at'])
    if not cutoff<=now<kickoff:raise ValueError('future pre-match fixture required')
    components=[];arrays=[]
    for name,p in [('current',a),('test',b)]:
        if not cutoff<=aware(p['generated_at'])<=now:raise ValueError('invalid expert generation time')
        m=p.get('matrix');summary=summarize_matrix(m)
        if object_sha256(m)!=p.get('matrix_hash'):raise ValueError('expert matrix hash mismatch')
        provenance=pair['execution_audit'][name]
        sha=provenance['artifact_sha256']
        if not isinstance(sha,str) or not re.fullmatch('[0-9a-f]{64}',sha):
            raise ValueError('explicit artifact identity required')
        components.append({'name':name,'version':p['version'],'engine':provenance['engine'],
            'artifact_sha256':sha,'matrix_sha256':p['matrix_hash'],
            'training_cutoff':p.get('training_data_cutoff'),'weight':.5})
        arrays.append(np.array(m,float))
    if components[0]['artifact_sha256']==components[1]['artifact_sha256']:
        raise ValueError('same artifact cannot count as independent experts')
    shape=np.max([m.shape for m in arrays],axis=0)
    arrays=[np.pad(m,[(0,int(shape[i]-m.shape[i])) for i in range(2)]) for m in arrays]
    m=(arrays[0]+arrays[1])/2
    h,aidx=np.indices(m.shape);primary=np.unravel_index(m.argmax(),m.shape)
    wdl=dict(zip(('主胜','平局','客胜'),[float(m[h>aidx].sum()),float(m[h==aidx].sum()),float(m[h<aidx].sum())]))
    total=None;total_status='NO_VERIFIED_CURRENT_QUOTE'
    q=fixture.get('total_quote')
    if q:
        # A recorded line without source time is not a current market quote.
        if q.get('quoted_at') and q.get('query_time'):
            qt,query=aware(q['quoted_at']),aware(q['query_time'])
            if qt<=query<=cutoff and 0<=(cutoff-qt).total_seconds()<=60:
                checked=legacy.quote_check(q,fixture)
                if checked:
                    total={'quote':checked,'over':legacy.total_settlement(m,checked['line'],'over'),
                           'under':legacy.total_settlement(m,checked['line'],'under')}
                    total_status='MATRIX_SETTLEMENT_ONLY_NOT_BETTING_EV'
            else:total_status='QUOTE_STALE_OR_TIME_CONFLICT'
    return {'version':'WCPP-UNIFIED-FUSION-REFERENCE-0.1','status':'EXPERIMENTAL_NOT_PROMOTED',
        'match_id':fixture['match_id'],'cohort':fixture['cohort'],'input':fixture,
        'generated_at':now.isoformat(),'policy':'UNTRAINED_CURRENT_TEST_EQUAL_WEIGHT',
        'components':components,'matrix':m.tolist(),'matrix_hash':object_sha256(m.tolist()),
        'primary':f'{primary[0]}-{primary[1]}','primary_type':'STATISTICAL_MODE_NOT_COMPREHENSIVE_PRIMARY',
        'wdl':wdl,'team_goal_analysis':summarize_matrix(m.tolist()),'total':total,'total_status':total_status,
        'expert_similarity':{'total_variation':float(np.abs(arrays[0]-arrays[1]).sum()/2),
            'same_statistical_primary':a['primary']==b['primary']},
        'excluded_from_probability_fusion':['upset-classifiers: different target space',
            'ML-0.2 and rejected fusion/calibration candidates: not accepted',
            'untrained player/market/selector modules: no qualified joint matrix',
            'other leagues and incoming asset conflicts: separate identities'],
        'active_models_modified':False,'trained_fusion':False,'accuracy_improvement_proven':False,
        'notice':'Current/test may be highly correlated. Equal weighting is a reproducible comparison '
                 'reference only; no claim of independent evidence or increased accuracy. '
                 'Manual comprehensive Primary remains separately reasoned and frozen.'}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('fixture');p.add_argument('--registry');p.add_argument('--out',required=True)
    p.add_argument('--freeze-root',default='/workspace/WCPP/forecasts/issued-analyses')
    a=p.parse_args();path=Path(a.out)
    if path.exists():raise FileExistsError(path)
    fixture=json.loads(Path(a.fixture).read_text())
    pair=run_active(fixture,a.registry);fusion=fuse(pair)
    # Independent stream: old current/test recommendations and matrices retained.
    result={'current_test_pair':pair,'fusion_reference':fusion}
    result['current_test_freeze_receipt']=save(pair,fixture,stage='prematch',
        track='current-test-pair',root=a.freeze_root)
    result['freeze_receipt']=save(result,fixture,stage='prematch',track='ensemble-research',root=a.freeze_root)
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x') as f:json.dump(result,f,ensure_ascii=False,indent=2,allow_nan=False)
    print(json.dumps({'out':str(path),'freeze_id':result['freeze_receipt']['freeze_id'],
                      'fusion_primary':fusion['primary'],'status':fusion['status']}))


if __name__=='__main__':main()
