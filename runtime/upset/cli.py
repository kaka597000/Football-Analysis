import argparse,json
from pathlib import Path
from classifier import attach,review
p=argparse.ArgumentParser();p.add_argument('command',choices=['predict','review']);p.add_argument('input');p.add_argument('--results');p.add_argument('--registry',default='/workspace/WCPP/runtime/upset-classifier.json');p.add_argument('--out',required=True)
a=p.parse_args();data=json.loads(Path(a.input).read_text())
if a.command=='predict':output=attach(data,a.registry)
else:
 if not a.results:p.error('--results is required')
 output=review(data,json.loads(Path(a.results).read_text()))
with Path(a.out).open('x') as f:json.dump(output,f,ensure_ascii=False,indent=2,allow_nan=False)
print(a.out)
