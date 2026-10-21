"""Synthetic serving checks; never contributes football outcomes."""
import json,tempfile,unittest,sys
from pathlib import Path
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parent))
import candidate as c
class Serving(unittest.TestCase):
 def test_reload_freeze_and_protection(self):
  with tempfile.TemporaryDirectory(prefix='synthetic-candidate-') as tmp:
   root=Path(tmp);ds=root/'data';ds.mkdir();fixtures=[];labels=[]
   for i in range(120):
    day=(datetime(2020,1,1)+timedelta(days=i)).date().isoformat();fixtures.append({'match_id':str(i),'date':day,'cohort':'synthetic','competition':'synthetic','season':'2020','stage':'regular','home':'a' if i%2 else 'b','away':'b' if i%2 else 'a','neutral':None});labels.append({'match_id':str(i),'home_goals':i%3,'away_goals':i%2})
   for name,rows in [('fixtures.jsonl',fixtures),('labels.jsonl',labels),('facts.jsonl',[])]:
    (ds/name).write_text(''.join(json.dumps(r)+'\n' for r in rows))
   c.save(ds/'manifest.json',{'created_at':c.now(),'files':{p.name:c.file_hash(p) for p in ds.iterdir()}})
   rows=[{**f,**l} for f,l in zip(fixtures,labels)];core=c.fit_core(rows);artifact=root/'artifact.json'
   c.save(artifact,{'cohort':'synthetic','configuration':{'group':'core','alpha':0},'core':core,'optimization_code_hash':c.code_hash(),'dataset_manifest_hash':c.file_hash(ds/'manifest.json'),'dataset':str(ds),'booster_file':None,'created_at':c.now(),'finalizer_sha256':c.file_hash(c.__file__)})
   current=datetime.now(timezone.utc);target={**fixtures[-1],'match_id':'SYNTHETIC-NOT-REAL','gender':'male','age':'senior','synthetic':True,'date':(current+timedelta(days=2)).date().isoformat(),'kickoff_at':(current+timedelta(days=2)).isoformat(),'cutoff_at':current.isoformat()};f=root/'fixture.json';f.write_text(json.dumps(target))
   a=c.calculate(artifact,target);b=c.calculate(artifact,target);self.assertEqual(a['matrix'],b['matrix'])
   for patch in ({'gender':'female'},{'gender':None},{'age':'U21'},{'age':None}):
    with self.subTest(classification=patch):
     with self.assertRaises(ValueError):c.calculate(artifact,{**target,**patch})
   with self.assertRaises(ValueError):c.calculate(artifact,{**target,'stage':'split_A'})
   with self.assertRaises(ValueError):c.calculate(artifact,{**target,'competition':'other'})
   output=root/'result.json';r=c.future(str(artifact),str(f),str(output),str(root/'synthetic-freezes'));self.assertIn('freeze_id',r['freeze']);self.assertFalse(r['active'])
   count=len(list((root/'synthetic-freezes').rglob('[0-9]*.json')))
   with self.assertRaises(FileExistsError):c.future(str(artifact),str(f),str(output),str(root/'synthetic-freezes'))
   self.assertEqual(count,len(list((root/'synthetic-freezes').rglob('[0-9]*.json'))))
   issued=json.loads(Path(r['freeze']['path']).read_text());self.assertTrue(issued['fixture']['synthetic']);self.assertEqual(issued['track'],'systematic_candidate')
   data=json.loads(artifact.read_text());data['cohort']='kor.2';artifact2=root/'k2.json';c.save(artifact2,data)
   with self.assertRaises(ValueError):c.calculate(artifact2,{**target,'cohort':'kor.2','competition':'kor.2'})
   (ds/'facts.jsonl').write_text('{}\n')
   with self.assertRaises(ValueError):c.calculate(artifact,target)
if __name__=='__main__':unittest.main()
