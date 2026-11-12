import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from research_program.retained_j1 import load_manifest,fixture_for


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.current=self.root/'current.json';self.current.write_text('{}')
        self.manifest={'schema':'WCPP-RETAINED-J1-1','current_artifact':str(self.current),'current_sha256':sha(self.current),'team_mapping':{}}
        for tid in ('1','2'):
            proofs=[]
            for event in ('a','b'):
                p=self.root/(tid+event+'.json');p.write_text(json.dumps({'data':{'header':{'id':event,'competitions':[{'competitors':[{'homeAway':'home','team':{'id':tid}}]}]}}}))
                proofs.append({'source_file':str(p),'source_sha256':sha(p),'event_id':event,'side':'home'})
            self.manifest['team_mapping']['espn:'+tid]={'current_id':tid,'test_id':'espn:'+tid,'proof':proofs}
        self.path=self.root/'manifest.json';self.path.write_text(json.dumps(self.manifest))
        self.fixture={'home':'espn:1','away':'espn:2','cohort':'jpn.1','competition':'jpn.1'}

    def test_current_does_not_depend_on_test_source_or_test_artifact(self):
        m=load_manifest(self.path,'current');self.assertEqual(fixture_for(m,self.fixture,'current')['home'],'1')

    def test_no_string_prefix_guess_for_unknown_team(self):
        f=dict(self.fixture,home='espn:3')
        with self.assertRaises(ValueError):fixture_for(self.manifest,f,'current')

    def test_mapping_keeps_input_unchanged(self):
        before=copy.deepcopy(self.fixture);f=fixture_for(self.manifest,self.fixture,'test')
        self.assertEqual(f,self.fixture);self.assertEqual(before,self.fixture)

    def test_tampered_evidence_and_duplicate_proof_rejected(self):
        entry=self.manifest['team_mapping']['espn:1'];entry['proof'][1]=copy.deepcopy(entry['proof'][0])
        with self.assertRaises(ValueError):fixture_for(self.manifest,self.fixture,'current')
        Path(entry['proof'][0]['source_file']).write_text('{}')
        with self.assertRaises(ValueError):fixture_for(self.manifest,self.fixture,'current')

    def test_wrong_artifact_checksum_rejected(self):
        self.current.write_text('{"changed":true}')
        with self.assertRaises(ValueError):load_manifest(self.path,'current')

    def test_other_league_and_conflicting_map_rejected(self):
        with self.assertRaises(ValueError):fixture_for(self.manifest,dict(self.fixture,competition='kor.1'),'current')
        self.manifest['team_mapping']['espn:1']['current_id']='2'
        with self.assertRaises(ValueError):fixture_for(self.manifest,self.fixture,'current')

if __name__=='__main__':unittest.main()
