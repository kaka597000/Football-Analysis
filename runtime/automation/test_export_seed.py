import hashlib
import gzip
import json
from pathlib import Path
import tempfile
import unittest
from export_seed import export


class SeedTests(unittest.TestCase):
    def test_identity_time_neutral_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'runtime').mkdir();artifact=root/'artifact.json'
            artifact.write_text(json.dumps({'history':[
                {'match_id':'old','date':'2020-01-01','home':'1','away':'2','home_goals':1,'away_goals':0,'stage':'regular','neutral':0,'source_url':'https://site.api.espn.com/test'},
                {'match_id':'future','date':'2099-01-01','home':'1','away':'2','home_goals':9,'away_goals':9,'stage':'regular'}]}))
            h=hashlib.sha256(artifact.read_bytes()).hexdigest()
            registry={'version':'SYNTHETIC_ONLY','models':{'eng.1':{'artifact_path':str(artifact),'artifact_sha256':h}}}
            (root/'runtime/active-model.json').write_text(json.dumps(registry))
            out=root/'private-state';result=export(root,out)
            rows=json.loads(gzip.decompress((out/'seed/training.json.gz').read_bytes()))
            self.assertEqual(result['rows'],1);self.assertIs(rows[0]['neutral'],False)
            self.assertEqual(rows[0]['home'],'espn:1');self.assertEqual(rows[0]['dataset_kind'],'SEED_RETROSPECTIVE')
            self.assertIsNone(rows[0]['first_known_at'])
            with self.assertRaises(FileExistsError):export(root,out)
            registry['models']['eng.1']['artifact_sha256']='bad'
            (root/'runtime/active-model.json').write_text(json.dumps(registry))
            with self.assertRaises(ValueError):export(root,root/'other')


if __name__=='__main__':unittest.main()
