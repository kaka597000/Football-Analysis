"""Explicit candidate review only; does not activate or retroactively regenerate forecasts."""
import argparse
import hashlib
import json
from pathlib import Path
from continued_model import predict,legacy


def main():
    p=argparse.ArgumentParser()
    p.add_argument('artifact');p.add_argument('fixture');p.add_argument('--out',required=True)
    p.add_argument('--registry',default='/workspace/WCPP/runtime/active-model.json')
    args=p.parse_args();a=json.loads(Path(args.artifact).read_text());f=json.loads(Path(args.fixture).read_text())
    registry=json.loads(Path(args.registry).read_text());entry=registry['models'][f['cohort']]
    basepath=Path(entry['artifact_path'])
    if hashlib.sha256(basepath.read_bytes()).hexdigest()!=entry['artifact_sha256']:raise ValueError('current model checksum mismatch')
    current=legacy.predict(json.loads(basepath.read_text())['anchor'],f);current['model_label']='当前模型'
    candidate=predict(a,current,f)
    result={'match_id':f['match_id'],'current_model':current,'test_model':candidate,
            'execution':'EXPLICIT_NONACTIVE_CANDIDATE_RESEARCH','formal_kpi_eligible':False}
    with Path(args.out).open('x') as file:json.dump(result,file,ensure_ascii=False,indent=2,allow_nan=False)
    print(args.out)


if __name__=='__main__':main()
