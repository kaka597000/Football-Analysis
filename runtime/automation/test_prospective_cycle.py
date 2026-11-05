import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime,timezone,timedelta

from runtime.automation import prospective_cycle as p


class PortableCycleTests(unittest.TestCase):
    def bundle(self,root,queue=None):
        root=Path(root)/'future'/'bundle';root.mkdir(parents=True);(root/'objects').mkdir()
        m={'schema':p.SCHEMA,'protocol':{'source_files_sha256':{}},'plans':{'plans':[]},'packages':[],
           'queue':queue or [],'objects':{},'aliases':{},'capture_assets':[],'protected_first_slots':[]}
        self.write(root,m);return root,m

    def write(self,root,m):
        m['manifest_sha256']=p.digest({k:v for k,v in m.items()if k!='manifest_sha256'})
        (root/'manifest.json').write_text(json.dumps(m))

    def plan(self,root,m,fixtures):
        body={'payload':{'fixtures_identity':fixtures,'eligible_match_ids':[f['match_id']for f in fixtures]}}
        record=dict(body,record_sha256=p.digest(body))
        data=json.dumps(record).encode();checksum=hashlib.sha256(data).hexdigest()
        (root/'objects'/checksum).write_bytes(data)
        alias='/workspace/WCPP/test-plan.json'
        m['objects'][checksum]={'path':'objects/'+checksum,'bytes':len(data),'sha256':checksum}
        m['aliases'][alias]=checksum
        receipt={'path':alias,'sha256':record['record_sha256']}
        m['plans']['plans']=[receipt];self.write(root,m)
        return receipt,record

    def fixture(self,mid,kickoff):
        return {'match_id':mid,'cohort':'eng.1','stage':'regular','home':'espn:1','away':'espn:2',
                'kickoff_at':kickoff,'gender':'men','age':'senior','identity_verified':True}

    def test_manifest_tampering_rejected_before_use(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,m=self.bundle(tmp);m['queue']=[{'match_id':'forged'}]
            (root/'manifest.json').write_text(json.dumps(m))
            with self.assertRaisesRegex(ValueError,'manifest'):p.check_bundle(root)

    def test_object_tampering_and_escape_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,m=self.bundle(tmp);body=b'original';h=hashlib.sha256(body).hexdigest()
            (root/'objects'/h).write_bytes(body)
            m['objects'][h]={'path':'objects/'+h,'bytes':len(body),'sha256':h};self.write(root,m)
            p.check_bundle(root)
            (root/'objects'/h).write_bytes(b'modified')
            with self.assertRaisesRegex(ValueError,'object'):p.check_bundle(root)
            m['objects'][h]['path']='../outside';self.write(root,m)
            with self.assertRaisesRegex(ValueError,'object'):p.check_bundle(root)

    def test_hydration_never_overwrites_conflicting_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,m=self.bundle(tmp);body=b'expected';h=hashlib.sha256(body).hexdigest()
            (root/'objects'/h).write_bytes(body);alias='/workspace/WCPP/synthetic'
            m['objects'][h]={'path':'objects/'+h,'bytes':len(body),'sha256':h};m['aliases'][alias]=h;m['capture_assets']=[alias];self.write(root,m)
            dest=Path(tmp)/'existing';dest.write_bytes(b'other')
            with patch.object(p,'_allowed_original',return_value=dest):
                with self.assertRaisesRegex(ValueError,'conflict'):p.hydrate(root)
            self.assertEqual(dest.read_bytes(),b'other')

    def test_hydration_checks_live_inference_code_not_archived_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,m=self.bundle(tmp);m['protocol']['source_files_sha256']={'/workspace/Football-Analysis/runtime/research_program/future_validation.py':'0'*64};self.write(root,m)
            with self.assertRaisesRegex(ValueError,'live executable'):p.hydrate(root)

    def test_entire_supplied_population_registers_even_when_capture_budget_zero(self):
        now=datetime.now(timezone.utc);future=(now+timedelta(days=2)).isoformat()
        queue=[{'match_id':f'espn:{i}','cohort':'eng.1','kickoff_at':future}for i in (1,2)]
        with tempfile.TemporaryDirectory() as tmp:
            root,m=self.bundle(tmp,queue)
            with patch.object(p,'hydrate'),patch.object(p,'_recover_registration',return_value=[]),patch.object(p.fv,'register',return_value=[])as reg:
                value=p.cycle(root,max_captures=0,max_result_requests=0,now=now)
            self.assertEqual(len(reg.call_args.args[1]),2)
            self.assertEqual(value['new_captures'],[])

    def test_first_slot_protection_excludes_recreated_forecast(self):
        now=datetime.now(timezone.utc);f={'match_id':'espn:1','cohort':'eng.1','kickoff_at':(now+timedelta(days=1)).isoformat()}
        with tempfile.TemporaryDirectory() as tmp:
            root,m=self.bundle(tmp,[f]);m['protected_first_slots']=[{'match_id':'espn:1'}];self.write(root,m)
            with patch.object(p.fv,'register')as register:
                v=p.cycle(root,max_result_requests=0,now=now)
            register.assert_not_called();self.assertEqual(v['new_captures'],[])

    def test_orphaned_registration_is_recovered_by_frozen_identity(self):
        with tempfile.TemporaryDirectory()as tmp:
            state=Path(tmp);folder=state/'freezes'/'one';folder.mkdir(parents=True)
            fixture=self.fixture('espn:1',(datetime.now(timezone.utc)+timedelta(days=1)).isoformat())
            keys=('match_id','cohort','stage','kickoff_at','home','away')
            entry={'candidate_artifact_sha256':'a','candidate_version':'c','current_artifact_sha256':'b','current_version':'m','stage':'regular'}
            payload=dict(entry,fixtures_identity=[{k:fixture[k]for k in keys}],protocol_sha256='locked')
            body={'payload':payload,'track':'research-preregistration','freeze_id':'plan',
                  'recorded_at':datetime.now(timezone.utc).isoformat(),'evaluation_slot':'NON_PREMATCH'}
            record=dict(body,record_sha256=p.digest(body));(folder/'000001.json').write_text(json.dumps(record))
            protocol={'protocol_sha256':'locked','cohorts':{'eng.1':entry}}
            recovered=p._recover_registration(state,protocol,[fixture])
            self.assertEqual(recovered[0]['sha256'],record['record_sha256'])
            protocol['cohorts']['eng.1']=dict(entry,current_artifact_sha256='tampered')
            with self.assertRaisesRegex(ValueError,'locked identity'):p._recover_registration(state,protocol,[fixture])

    def test_existing_package_recovers_after_kickoff_without_fetch_or_inference(self):
        now=datetime.now(timezone.utc)
        fixture=self.fixture('espn:1',(now-timedelta(hours=1)).isoformat())
        with tempfile.TemporaryDirectory()as tmp:
            root,m=self.bundle(tmp,[fixture]);receipt,plan=self.plan(root,m,[fixture])
            state=root.parent/'state';state.mkdir();(state/'capture-espn_1.json').write_text('{}')
            package={'match_id':fixture['match_id'],'plan_sha256':plan['record_sha256'],
                     'current_receipt':receipt,'candidate_receipt':receipt}
            pending={'completed':0,'pending_results':[fixture['match_id']],'missing_first_pairs':[]}
            with patch.object(p,'_recover_package',return_value=package),patch.object(p.fv,'capture')as capture,patch.object(p.fv,'review',return_value=pending),patch.object(p,'hydrate')as hydrate:
                fetch=lambda url:self.fail('recovery must not fetch new data')
                result=p.cycle(root,fetcher=fetch,now=now)
            capture.assert_not_called();hydrate.assert_not_called()
            self.assertEqual(result['captured_population'],1)
            self.assertEqual(len(p.read(state/'state.json')['packages']),1)

    def test_recovery_rejects_outfile_bound_to_different_plan(self):
        with tempfile.TemporaryDirectory()as tmp:
            root,m=self.bundle(tmp);out=root/'wrong.json'
            out.write_text(json.dumps({'match_id':'espn:1','plan_sha256':'other'}))
            with self.assertRaisesRegex(ValueError,'plan binding'):
                p._recover_package(root,m,out,{'record_sha256':'expected'},'espn:1',root)

    def test_network_failure_preserves_prior_successful_capture_checkpoint(self):
        now=datetime.now(timezone.utc);kickoff=(now+timedelta(days=1)).isoformat()
        fixtures=[self.fixture('espn:'+str(i),kickoff)for i in (1,2)]
        with tempfile.TemporaryDirectory()as tmp:
            root,m=self.bundle(tmp,fixtures);receipt,plan=self.plan(root,m,fixtures)
            package={'match_id':'espn:1','plan_sha256':plan['record_sha256'],
                     'current_receipt':receipt,'candidate_receipt':receipt}
            def fetch(url):
                if url.endswith('=2'):raise OSError('bounded source outage')
                return {'header':{'id':'1','competitions':[{'date':kickoff,'status':{'type':{'state':'pre'}},
                    'neutralSite':False,'competitors':[{'homeAway':s,'team':{'id':v}}for s,v in [('home','1'),('away','2')]]}]}}
            pending={'completed':0,'pending_results':['espn:1'],'missing_first_pairs':['espn:2']}
            with patch.object(p,'hydrate'),patch.object(p.fv,'capture',return_value=package),patch.object(p.fv,'review',return_value=pending):
                result=p.cycle(root,fetcher=fetch,now=now)
            persisted=p.read(root.parent/'state'/'state.json')
            self.assertEqual([v['match_id']for v in persisted['packages']],['espn:1'])
            self.assertEqual(result['failures'][0]['status'],'CAPTURE_BLOCKED')
            self.assertEqual(result['failures'][0]['error_type'],'OSError')

    def test_original_destination_allowlist(self):
        for path in ('/tmp/key','/etc/passwd','/workspace/WCPP/../elsewhere','relative'):
            with self.assertRaises(ValueError):p._allowed_original(path)

    def test_ucl_bundle_cannot_be_used_as_domestic_bundle(self):
        with tempfile.TemporaryDirectory()as tmp:
            root,m=self.bundle(tmp);m['schema']=p.UCL_SCHEMA;self.write(root,m)
            with self.assertRaisesRegex(ValueError,'manifest'):p.check_bundle(root)
            self.assertEqual(p.check_bundle(root,schema=p.UCL_SCHEMA)['schema'],p.UCL_SCHEMA)

    def test_ucl_review_retains_future_population_without_fetch_or_inference(self):
        from runtime.research_program import ucl_refresh_review as reviewer
        now=datetime.now(timezone.utc);f=self.fixture('espn:1',(now+timedelta(days=2)).isoformat())
        with tempfile.TemporaryDirectory()as tmp:
            root,m=self.bundle(tmp);receipt,plan=self.plan(root,m,[f])
            m.update(schema=p.UCL_SCHEMA,state_directory='ucl-state',plan=receipt,
                     packages=[{'match_id':'espn:1','baseline':receipt,'refreshed':receipt}]);self.write(root,m)
            pending={'population':1,'first_paired':1,'completed':0,'pending_results':['espn:1'],'missing_first_pairs':[]}
            with patch.object(p,'_hydrate_ucl'),patch.object(reviewer,'review',side_effect=lambda *a,**k:dict(pending)):
                value=p.ucl_cycle(root,fetcher=lambda u:self.fail('future match cannot fetch results'),now=now)
            self.assertEqual(value['pending_results'],['espn:1']);self.assertEqual(value['result_requests'],0)
            self.assertEqual(value['mode'],'REVIEW_ONLY_NO_NEW_INFERENCE')

    def test_ucl_result_identity_mismatch_stays_unsettled(self):
        from runtime.research_program import ucl_refresh_review as reviewer
        now=datetime.now(timezone.utc);f=self.fixture('espn:1',(now-timedelta(days=1)).isoformat())
        with tempfile.TemporaryDirectory()as tmp:
            root,m=self.bundle(tmp);receipt,plan=self.plan(root,m,[f])
            m.update(schema=p.UCL_SCHEMA,state_directory='ucl-state',plan=receipt,
                     packages=[{'match_id':'espn:1','baseline':receipt,'refreshed':receipt}]);self.write(root,m)
            body={'header':{'id':'2','competitions':[{'date':f['kickoff_at'],'status':{'type':{'name':'STATUS_FULL_TIME'}},
                'competitors':[{'homeAway':s,'team':{'id':v},'score':'0'}for s,v in [('home','1'),('away','2')]]}]}}
            pending={'population':1,'first_paired':1,'completed':0,'pending_results':['espn:1'],'missing_first_pairs':[]}
            with patch.object(p,'_hydrate_ucl'),patch.object(reviewer,'review',side_effect=lambda *a,**k:dict(pending)):
                value=p.ucl_cycle(root,fetcher=lambda u:body,now=now)
            self.assertEqual(value['completed'],0);self.assertEqual(value['failures'][0]['status'],'RESULT_NOT_CERTIFIED')
            self.assertFalse((root.parent/'ucl-state'/'results.json').exists())


if __name__=='__main__':unittest.main()
