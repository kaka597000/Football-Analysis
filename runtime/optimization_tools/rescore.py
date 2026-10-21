"""Independently score persisted audit receipts, without fitting or predicting."""
import argparse,json,sys
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'optimization'))
from schema import save,now,file_hash
from dataset import load
from ledger import read
from evaluate import score_prediction,summarize

def rescore(run,out):
 run=Path(run);p=json.loads((run/'protocol.json').read_text());fs,labels,facts,manifest=load(p['dataset']);results=[];n=0
 for cohort in p['cohorts']:
  if cohort['status']!='PLANNED':continue
  d=run/cohort['cohort'];receipts=json.loads((d/'audit-receipt-index.json').read_text());grouped={}
  for receipt in receipts:
   row=read(receipt)
   if row['status']=='PREDICTED':
    m=np.asarray(row['matrix']);ij=list(map(int,np.unravel_index(m.argmax(),m.shape)))
    if row.get('primary')!=ij or row.get('primary_probability')!=float(m[tuple(ij)]):raise ValueError('stored Primary differs from actual matrix')
   score=score_prediction(row,labels.get(row['match_id']));grouped.setdefault(row['model'],[]).append(score);n+=1
  for model,rows in grouped.items():
   stored=json.loads((d/('audit-'+model+'-scores.json')).read_text());stored={r['match_id']:r for r in stored};mismatch=[r['match_id'] for r in rows if r!=stored.get(r['match_id'])]
   if mismatch:raise ValueError('independent score differs: '+str((cohort['cohort'],model,mismatch)))
   results.append({'cohort':cohort['cohort'],'model':model,'independent_score_rows':len(rows),'cached_scores_exactly_equal':True,'summary':summarize(rows)})
 answer={'generated_at':now(),'record_type':'INDEPENDENT_RECEIPT_RESCORE','run_protocol_hash':file_hash(run/'protocol.json'),'prediction_calls':0,'receipt_rows_verified_and_rescored':n,'results':results};save(out,answer);return {'receipt_rows_verified_and_rescored':n,'prediction_calls':0,'exact_agreement':True}
if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('--run',required=True);a.add_argument('--out',required=True);args=a.parse_args();print(json.dumps(rescore(args.run,args.out),ensure_ascii=False))
