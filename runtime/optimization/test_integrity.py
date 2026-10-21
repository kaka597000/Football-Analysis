import unittest,copy,tempfile
from pathlib import Path
import numpy as np
from features import History,NAMES
from models import fit_core,predict,matrix
from schema import fixture,digest
from availability import eligible,VERIFIED
from ledger import record,read,check_matrix
class Integrity(unittest.TestCase):
 def setUp(self):
  self.fs=[dict(match_id=str(i),date='2020-%02d-%02d'%(1+i//28,1+i%28),cohort='test',competition='test',season='2020',home='A' if i%2 else 'B',away='B' if i%2 else 'A',neutral=None,stage='regular') for i in range(100)]
  self.labels={r['match_id']:dict(match_id=r['match_id'],home_goals=i%4,away_goals=i%3) for i,r in enumerate(self.fs)}
 def test_target_future_labels_facts_invariance(self):
  t=fixture(self.fs[80]);prior=[{**r,**self.labels[r['match_id']]} for r in self.fs[:80]];core=fit_core(prior)
  h=History(self.fs,self.labels,[]);x,proof=h.vector(t,origin=t['date']);a=predict(core,[t],h)
  altered=copy.deepcopy(self.labels)
  for r in self.fs[80:]:altered[r['match_id']].update(home_goals=99,away_goals=90)
  f=[dict(match_id='90',date=self.fs[90]['date'],kind='xg',provider='understat',fact_id='future',home_xg=999,away_xg=999)]
  hb=History(self.fs,altered,f);np.testing.assert_array_equal(x,hb.vector(t,origin=t['date'])[0]);np.testing.assert_array_equal(a,predict(core,[t],hb))
 def test_fixed_origin_all_audit_labels_invariant(self):
  t=fixture(self.fs[95]);origin=self.fs[80]['date'];a=History(self.fs,self.labels,[]).vector(t,origin)[0]
  for r in self.fs[80:]:self.labels[r['match_id']].update(home_goals=50,away_goals=50)
  np.testing.assert_array_equal(a,History(self.fs,self.labels,[]).vector(t,origin)[0])
 def test_providers_never_mixed_missing_nan(self):
  t=fixture(self.fs[80]);facts=[dict(match_id='0',date=self.fs[0]['date'],kind='xg',provider='unapproved',fact_id='f',home_xg=90,away_xg=90)]
  x,_=History(self.fs,self.labels,facts).vector(t);self.assertTrue(np.isnan(x[0,22]));self.assertEqual(x[0,24],0)
 def test_unknown_venue_preserved(self):
  x,_=History(self.fs,self.labels,[]).vector(fixture(self.fs[80]));self.assertEqual(x[0,1],1);self.assertIsNone(fixture(self.fs[80])['neutral'])
 def test_matrix_tail_and_receipt_tampering(self):
  m=matrix([8,7]);check_matrix(m);self.assertAlmostEqual(np.arange(len(m))@m.sum(axis=1),8,10)
  with tempfile.TemporaryDirectory() as t:
   r=record(t,dict(match_id='x',model='x',fold='x',status='PREDICTED',matrix=m.tolist()));read(r);Path(r['path']).write_text('{}')
   with self.assertRaises(ValueError):read(r)
 def test_verified_unknown_rejects_and_lag(self):
  f={'date':'2020-01-01','first_known_at':None};self.assertFalse(eligible(f,'2020-02-01',VERIFIED,cutoff='2020-02-01T00:00:00+00:00'));self.assertFalse(eligible(f,'2020-01-08'));self.assertTrue(eligible(f,'2020-01-09'))
 def test_fixture_rejects_postmatch(self):
  from schema import validate_fixture
  with self.assertRaises(ValueError):validate_fixture({**fixture(self.fs[90]),'home_goals':1})

class LearnedResidualIntegrity(unittest.TestCase):
 """Synthetic leakage checks exercise fitted LightGBM, not football accuracy."""
 @classmethod
 def setUpClass(cls):
  from datetime import date,timedelta
  from models import fit_residual
  cls.fs=[];cls.labels={};cls.facts=[]
  teams=['A','B','C','D','E','F']
  for i in range(500):
   home=teams[i%6];away=teams[(i+1+i//6)%6]
   if home==away:away=teams[(teams.index(away)+1)%6]
   r=dict(match_id='synthetic-'+str(i),date=(date(2020,1,1)+timedelta(days=i)).isoformat(),cohort='synthetic',competition='synthetic',season='synthetic',home=home,away=away,neutral=None,stage='regular')
   cls.fs.append(r);phase=(i//35)%3
   h=(0,2,5)[phase]+int(i%7==0);a=(0,1,3)[phase]+int(i%11==0)
   cls.labels[r['match_id']]=dict(match_id=r['match_id'],home_goals=h,away_goals=a)
   cls.facts.extend([
    dict(match_id=r['match_id'],date=r['date'],kind='xg',provider='understat',fact_id='xg-'+str(i),home_xg=h+.2,away_xg=a+.15),
    dict(match_id=r['match_id'],date=r['date'],kind='process',provider='football-data.co.uk',fact_id='process-'+str(i),home_shots=4+3*h,away_shots=3+3*a,home_sot=1+h,away_sot=1+a)])
  cls.origin=cls.fs[400]['date'];cls.config=dict(half_life=365,ridge=30)
  cls.audit_labels,cls.audit_facts=cls.perturb(400)
  cls.target_labels,cls.target_facts=cls.perturb(430)
  cls.history=History(cls.fs,cls.labels,cls.facts)
  cls.audit_history=History(cls.fs,cls.audit_labels,cls.audit_facts)
  cls.target_history=History(cls.fs,cls.target_labels,cls.target_facts)
  cls.prior=[{**r,**cls.labels[r['match_id']]} for r in cls.fs[:400]]
  cls.changed_prior=[{**r,**cls.audit_labels[r['match_id']]} for r in cls.fs[:400]]
  cls.core=fit_core(cls.prior,**cls.config)
  cls.changed_core=fit_core(cls.changed_prior,**cls.config)
  cls.booster,cls.metadata=fit_residual(cls.prior,cls.history,cls.config,'xg')
  cls.changed_booster,cls.changed_metadata=fit_residual(cls.changed_prior,cls.audit_history,cls.config,'xg')

 @classmethod
 def perturb(cls,start):
  labels=copy.deepcopy(cls.labels);facts=copy.deepcopy(cls.facts)
  ids={r['match_id'] for r in cls.fs[start:]}
  for mid in ids:labels[mid].update(home_goals=29,away_goals=27)
  for f in facts:
   if f['match_id'] in ids:
    for key in ('home_xg','away_xg','home_shots','away_shots','home_sot','away_sot'):
     if key in f:f[key]=1000.
  return labels,facts

 def test_actual_fitted_booster_target_future_matrix_invariance(self):
  target=fixture(self.fs[430]);origin=target['date']
  self.assertGreater(self.metadata['oof_matches']*2,200)
  self.assertGreater(self.booster.current_iteration(),0)
  self.assertTrue(any(tree['num_leaves']>1 for tree in self.booster.dump_model()['tree_info']))
  base=predict(self.core,[target],self.history)
  learned=predict(self.core,[target],self.history,self.booster,'xg',.5,origin)
  changed=predict(self.core,[target],self.target_history,self.booster,'xg',.5,origin)
  # The learner actually affects this forecast; omitting it cannot pass this check.
  self.assertGreater(float(np.max(np.abs(learned-base))),1e-8)
  np.testing.assert_array_equal(learned,changed)
  np.testing.assert_array_equal(matrix(learned[0]),matrix(changed[0]))
  before=self.history.vector(target,origin)[1];after=self.target_history.vector(target,origin)[1]
  self.assertEqual(before,after)
  self.assertNotIn(target['match_id'],before['historical_match_ids'])

 def test_fixed_origin_audit_labels_facts_cannot_change_fitted_models(self):
  self.assertNotEqual(self.labels,self.audit_labels)
  self.assertNotEqual(self.facts,self.audit_facts)
  self.assertEqual(self.core,self.changed_core)
  self.assertEqual(self.metadata,self.changed_metadata)
  self.assertEqual(self.booster.model_to_string(),self.changed_booster.model_to_string())
  targets=[fixture(self.fs[i]) for i in (400,430,499)]
  baseline=predict(self.core,targets,self.history)
  changed_baseline=predict(self.changed_core,targets,self.audit_history)
  np.testing.assert_array_equal(baseline,changed_baseline)
  learned=predict(self.core,targets,self.history,self.booster,'xg',.5,self.origin)
  changed=predict(self.changed_core,targets,self.audit_history,self.changed_booster,'xg',.5,self.origin)
  np.testing.assert_array_equal(learned,changed)
  for x,y in zip(learned,changed):np.testing.assert_array_equal(matrix(x),matrix(y))
  for target in targets:
   self.assertEqual(self.history.vector(target,self.origin)[1],self.audit_history.vector(target,self.origin)[1])

 def test_production_pipeline_selection_is_fixed_before_audit_labels(self):
  import json
  import pipeline
  from schema import code_hash,file_hash
  # Two predeclared members of the production grid suffice to exercise its
  # actual bias/CRPS gate and tie-breaking selection without copying that code.
  configs=[copy.deepcopy(c) for c in pipeline.GRID if c['name'] in ('M2-h365-r30','M3-xg-a0.5')]
  self.assertEqual(len(configs),2)
  with tempfile.TemporaryDirectory() as root:
   root=Path(root);runs=[]
   for index,(labels,facts) in enumerate(((self.labels,self.facts),(self.audit_labels,self.audit_facts))):
    ds=root/('dataset-'+str(index));ds.mkdir()
    payloads={'fixtures.jsonl':self.fs,'labels.jsonl':list(labels.values()),'facts.jsonl':facts}
    for name,rows in payloads.items():
     (ds/name).write_text(''.join(json.dumps(r,sort_keys=True)+'\n' for r in rows))
    (ds/'manifest.json').write_text(json.dumps({'files':{name:file_hash(ds/name) for name in payloads},'active_cohorts':['synthetic']}))
    folds=[]
    for name,start,end in (('dev1',300,350),('dev2',350,400),('audit',400,500)):
     folds.append(dict(name=name,start=self.fs[start]['date'],end=self.fs[end]['date'] if end<500 else '9999-12-31',fit_ids=[r['match_id'] for r in self.fs[:start]],target_ids=[r['match_id'] for r in self.fs[start:end]]))
    protocol=dict(code_hash=code_hash(),dataset=str(ds),dataset_manifest_hash=file_hash(ds/'manifest.json'),time_track='RETROSPECTIVE_PRIOR_ONLY',candidates=configs,cohorts=[dict(cohort='synthetic',status='PLANNED',n=500,folds=folds)])
    spec=root/('protocol-'+str(index)+'.json');spec.write_text(json.dumps(protocol))
    out=root/('run-'+str(index));pipeline.train(spec,out)
    runs.append(out)
   a,b=[p/'synthetic' for p in runs]
   selections=[json.loads((p/'development-selection.json').read_text()) for p in (a,b)]
   self.assertEqual(selections[0]['selected'],selections[1]['selected'])
   self.assertEqual(selections[0]['all_development_metrics'],selections[1]['all_development_metrics'])
   for fold in ('dev1','dev2','audit'):
    self.assertEqual((a/(fold+'-xg.lgb.txt')).read_text(),(b/(fold+'-xg.lgb.txt')).read_text())
   def predictions(run):
    receipts=json.loads((run/'receipt-index.json').read_text())
    return {(row['match_id'],row['model'],row['fold']):row for row in map(read,receipts)}
   original,changed=map(predictions,runs)
   self.assertEqual(original.keys(),changed.keys())
   for key,row in original.items():
    other=changed[key]
    for field in ('status','feature_hash','feature_values','provenance','input_hash','rates','matrix','matrix_sha256','model_meta'):
     self.assertEqual(row.get(field),other.get(field),(key,field))
   # Scoring is expected to change: otherwise the audit-label intervention
   # might not have reached the real run at all.
   auditfile='audit-M2-h365-r30-scores.json'
   self.assertNotEqual(json.loads((a/auditfile).read_text())[0]['actual_total'],json.loads((b/auditfile).read_text())[0]['actual_total'])
   self.assertEqual(code_hash(),protocol['code_hash'])


if __name__=='__main__':unittest.main()
