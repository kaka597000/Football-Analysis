"""Reproduce continued-training candidates; never changes activation registry."""
import argparse
import hashlib
import json
from pathlib import Path
from continued_model import train


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--registry',required=True);p.add_argument('--data',required=True)
    p.add_argument('--cohort');p.add_argument('--out',required=True)
    args=p.parse_args();registry=json.loads(Path(args.registry).read_text())
    datasets=json.loads((Path(args.data)/'data-audit.json').read_text())
    if args.cohort:
        datasets=[d for d in datasets if d['cohort']==args.cohort]
        if not datasets:raise ValueError('requested cohort not exported')
    root=Path(args.out);root.mkdir(parents=True,exist_ok=False);summary=[]
    for d in datasets:
        entry=registry['models'][d['cohort']]
        oldpath=Path(entry['test_artifact_path'])
        if hashlib.sha256(oldpath.read_bytes()).hexdigest()!=entry['test_artifact_sha256']:
            raise ValueError('previous model checksum mismatch')
        source=Path(d['history_path'])
        if hashlib.sha256(source.read_bytes()).hexdigest()!=d['history_sha256']:
            raise ValueError('training dataset checksum mismatch')
        previous=json.loads(oldpath.read_text());oldreport=json.loads(oldpath.with_name('report.json').read_text())
        if previous['version']!='WCPP-ML-0.1':raise ValueError('this protocol compares against ML0.1')
        artifact,report=train(json.loads(source.read_text()),previous['history'],oldreport)
        folder=root/d['cohort'].replace(':','-');folder.mkdir()
        for name,value in [('artifact',artifact),('report',report)]:
            (folder/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False))
        summary.append({'cohort':d['cohort'],'history_n':len(artifact['history']),
                        'test_n':report['test']['new']['n'],'primary_hits':report['test']['new']['primary_hits'],
                        'alpha':artifact['alpha'],'params':artifact['params']})
        (root/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
        print(d['cohort'],flush=True)


if __name__=='__main__':main()
