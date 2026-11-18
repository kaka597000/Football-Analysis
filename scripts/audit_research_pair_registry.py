"""Report exact scope coverage without equating registration with model validation."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime'))
from competition_scope import policy
from research_pair_service import read_registry, ENGINES


def audit(active_path,research_path):
    active_bytes=Path(active_path).read_bytes();active=json.loads(active_bytes)
    research,research_sha=read_registry(research_path)
    rows=[]
    for competition in policy()['competitions']:
        code=competition['code'];retained=[]
        for cohort,route in active['models'].items():
            if cohort.split(':',1)[0]!=code:continue
            checks={}
            for role,prefix in [('current',''),('test','test_')]:
                path=Path(route[prefix+'artifact_path'])
                checks[role]=path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==route[prefix+'artifact_sha256']
            retained.append({'cohort':cohort,'artifact_hash_checks':checks})
        overlay=research['routes'].get(code)
        row={'competition':code,'name':competition['name'],'existing_routes':retained,
             'research_overlay':overlay,'actual_fixture_inference':'NOT_EXECUTED_BY_INVENTORY',
             'formal_promotion':'NOT_GRANTED_BY_INVENTORY'}
        if retained:row['status']='EXISTING_REGISTERED_PAIR_HASH_VERIFIED' if all(all(r['artifact_hash_checks'].values()) for r in retained) else 'EXISTING_ASSET_UNAVAILABLE_OR_CHANGED'
        elif overlay and overlay.get('status')=='READY_RESEARCH_PAIR' and overlay.get('engine') in ENGINES:
            p=Path(overlay.get('dependency_paths',{}).get(overlay['index_path'],overlay['index_path']))
            row['status']='RESEARCH_INDEX_HASH_VERIFIED' if p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest()==overlay['index_sha256'] else 'RESEARCH_INDEX_UNAVAILABLE_OR_CHANGED'
        else:row['status']='BLOCKED_NO_VERIFIED_PAIR'
        rows.append(row)
    return {'schema':'WCPP-PAIR-COVERAGE-AUDIT-1','scope_count':len(rows),
        'active_registry_sha256':hashlib.sha256(active_bytes).hexdigest(),
        'research_registry_sha256':research_sha,'competitions':rows,
        'active_registry_modified':False,'automatic_promotion':False}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--active-registry',required=True);p.add_argument('--research-registry',required=True);p.add_argument('--out',required=True);a=p.parse_args()
    result=audit(a.active_registry,a.research_registry)
    with Path(a.out).open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
