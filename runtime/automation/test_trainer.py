"""Synthetic software tests, not model quality evidence."""
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from trainer import train


class TrainerTests(unittest.TestCase):
    def seed(self,root):
        (root/'seed').mkdir();(root/'config').mkdir()
        (root/'config/scopes.json').write_text(json.dumps([{'cohort':'SYNTHETIC','competition':'SYNTHETIC','stages':['regular']}]))
        rows=[]
        for i in range(160):
            rows.append({'match_id':str(i),'cohort':'SYNTHETIC','competition':'SYNTHETIC','stage':'regular',
                         'date':(datetime(2020,1,1)+timedelta(days=i)).date().isoformat(),
                         'home':'A' if i%2 else 'B','away':'B' if i%2 else 'A',
                         'home_goals':i%4,'away_goals':i%3,'neutral':None,
                         'score_basis':'regulation_90','dataset_kind':'SEED_RETROSPECTIVE',
                         'observed_at':'2021-01-01T00:00:00Z','source_url':'https://example.test/synthetic'})
        with gzip.open(root/'seed/training.json.gz','wt') as f:json.dump(rows,f)
        return rows

    def test_fit_no_new_data_skip_and_no_activation_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);self.seed(root);now=datetime(2022,1,1,tzinfo=timezone.utc)
            active=root/'active-model.json';active.write_text('{"sentinel":true}');before=active.read_bytes()
            a=train(root,now=now);row=a['cohorts'][0]
            self.assertEqual(row['status'],'TRAINED_CANDIDATE')
            artifact=root/row['artifact'];loaded=json.loads(artifact.read_text())
            self.assertEqual(loaded['core']['fit_n'],160)
            self.assertEqual(hashlib.sha256(artifact.read_bytes()).hexdigest(),row['artifact_sha256'])
            self.assertFalse(loaded['automatic_promotion'])
            b=train(root,now=now+timedelta(hours=1));self.assertEqual(b['cohorts'][0]['status'],'SKIPPED_UNCHANGED')
            self.assertEqual(active.read_bytes(),before)

    def test_future_known_time_duplicate_and_conflict_are_not_trained(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);rows=self.seed(root);now=datetime(2022,1,1,tzinfo=timezone.utc)
            conflict=dict(rows[0],home_goals=30,known_at='2021-01-02T00:00:00Z')
            duplicate=dict(rows[1],known_at='2021-01-02T00:00:00Z')
            future=dict(rows[2],match_id='future',date='2023-01-01',known_at='2021-01-02T00:00:00Z')
            unknown=dict(rows[3],match_id='unknown',date='2020-08-01');unknown.pop('observed_at');unknown.pop('dataset_kind')
            later=dict(rows[4],match_id='later',date='2020-08-02',known_at='2024-01-01T00:00:00Z')
            (root/'normalized-results.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in [conflict,duplicate,future,unknown,later]))
            result=train(root,now=now)
            self.assertEqual(result['accepted_rows'],159)
            self.assertEqual(len(result['excluded_by_time']),2)
            self.assertGreaterEqual(result['quarantined'],2)
            path=(root/result['cohorts'][0]['artifact']).parent/'manifest.json'
            ids={x['match_id'] for x in json.loads(path.read_text())['source_records']}
            self.assertNotIn('0',ids);self.assertNotIn('unknown',ids)

    def test_same_inputs_produce_same_numerical_refit_and_wrong_stage_excluded(self):
        with tempfile.TemporaryDirectory() as a,tempfile.TemporaryDirectory() as b:
            ra,rb=Path(a),Path(b);rows=self.seed(ra);self.seed(rb)
            bad=dict(rows[0],match_id='cup',stage='semifinals',date='2020-08-02',known_at='2021-01-01T00:00:00Z')
            (rb/'normalized-results.jsonl').write_text(json.dumps(bad)+'\n')
            now=datetime(2022,1,1,tzinfo=timezone.utc);x=train(ra,now=now);y=train(rb,now=now)
            ca=json.loads((ra/x['cohorts'][0]['artifact']).read_text())['core']
            cb=json.loads((rb/y['cohorts'][0]['artifact']).read_text())['core']
            self.assertEqual(ca,cb);self.assertEqual(y['accepted_rows'],160)

    def test_retrospective_seed_can_use_registered_artifact_provenance(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);rows=self.seed(root);sha='a'*64
            for row in rows:
                row.pop('source_url');row['source_artifact_sha256']=sha
            rows[-1]['source_artifact_sha256']='b'*64
            with gzip.open(root/'seed/training.json.gz','wt') as f:json.dump(rows,f)
            manifest={'created_at':'2021-01-01T00:00:00Z',
                      'seed_sha256':hashlib.sha256((root/'seed/training.json.gz').read_bytes()).hexdigest(),
                      'sources':[{'cohort':'SYNTHETIC','source_artifact_sha256':sha}]}
            (root/'seed/manifest.json').write_text(json.dumps(manifest))
            incremental=dict(rows[0],match_id='fake-live',date='2020-09-01',known_at='2021-01-01T00:00:00Z')
            (root/'normalized-results.jsonl').write_text(json.dumps(incremental)+'\n')
            result=train(root,now=datetime(2022,1,1,tzinfo=timezone.utc))
            self.assertEqual(result['accepted_rows'],159)
            self.assertEqual(result['quarantined'],2)
            manifest['seed_sha256']='c'*64;(root/'seed/manifest.json').write_text(json.dumps(manifest))
            with self.assertRaises(ValueError):train(root,now=datetime(2022,1,1,tzinfo=timezone.utc))


if __name__=='__main__':unittest.main()
