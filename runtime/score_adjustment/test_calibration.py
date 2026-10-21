import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).parent))
from calibration import apply, date_split, digest, load, save, train, validate_matrix

class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.binding={'model':'M2-h365-r30','cohorts':['test'],'source_protocol_sha256':['verified']}
        self.identity={'model':'M2-h365-r30','cohort':'test','source_protocol_sha256':'verified','feature_group':'core','half_life':365,'ridge':30,'alpha':0.0}
        self.m=np.array([[.10,.10,.05],[.10,.25,.05],[.05,.10,.20]])
    def fit(self,candidate='C2'):
        return train([self.m]*6,[[1,1],[2,2],[2,1],[1,1],[0,1],[1,0]],candidate=candidate,base_binding=self.binding)
    def test_save_reload_exact(self):
        a=self.fit()
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'artifact.json';save(a,p)
            np.testing.assert_array_equal(apply(a,self.m,base_identity=self.identity),apply(load(p),self.m,base_identity=self.identity))
            with self.assertRaises(FileExistsError):save(a,p)
    def test_zero_support_input_untouched(self):
        m=self.m.copy();m[0,0]=0;m/=m.sum();original=m.copy();a=self.fit()
        q=apply(a,m,base_identity=self.identity)
        np.testing.assert_array_equal(m,original)
        self.assertEqual(q[0,0],0);self.assertAlmostEqual(q.sum(),1,14);self.assertTrue((q>=0).all())
    def test_identity_exact(self):
        a=self.fit('C0');np.testing.assert_array_equal(apply(a,self.m,base_identity=self.identity),self.m)
    def test_unknown_base_rejected(self):
        a=self.fit()
        for field,value in [('model','current'),('cohort','unknown'),('ridge',10),('alpha',.5),('source_protocol_sha256','other')]:
            identity=dict(self.identity);identity[field]=value
            with self.assertRaises(ValueError):apply(a,self.m,base_identity=identity)
        with self.assertRaises(ValueError):apply(a,self.m,base_identity={})
    def test_missing_corrupt_artifact(self):
        with self.assertRaises(FileNotFoundError):load('/tmp/does-not-exist-score-calibration-742618.json')
        a=self.fit();a['theta'][0]+=1
        with self.assertRaises(ValueError):apply(a,self.m,base_identity=self.identity)
    def test_invalid_matrices(self):
        for m in ([[1,0,0],[0,0,0]],[[1.1,-.1],[0,0]],[[float('nan'),0],[0,1]],[[.1,0],[0,.1]]):
            with self.assertRaises(ValueError):validate_matrix(m)
    def test_day_grouped_chronology(self):
        rows=[{'date':f'2025-01-{day:02d}','cohort':cohort} for day in range(1,11) for cohort in ['a','b']]
        split,dates=date_split(rows)
        self.assertEqual([len(split[k]) for k in ['train','select','final']],[12,4,4])
        self.assertLess(max(dates['train']),min(dates['select']));self.assertLess(max(dates['select']),min(dates['final']))
        self.assertEqual(len(set(sum(dates.values(),[]))),10)
    def test_target_not_apply_feature(self):
        a=self.fit();payload={'matrix':self.m.copy(),'target':[0,0]}
        q=apply(a,payload['matrix'],base_identity=self.identity);payload['target']=[9,9]
        np.testing.assert_array_equal(q,apply(a,payload['matrix'],base_identity=self.identity))
        with self.assertRaises(TypeError):apply(a,payload['matrix'],base_identity=self.identity,label=[1,1])
    def test_variable_support_padding(self):
        small=np.array([[.25,.25],[.25,.25]])
        a=train([small,self.m],[[1,1],[2,2]],candidate='C1',base_binding=self.binding)
        self.assertEqual(apply(a,small,base_identity=self.identity).shape,(2,2))
    def test_cli_preserves_primary(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);artifact=root/'model.json';source=root/'input.json';output=root/'out.json'
            save(self.fit(),artifact)
            payload={'matrix':self.m.tolist(),'base_identity':self.identity,'primary':[0,2]};source.write_text(json.dumps(payload))
            subprocess.run([sys.executable,str(Path(__file__).parent/'runner.py'),'apply','--artifact',str(artifact),'--input',str(source),'--out',str(output)],check=True,capture_output=True)
            result=json.loads(output.read_text());self.assertEqual(result['comprehensive_primary'],[0,2]);self.assertEqual(json.loads(source.read_text()),payload)
    def test_scale_can_decrease(self):
        a=train([self.m]*4,[[0,0]]*4,candidate='C1',base_binding=self.binding)
        self.assertTrue(all(t<0 for t in a['theta'][:2]));self.assertGreaterEqual(min(np.exp(a['theta'][:2])),2/3)

if __name__=='__main__':unittest.main()
