"""All callers below are synthetic software mocks, never football evidence."""
import copy
from datetime import datetime, timedelta, timezone
import unittest

from evidence_agents import ROLES, run


class AgentTests(unittest.TestCase):
    def packet(self):
        now = datetime.now(timezone.utc)
        return {'match_id': 'SYNTHETIC_ONLY', 'as_of': now.isoformat(),
                'kickoff_at': (now+timedelta(hours=1)).isoformat(),
                'evidence': [{'id': 'fixture-source', 'source_url': 'https://example.test/software-only',
                              'known_at': (now-timedelta(minutes=1)).isoformat(),
                              'captured_at': now.isoformat(),
                              'fields': {'home_team': 'SYNTHETIC HOME', 'rest_days': 3}}]}

    def record(self, req):
        return {k: req[k] for k in ('role', 'match_id', 'inputs_hash', 'as_of')} | {
            'status': 'EVIDENCE_REVIEWED',
            'observations': [{'evidence_id': 'fixture-source', 'field': 'home_team',
                              'interpretation': '球队身份来自本次固定证据'}],
            'objections': [], 'corrections': []}

    def test_no_caller_is_not_run(self):
        out = run(self.packet())
        self.assertEqual(out['status'], 'NOT_RUN_NO_CALLER')
        self.assertEqual(out['calls_used'], 0)
        self.assertFalse(out['numeric_prediction_modified'])

    def test_independent_first_round_hash_and_packet_immutability(self):
        packet = self.packet(); before = copy.deepcopy(packet); seen = []
        def caller(req):
            seen.append(copy.deepcopy(req))
            result = self.record(req)
            req['packet']['evidence'][0]['fields']['home_team'] = 'MUTATED BY TEST'
            return result
        out = run(packet, caller)
        self.assertEqual(out['status'], 'EVIDENCE_REVIEW_COMPLETE_RESEARCH_ONLY')
        self.assertEqual(packet, before)
        self.assertEqual(len({r['inputs_hash'] for r in seen}), 1)
        self.assertTrue(all(not r['reports'] for r in seen[:-1]))
        self.assertEqual(len(seen[-1]['reports']), 4)
        self.assertEqual([r['role'] for r in seen], list(ROLES))

    def test_future_evidence_rejected_before_any_call(self):
        packet = self.packet(); packet['evidence'][0]['known_at'] = packet['kickoff_at']
        with self.assertRaises(ValueError):
            run(packet, lambda r: self.fail('caller must not run'))

    def test_invalid_fields_numeric_claim_fake_citation_and_hash_rejected(self):
        def bad_numeric(r):
            result = self.record(r)
            result['observations'][0]['interpretation'] = '主胜概率80%并选择比分2-1'
            return result
        def bad_field(r):
            return self.record(r) | {'primary': '2-1'}
        def fake_citation(r):
            result = self.record(r); result['observations'][0]['evidence_id'] = 'NOT_CAPTURED'
            return result
        def fake_hash(r):
            return self.record(r) | {'inputs_hash': 'FAKE'}
        for caller in (bad_numeric, bad_field, fake_citation, fake_hash):
            with self.subTest(caller=caller.__name__):
                out = run(self.packet(), caller)
                self.assertEqual(out['status'], 'INCOMPLETE_CALL_OR_SCHEMA')
                self.assertEqual(out['records'], [])
                self.assertTrue(all(c['error_type']=='ValueError' for c in out['calls']))

    def test_two_round_correction_is_bound_to_existing_objection(self):
        seen=[]
        def caller(req):
            seen.append(copy.deepcopy(req)); result=self.record(req)
            if req['role']=='auditor' and req['round']==1:
                result['status']='OBJECTION'
                result['objections']=[{'id':'identity-evidence-objection', 'target_role':'identity_data',
                    'evidence_ids':['fixture-source'], 'field':'home_team',
                    'reason':'请核对该身份字段的来源说明', 'needs_correction':True}]
            if req['role']=='identity_data' and req['round']==2:
                result['status']='CORRECTION'
                result['corrections']=[{'objection_id':'identity-evidence-objection',
                    'evidence_id':'fixture-source','field':'home_team',
                    'interpretation':'已按同一证据字段修正说明'}]
            return result
        out=run(self.packet(), caller)
        self.assertEqual(out['calls_used'],7)
        self.assertEqual(out['status'],'EVIDENCE_REVIEW_COMPLETE_RESEARCH_ONLY')
        self.assertEqual([(x['role'],x['round']) for x in seen[-2:]], [('identity_data',2),('auditor',2)])
        limited=run(self.packet(),caller,call_budget=5)
        self.assertEqual(limited['status'],'INCOMPLETE_BUDGET')
        self.assertEqual(limited['calls_used'],5)

    def test_unsolicited_correction_and_caller_errors_do_not_approve(self):
        def unsolicited(req):
            result=self.record(req)
            result['corrections']=[{'objection_id':'invented','evidence_id':'fixture-source',
                'field':'home_team','interpretation':'无原异议的擅自改写'}]
            return result
        self.assertEqual(run(self.packet(),unsolicited)['status'],'INCOMPLETE_CALL_OR_SCHEMA')
        def broken(req): raise RuntimeError('must not echo arbitrary caller exception text')
        out=run(self.packet(),broken)
        self.assertNotIn('must not echo',str(out))
        self.assertFalse(out['promotion_approved'])

    def test_limits_and_secret_packet_fields(self):
        for kwargs in ({'max_rounds':3},{'max_rounds':True},{'call_budget':11},{'call_budget':-1}):
            with self.assertRaises(ValueError):run(self.packet(),self.record,**kwargs)
        packet=self.packet();packet['evidence'][0]['fields']['api_key']='TEST_ONLY'
        with self.assertRaises(ValueError):run(packet)

    def test_auditor_cannot_clear_objection_without_accepted_correction(self):
        def caller(req):
            result=self.record(req)
            if req['role']=='auditor' and req['round']==1:
                result['objections']=[{'id':'verify-identity','target_role':'identity_data',
                    'evidence_ids':['fixture-source'],'field':'home_team',
                    'reason':'需要引用原身份字段进行修正','needs_correction':True}]
            if req['role']=='identity_data' and req['round']==2:
                # A fabricated reference must fail; auditor's later empty list
                # must not erase the unresolved original objection.
                result['corrections']=[{'objection_id':'invented','evidence_id':'fixture-source',
                    'field':'home_team','interpretation':'假装完成修正'}]
            return result
        out=run(self.packet(),caller)
        self.assertEqual(out['status'],'INCOMPLETE_CALL_OR_SCHEMA')
        self.assertEqual([o['id'] for o in out['unresolved_objections']],['verify-identity'])
        packet=self.packet();packet['evidence'][0]['source_url']='https://example.test/?apikey=TEST_ONLY'
        with self.assertRaises(ValueError):run(packet)


if __name__=='__main__':unittest.main()
