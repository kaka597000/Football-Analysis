"""Synthetic ESPN-shaped regressions; no network and no real-data mutation."""
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from trainer import _load


class RawEvidenceTests(unittest.TestCase):
    def setup_state(self, root, alter=None, bad_hash=False, reference=True):
        (root/'config').mkdir();(root/'raw').mkdir()
        (root/'config/scopes.json').write_text(json.dumps([
            {'cohort':'eng.1','competition':'eng.1','stages':['regular']}]))
        row={'match_id':'espn:123','cohort':'eng.1','competition':'eng.1','stage':'regular',
             'date':'2024-01-01','kickoff_at':'2024-01-01T15:00:00Z',
             'home':'espn:1','away':'espn:2','home_goals':2,'away_goals':1,
             'neutral':False,'score_basis':'regulation_90','known_at':'2024-01-02T00:00:00Z',
             'source_url':'https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/summary?event=123'}
        raw={'format':{'regulation':{'periods':2}},'header':{'id':'123','competitions':[
            {'date':row['kickoff_at'],'status':{'type':{'completed':True,'name':'STATUS_FULL_TIME'}},
             'competitors':[{'homeAway':'home','team':{'id':'1'},'score':'2',
                             'linescores':[{'displayValue':'1'},{'displayValue':'1'}]},
                            {'homeAway':'away','team':{'id':'2'},'score':'1',
                             'linescores':[{'value':0.0},{'value':1.0}]}]}]}}
        if alter:alter(raw,row)
        data=json.dumps(raw).encode();sha=hashlib.sha256(data).hexdigest()
        (root/'raw/result.json.gz').write_bytes(gzip.compress(data))
        if reference:row['raw_reference']={'path':'raw/result.json.gz','sha256':'0'*64 if bad_hash else sha}
        (root/'normalized-results.jsonl').write_text(json.dumps(row)+'\n')
        return row

    def check(self, alter=None, bad_hash=False, reference=True):
        with tempfile.TemporaryDirectory()as d:
            root=Path(d);self.setup_state(root,alter,bad_hash,reference)
            return _load(root,datetime(2025,1,1,tzinfo=timezone.utc))

    def test_valid_bytes_and_periods_accepted(self):
        _,rows,q,_=self.check();self.assertEqual(len(rows),1);self.assertEqual(q,[])
        self.assertEqual(rows[0]['source_evidence_status'],'VERIFIED_RAW_REGULATION_90')

    def test_bad_raw_hash_is_quarantined(self):
        _,rows,q,_=self.check(bad_hash=True);self.assertEqual(rows,[])
        self.assertEqual(q[0]['reason'],'RAW_SOURCE_CHECKSUM_MISMATCH')

    def test_corrupt_compression_is_quarantined_without_aborting_batch(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.setup_state(root)
            path=root/'raw/result.json.gz'
            damaged=bytearray(path.read_bytes());damaged[10]=7  # Invalid DEFLATE block type.
            path.write_bytes(damaged)
            _,rows,q,_=_load(root,datetime(2025,1,1,tzinfo=timezone.utc))
        self.assertEqual(rows,[])
        self.assertEqual(q[0]['reason'],'RAW_SOURCE_UNREADABLE_OR_INVALID')

    def test_oversized_raw_score_is_quarantined_without_aborting_batch(self):
        def alter(raw,row):raw['header']['competitions'][0]['competitors'][0]['linescores'][0]={'value':10**400}
        _,rows,q,_=self.check(alter);self.assertEqual(rows,[])
        self.assertEqual(q[0]['reason'],'RAW_SOURCE_UNREADABLE_OR_INVALID')

    def test_periods_conflicting_with_training_label_are_quarantined(self):
        def alter(raw,row):raw['header']['competitions'][0]['competitors'][0]['linescores']=[{'value':0},{'value':0}]
        _,rows,q,_=self.check(alter);self.assertEqual(rows,[])
        self.assertEqual(q[0]['reason'],'RAW_SOURCE_REGULATION_LABEL_CONFLICT')

    def test_ft_final_conflicting_with_periods_is_quarantined(self):
        def alter(raw,row):raw['header']['competitions'][0]['competitors'][0]['score']='3'
        _,rows,q,_=self.check(alter);self.assertEqual(rows,[])
        self.assertEqual(q[0]['reason'],'RAW_SOURCE_FT_PERIOD_CONFLICT')

    def test_identity_mismatch_is_quarantined(self):
        for which in ['event','team','league']:
            def alter(raw,row):
                if which=='event':raw['header']['id']='999'
                elif which=='team':raw['header']['competitions'][0]['competitors'][0]['team']['id']='999'
                else:raw['header']['league']={'slug':'wrong.league'}
            _,rows,q,_=self.check(alter);self.assertEqual(rows,[])
            self.assertIn('IDENTITY_MISMATCH',q[0]['reason'])

    def test_legacy_without_raw_reference_is_explicitly_unverified(self):
        _,rows,q,_=self.check(reference=False);self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['source_evidence_status'],'LEGACY_NO_RAW_REFERENCE')

    def test_extra_time_final_is_not_substituted_for_regulation(self):
        def alter(raw,row):
            match=raw['header']['competitions'][0];match['status']['type']['name']='STATUS_FINAL_AET'
            for team in match['competitors']:
                team['score']=str(int(team['score'])+1);team['linescores'].append({'value':1})
        _,rows,q,_=self.check(alter);self.assertEqual(len(rows),1);self.assertEqual(q,[])

if __name__=='__main__':unittest.main()
