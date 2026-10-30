from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
import smtplib

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from runtime.automation.email_notify import build,send


class EmailTests(unittest.TestCase):
    def setUp(self):
        self.config={'enabled':True,'repository':'example/private-data','sender':'operator@qq.com','recipient':'recipient@qq.com'}
        self.event={'repository':{'full_name':'example/private-data'},'workflow_run':{
            'id':123,'run_attempt':1,'name':'WCPP private unattended research','head_branch':'main',
            'status':'completed','conclusion':'success','updated_at':'2026-10-30T03:06:00Z'}}
        self.record={'github_run_id':'123','github_run_attempt':'1','mode':'train','status':'COMPLETED',
            'finished_at':'2026-10-30T03:05:34Z','source_commit':'SYNTHETIC_ONLY',
            'result':{'accepted_rows':120,'quarantined':0,'cohorts':[{'cohort':'synthetic','status':'TRAINED_CANDIDATE','rows':120,
                'audit_n':20,'candidate_top1_hits':3,'baseline_top1_hits':2}]}}

    def render(self,record=None,event=None):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'runs';p.mkdir()
            if record is not None:(p/'run.json').write_text(json.dumps(record))
            return build(folder,event or self.event,self.config)

    def test_bjt_candidate_metrics_and_no_promotion_claim(self):
        n=self.render(self.record)
        self.assertIn('11:06',n['subject']);self.assertIn('候选3/20，基线2/20',n['body'])
        self.assertIn('不会自动替换',n['body']);self.assertNotIn('120%',n['body'])

    def test_successful_collection_goes_in_digest_not_each_email(self):
        r=deepcopy(self.record);r['mode']='collect'
        self.assertEqual(self.render(r)['status'],'SKIPPED_SUCCESSFUL_COLLECTION')

    def test_timeout_without_persisted_record_not_previous_success(self):
        e=deepcopy(self.event);e['workflow_run']['conclusion']='timed_out'
        r=deepcopy(self.record);r['github_run_id']='122'
        n=self.render(r,e)
        self.assertIn('任务异常',n['subject']);self.assertIn('没有取得与本次运行ID',n['body'])
        self.assertNotIn('合格训练记录：120',n['body'])

    def test_rerun_does_not_use_previous_attempt(self):
        e=deepcopy(self.event);e['workflow_run']['run_attempt']=2
        self.assertIn('没有取得',self.render(self.record,e)['body'])

    def test_repo_branch_and_workflow_must_match(self):
        for key,value in [('name','Other workflow'),('head_branch','fork-branch'),('status','in_progress')]:
            e=deepcopy(self.event);e['workflow_run'][key]=value
            with self.assertRaises(ValueError):self.render(self.record,e)

    def test_no_secret_never_opens_smtp(self):
        def forbidden(*args,**kwargs):raise AssertionError('network must not be used')
        n=self.render(self.record)
        self.assertEqual(send(n,self.config,password=None,smtp_factory=forbidden)['status'],'NOT_CONFIGURED_SMTP_PASSWORD')

    def test_only_intended_recipient_and_acceptance_not_delivery(self):
        calls={}
        class SMTP:
            def __init__(self,host,port,**kwargs):calls.update(host=host,port=port,tls=kwargs['context'])
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def login(self,user,password):calls.update(user=user,password=password)
            def send_message(self,message,**kwargs):calls.update(message=message,**kwargs);return {}
        result=send(self.render(self.record),self.config,password='SYNTHETIC_ONLY',smtp_factory=SMTP)
        self.assertEqual(calls['host'],'smtp.qq.com');self.assertEqual(calls['port'],465)
        self.assertEqual(calls['to_addrs'],['recipient@qq.com'])
        self.assertEqual(result['status'],'SMTP_ACCEPTED_INBOX_UNCONFIRMED')
        self.assertNotIn('SYNTHETIC_ONLY',calls['message'].as_string())

    def test_header_injection_blocked_and_disabled_skip(self):
        c=dict(self.config,recipient='recipient@qq.com\nBcc: injected@example.org')
        with self.assertRaises(ValueError):send(self.render(self.record),c,password='SYNTHETIC_ONLY')
        self.config['enabled']=False
        self.assertEqual(self.render()['status'],'SKIPPED_DISABLED')

    def test_authentication_failure_never_exposes_provider_text(self):
        def broken(*args,**kwargs):
            raise smtplib.SMTPAuthenticationError(535,b'SYNTHETIC_SECRET_DO_NOT_LOG')
        result=send(self.render(self.record),self.config,password='SYNTHETIC_ONLY',smtp_factory=broken)
        self.assertEqual(result,{'status':'FAILED_SMTP'})
        self.assertNotIn('SYNTHETIC_SECRET',json.dumps(result))


if __name__=='__main__':unittest.main()
