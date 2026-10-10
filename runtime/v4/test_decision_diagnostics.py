import copy
import hashlib
import json
import math
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from main_model import digest, probabilities
from decision_audit import assess_decision, api_audit, CATEGORIES
from diagnostics import diagnose, reliability
from provider_feed import fetch_fixtures


def fixture():
    now = datetime.now(timezone.utc)
    return {'match_id': 'audit', 'cohort': 'eng.1', 'cutoff_at': now.isoformat(),
            'kickoff_at': (now+timedelta(hours=3)).isoformat()}


def call(f, endpoint='/leagues/123/fixtures'):
    return {'provider': '5DollarFootballAPI', 'url': 'https://api.5dollarfootballapi.com/v1'+endpoint,
            'query_time': f['cutoff_at'], 'match_id': f['match_id'], 'http_status': 200,
            'api_success': True, 'retrieved_fields': ['teams'], 'analysis_fields': ['teams'],
            'model_input_fields': [], 'fixture_mapping_verified': True}


def sample():
    kickoff = datetime.now(timezone.utc)-timedelta(days=3)
    m = [[.2,.3],[.4,.1]]
    f = {'version':'TEST_ONLY', 'recipe':'synthetic', 'freeze_status':'DRAFT_NOT_FROZEN',
         'generated_at': (kickoff-timedelta(hours=1)).isoformat(),
         'input': {'match_id':'sample', 'cohort':'eng.1', 'kickoff_at':kickoff.isoformat(),
                   'cutoff_at':(kickoff-timedelta(hours=2)).isoformat()},
         'matrix':m, 'matrix_hash':digest(m), 'primary':'1-0', 'primary_probability':.4,
         'wdl':dict(zip(('主胜','平局','客胜'),map(float,probabilities(m)))), 'wdl_reference':'主胜',
         'total': None}
    r = {'match_id':'sample', 'home_goals':0, 'away_goals':1, 'score_basis':'regulation_90',
         'source_url':'https://example.org/result', 'observed_at':(kickoff+timedelta(hours=3)).isoformat()}
    return f,r


class DecisionTests(unittest.TestCase):
    def test_status_does_not_count_as_match_feed(self):
        f=fixture();f['source_calls']=[call(f,'/status')]
        self.assertFalse(api_audit(f)['attempted_fixture_data'])
        self.assertFalse(api_audit(f)['compliant'])
        f['source_calls'].append(call(f))
        self.assertTrue(api_audit(f)['compliant'])
        self.assertFalse(api_audit(f)['execution_verified_by_audit'])

    def test_failure_requires_disclosed_fallback_and_unretrieved_inputs_rejected(self):
        f=fixture();c=call(f);c.update(http_status=403,api_success=False,retrieved_fields=[],analysis_fields=[])
        f['source_calls']=[c]
        self.assertFalse(api_audit(f)['compliant'])
        f['source_fallback']={'reason':'permission denied','source_urls':['https://example.org/official']}
        self.assertTrue(api_audit(f)['compliant']);self.assertFalse(api_audit(f)['data_available'])
        c['model_input_fields']=['xg'];self.assertFalse(api_audit(f)['compliant'])

    def test_post_cutoff_and_credential_urls_rejected_without_echo(self):
        f=fixture();c=call(f);c['query_time']=f['kickoff_at'];f['source_calls']=[c]
        self.assertFalse(api_audit(f)['compliant'])
        c['query_time']=f['cutoff_at'];c['url']+='?api_key=secret-test-value'
        out=api_audit(f);self.assertFalse(out['compliant']);self.assertNotIn('secret-test-value',json.dumps(out))

    def test_documentation_does_not_enable_numeric_fusion(self):
        f=fixture();f['source_calls']=[call(f)];f['decision_evidence']={}
        for side in ('home','away'):
            f['decision_evidence'][side]={}
            for category in CATEGORIES:
                b={'status':'documented','summary':'Sourced test statement',
                   'evidence':[{'source_url':'https://example.org/evidence','source_tier':'L1',
                                'query_time':f['cutoff_at'],'supports_field':[category]}]}
                if category=='paths':b['counterargument']='Opponent may change strategy'
                if category=='lineup':b.update(official_confirmed=True,starters=[str(i) for i in range(11)])
                f['decision_evidence'][side][category]=b
        out=assess_decision(f);self.assertEqual(out['documented_blocks'],18)
        self.assertTrue(out['evidence_documentation_complete']);self.assertFalse(out['comprehensive_primary_ready'])
        f['decision_evidence']['home']['lineup']['starters']=['same']*11
        f['decision_evidence']['away']['process']['xg_origin']='narrative_inference'
        f['decision_evidence']['home']['paths'].pop('counterargument')
        out=assess_decision(f);self.assertEqual(out['documented_blocks'],15)


class DiagnosticTests(unittest.TestCase):
    def test_calibration_and_residuals_known_answer(self):
        f,r=sample();out=diagnose([f],[r],3)
        s=out['overall'];self.assertEqual(s['top1'],0)
        self.assertAlmostEqual(s['primary_reliability']['brier'],.16)
        self.assertAlmostEqual(s['score_log_loss'],-math.log(.3))
        self.assertAlmostEqual(s['wdl_brier'],.4**2+.3**2+.7**2)
        self.assertAlmostEqual(s['mean_goal_residuals']['home_goal_residual'],-.5)
        self.assertAlmostEqual(s['mean_goal_residuals']['away_goal_residual'],.6)
        self.assertEqual(out['saved_coverage'],1/3);self.assertEqual(out['formal_kpi_added'],0)
        self.assertEqual(reliability([(1.,1),(0.,0)])['ece'],0)

    def test_pending_duplicate_non90_and_tampered_matrix(self):
        f,r=sample();self.assertEqual(diagnose([f],[],1)['pending'],['sample'])
        with self.assertRaises(ValueError):diagnose([f,f],[r],2)
        r['score_basis']='after_extra_time'
        with self.assertRaises(ValueError):diagnose([f],[r],1)
        r['score_basis']='regulation_90';f['matrix'][0][0]=.21
        with self.assertRaises(ValueError):diagnose([f],[r],1)

    def test_actual_quarter_line_half_win_is_not_full_win(self):
        f,r=sample();r.update(home_goals=1,away_goals=1)
        f['total']={'quote':{'verified':True,'match_id':'sample','bookmaker':'test',
                            'source_url':'https://example.org/market','query_time':f['input']['cutoff_at'],
                            'line':2.25,'market':'asian_total'},'reference_direction':'under'}
        out=diagnose([f],[r],1);s=out['overall']['total']
        self.assertEqual(s['settlements']['half_win'],1);self.assertEqual(s['full_win_frequency_all_quoted'],0)
        self.assertAlmostEqual(s['settlement_brier'],.9**2+.9**2)

    def test_outside_matrix_and_future_generated_rejected(self):
        f,r=sample();r.update(home_goals=8,away_goals=0)
        out=diagnose([f],[r],1);self.assertEqual(out['overall']['actual_outside_matrix'],1)
        f['generated_at']=f['input']['kickoff_at']
        with self.assertRaises(ValueError):diagnose([f],[r],1)


class FeedTests(unittest.TestCase):
    def test_missing_key_does_not_make_network_request(self):
        with patch.dict(os.environ,{},clear=True):
            out=fetch_fixtures(123,1,2,opener=lambda *a,**k:self.fail('network called'))
        self.assertEqual(out['calls'][0]['error_code'],'ENV_BINDING_MISSING')

    def test_status_then_feed_and_secret_redaction(self):
        urls=[];payloads=[{'success':1,'data':{'key':'TEST_KEY','plan':'test'}},
                         {'success':1,'data':[{'id':1,'name':'echo TEST_KEY','token':'private'}],
                          'pagination':{'has_more':False}}]
        class Response:
            status=200
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return json.dumps(payloads.pop(0)).encode()
        def opener(request,timeout):
            urls.append(request.full_url);self.assertEqual(request.get_header('Authorization'),'Bearer TEST_KEY')
            return Response()
        with patch.dict(os.environ,{'WCPP_5DOLLAR_API_KEY':'TEST_KEY'}):
            out=fetch_fixtures(123,1,2,opener=opener)
        self.assertEqual(urls[0],'https://api.5dollarfootballapi.com/v1/status')
        self.assertIn('/leagues/123/fixtures?',urls[1]);self.assertNotIn('TEST_KEY',json.dumps(out))
        self.assertNotIn('token',out['response']['data'][0]);self.assertFalse(out['model_ingestion_performed'])
        self.assertTrue(out['pagination_complete'])


if __name__=='__main__':unittest.main()
