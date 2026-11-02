"""Reproduce all registered-cohort ML artifacts without changing activation."""
import argparse
import hashlib
import json
import sys
from pathlib import Path
from ml_model import train

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'data_sources'))
from history_process import enrich


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--registry',default='/workspace/WCPP/runtime/active-model.json')
    p.add_argument('--out',required=True)
    p.add_argument('--cohort',help='Optional single already registered cohort')
    a=p.parse_args();root=Path(a.out)
    registry=json.loads(Path(a.registry).read_text());summary=[]
    if a.cohort and a.cohort not in registry['models']:raise ValueError('unregistered cohort')
    root.mkdir(parents=True,exist_ok=False)
    for cohort,entry in registry['models'].items():
        if a.cohort and cohort!=a.cohort:continue
        source=Path(entry['artifact_path'])
        if hashlib.sha256(source.read_bytes()).hexdigest()!=entry['artifact_sha256']:
            raise ValueError('current artifact checksum mismatch')
        base=json.loads(source.read_text());rows,audit=enrich(base['history'])
        artifact,report=train(rows);folder=root/cohort.replace(':','-');folder.mkdir()
        for name,data in [('artifact',artifact),('report',report)]:
            (folder/(name+'.json')).write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False))
        summary.append({'cohort':cohort,'artifact_path':str(folder/'artifact.json'),
                        'artifact_sha256':hashlib.sha256((folder/'artifact.json').read_bytes()).hexdigest(),
                        'process_audit':audit,'test':{k:{x:v for x,v in values.items() if x!='records'}
                                                   for k,values in report['test'].items()}})
        (root/'training-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
        print(cohort,flush=True)


if __name__=='__main__':main()
