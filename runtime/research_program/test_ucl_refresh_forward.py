import copy
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import tempfile
import unittest
from research.analysis_freeze import digest
from research_program import ucl_refresh_forward as f

class RefreshForwardTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.freezes=self.root/'freezes';self.freezes.mkdir()
        def write(name,obj):
            p=self.root/name;p.write_text(json.dumps(obj));return p
        self.write=write
        current=write('active-current.json',{'id':'old-current'})
        test=write('active-test.json',{'id':'old-test'})
        registry=write('active.json',{'status':'ACTIVE_RESEARCH','models':{f.COHORT:{'artifact_path':str(current),'artifact_sha256':f.sha(current),'test_artifact_path':str(test),'test_artifact_sha256':f.sha(test)}}})
        lineage={'id':'FIXED-TEST','active':False,'status':'INACTIVE_AWAITING_PROSPECTIVE_VALIDATION','historical_asof_certified':False}
        candidate={'cohort':f.COHORT,'built_at':(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat(),'refresh_experiment':lineage}
        a=write('refresh-current.json',candidate);b=write('refresh-test.json',candidate)
        self.protocol=f.lock(registry,a,b,self.root/'protocol.json')
        now=datetime.now(timezone.utc)
        self.fixture={'match_id':'SYNTHETIC-NOT-REAL','cohort':f.COHORT,'competition':'uefa.champions','stage':f.STAGE,'phase':'PreMatch','age':'senior','gender':'men','identity_verified':True,'neutral':False,'home':'espn:1','away':'espn:2','kickoff_at':(now+timedelta(days=1)).isoformat(),'cutoff_at':now.isoformat()}
        receipt=f.register(self.protocol,[self.fixture],self.root/'plan-receipt.json',self.freezes)
        self.plan=f.read(receipt['path']);self.fixture['cutoff_at']=datetime.now(timezone.utc).isoformat()
    def runner(self,fixture,registry):
        route=f.read(registry)['models'][f.COHORT];matrix=[[.4,.3],[.2,.1]]
        p={'version':'SYNTHETIC','input':fixture,'matrix':matrix,'matrix_hash':digest(matrix),'primary':'0-0'}
        return {'current_model':copy.deepcopy(p),'test_model':copy.deepcopy(p),'execution_audit':{'current':{'artifact_sha256':route['artifact_sha256']},'test':{'artifact_sha256':route['test_artifact_sha256']}}}
    def test_capture_same_fixture_separate_tracks_and_pending(self):
        out=self.root/'packages'/'pair.json';package=f.capture(self.protocol,self.plan,self.fixture,out,self.freezes,self.runner)
        self.assertNotEqual(package['baseline']['path'],package['refreshed']['path'])
        status=f.status(self.plan,[package]);self.assertEqual(status['first_paired'],1);self.assertEqual(status['completed'],0)
        self.assertEqual(status['pending_results'],[self.fixture['match_id']])
        # Resume reuses the frozen journal, never runs a later best-of-model inference.
        recovered=f.capture(self.protocol,self.plan,self.fixture,out,self.freezes,lambda *_:self.fail('recomputed'),True)
        self.assertEqual(recovered,package)
    def test_rejects_historical_target_and_labeled_future(self):
        bad=copy.deepcopy(self.fixture);bad['kickoff_at']=(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()
        with self.assertRaises(ValueError):f.register(self.protocol,[bad],self.root/'historical.json',self.freezes)
        bad=copy.deepcopy(self.fixture);bad['home_goals']=0
        with self.assertRaises(ValueError):f.capture(self.protocol,self.plan,bad,self.root/'labeled.json',self.freezes,self.runner)
    def test_rejects_changed_locked_asset_and_backdated_cutoff(self):
        bad=copy.deepcopy(self.fixture);bad['cutoff_at']=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()
        with self.assertRaises(ValueError):f.capture(self.protocol,self.plan,bad,self.root/'backdated.json',self.freezes,self.runner)
        Path(self.protocol['inactive_registry']).write_text('{}')
        with self.assertRaises(ValueError):f.capture(self.protocol,self.plan,self.fixture,self.root/'changed.json',self.freezes,self.runner)
    def test_existing_first_pair_and_tampered_resume_rejected(self):
        out=self.root/'first.json';f.capture(self.protocol,self.plan,self.fixture,out,self.freezes,self.runner)
        with self.assertRaises(ValueError):f.capture(self.protocol,self.plan,self.fixture,self.root/'duplicate.json',self.freezes,self.runner)
        journal=out.with_name(out.name+'.capture.json');state=f.read(journal);state['fixture']['home']='espn:999';journal.write_text(json.dumps(state))
        with self.assertRaises(ValueError):f.capture(self.protocol,self.plan,self.fixture,out,self.freezes,self.runner,True)

if __name__=='__main__':unittest.main()
