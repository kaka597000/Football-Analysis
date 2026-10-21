"""Append-only candidate artifact inventory and independent reload verification."""
import argparse,json,sys
from pathlib import Path
import numpy as np
import lightgbm as lgb
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'optimization'))
from schema import save,now,file_hash,fixture,code_hash
from dataset import load
from features import History
from models import predict,matrix
from ledger import read

def inventory(run,final,out):
 paths=[]
 for root in (Path(run),Path(final)):
  for p in root.rglob('*'):
   if p.is_file() and p.parent.name!='predictions' and p.suffix in ('.json','.txt'):paths.append(p)
 paths.extend(Path(__file__).parent.glob('*.py'))
 value={'generated_at':now(),'optimization_code_hash':code_hash(),'run':str(run),'final':str(final),'files':[{'path':str(p.resolve()),'sha256':file_hash(p),'bytes':p.stat().st_size} for p in sorted(set(paths))],'limits':['Legacy and lag sensitivity fitted parameters were not persisted; this manifest does not invent them.','Complete saved matrices are independently checked by receipt verification.']}
 save(out,value);return {'files':len(value['files'])}

def verify(manifest,reload=False):
 value=json.loads(Path(manifest).read_text());checked=0
 for item in value['files']:
  if file_hash(item['path'])!=item['sha256']:raise ValueError('artifact file checksum changed: '+item['path'])
  checked+=1
 if code_hash()!=value['optimization_code_hash']:raise ValueError('optimization source changed')
 results=[]
 if reload:
  run=Path(value['run']);protocol=json.loads((run/'protocol.json').read_text());fs,labels,facts,man=load(protocol['dataset']);lookup={r['match_id']:r for r in fs}
  for scope in protocol['cohorts']:
   if scope['status']!='PLANNED':continue
   d=run/scope['cohort'];hist=History([r for r in fs if r['cohort']==scope['cohort']],labels,facts,7);origin=scope['folds'][-1]['start'];index=json.loads((d/'audit-receipt-index.json').read_text())
   for path in sorted(d.glob('audit-*-artifact.json')):
    art=json.loads(path.read_text());cfg=art['model'];boost=lgb.Booster(model_file=str(d/('audit-'+cfg['group']+'.lgb.txt'))) if cfg['group']!='core' else None
    receipts=[r for r in index if r['model']==cfg['name']];samples=[receipts[0],receipts[len(receipts)//2],receipts[-1]]
    for receipt in samples:
     old=read(receipt);target=fixture(lookup[old['match_id']]);mu=predict(art['core'],[target],hist,boost,cfg['group'],cfg['alpha'],origin)[0]
     if not np.array_equal(matrix(mu),np.asarray(old['matrix'])):raise ValueError('audit artifact reload differs: '+str(path))
    results.append({'artifact':str(path),'reload_matrix_exact':True,'sample_receipts':len(samples)})
  from candidate import calculate
  for item in json.loads((Path(value['final'])/'candidate-index.json').read_text()):
   p=Path(item['artifact']);check=json.loads((p.parent/'serialize-check.json').read_text());out=calculate(p,check['fixture'])
   from schema import digest
   if digest(out['matrix'])!=check['matrix_sha256']:raise ValueError('final artifact reload differs')
   results.append({'artifact':str(p),'reload_matrix_exact':True,'synthetic':True})
 return {'files_verified':checked,'optimization_code_current':True,'reload_checks':results,'unpersisted_parameter_classes':['legacy','lag_sensitivity']}

def main():
 p=argparse.ArgumentParser();s=p.add_subparsers(dest='command',required=True);i=s.add_parser('inventory');i.add_argument('--run',required=True);i.add_argument('--final',required=True);i.add_argument('--out',required=True);i=s.add_parser('verify');i.add_argument('--manifest',required=True);i.add_argument('--reload',action='store_true');i.add_argument('--out');a=vars(p.parse_args());command=a.pop('command')
 if command=='inventory':answer=inventory(**a)
 else:
  out=a.pop('out');answer=verify(**a)
  if out:save(out,answer)
 print(json.dumps(answer,ensure_ascii=False))
if __name__=='__main__':main()
