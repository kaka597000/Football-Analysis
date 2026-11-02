import io
import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from clients import fetch, NoRedirect


class Response(io.BytesIO):
    status = 200
    headers = {'x-ratelimit-remaining':'98'}
    def __init__(self,data): super().__init__(json.dumps(data).encode())


class Tests(unittest.TestCase):
    def test_verified_new_binding_takes_priority(self):
        def opener(req, **kwargs):
            self.assertEqual(req.get_header('X-apisports-key'), 'NEW_BINDING_TEST_ONLY')
            return Response({'errors': [], 'response': []})
        with patch.dict(os.environ, {'_apifootball': 'NEW_BINDING_TEST_ONLY',
                                    'dashboard_api_football': 'OLD_BINDING_TEST_ONLY'}, clear=True):
            result = fetch('api-football', opener=opener)
        self.assertEqual(result['call']['credential_binding_name'], '_apifootball')

    def test_configured_alias_is_used_when_standard_variable_absent(self):
        def opener(req,**kwargs):
            self.assertEqual(req.get_header('X-apisports-key'),'TEST_ALIAS_ONLY')
            return Response({'errors':{},'response':{'subscription':{'plan':'test-only'}}})
        with patch.dict(os.environ,{'Apifootballcom_APIKEY':'TEST_ALIAS_ONLY'},clear=True):
            r=fetch('api-football',opener=opener)
        self.assertTrue(r['call']['api_success'])
        self.assertEqual(r['call']['credential_binding_name'],'Apifootballcom_APIKEY')

    def test_new_runtime_variable_takes_priority_over_old_proxy_alias(self):
        def opener(req,**kwargs):
            self.assertEqual(req.get_header('X-apisports-key'),'NEW_TEST_ONLY')
            return Response({'errors':[],'response':[]})
        with patch.dict(os.environ,{'dashboard_api_football':'NEW_TEST_ONLY',
                                    'Apifootballcom_APIKEY':'OLD_TEST_ONLY'},clear=True):
            r=fetch('api-football',opener=opener)
        self.assertEqual(r['call']['credential_binding_name'],'dashboard_api_football')

    def test_missing_binding_never_makes_request(self):
        with patch.dict(os.environ,{},clear=True):
            result=fetch('api-football',opener=lambda *a,**k:self.fail('unexpected request'))
        self.assertEqual(result['call']['error_code'],'ENV_BINDING_MISSING')

    def test_explicit_binding_is_used_and_cannot_cross_providers(self):
        def opener(req, **kwargs):
            self.assertEqual(req.get_header('X-apisports-key'), 'SELECTED_TEST_ONLY')
            return Response({'errors': [], 'response': []})
        with patch.dict(os.environ, {'dashboard_api_football': 'DEFAULT_TEST_ONLY',
                                    'Apifootballcom_APIKEY': 'SELECTED_TEST_ONLY'}, clear=True):
            result = fetch('api-football', opener=opener,
                           credential_binding='Apifootballcom_APIKEY')
            self.assertEqual(result['call']['credential_binding_name'], 'Apifootballcom_APIKEY')
            with self.assertRaises(ValueError):
                fetch('api-football', opener=lambda *a, **k: self.fail('unexpected request'),
                      credential_binding='football_data')
            missing = fetch('api-football', credential_binding='WCPP_API_FOOTBALL_KEY',
                            opener=lambda *a, **k: self.fail('unexpected request'))
            self.assertEqual(missing['call']['error_code'], 'ENV_BINDING_MISSING')

    def test_correct_header_success_and_no_secret_saved(self):
        key='TEST_ONLY_NOT_A_REAL_CREDENTIAL'
        def opener(req,**kwargs):
            self.assertEqual(req.get_header('X-auth-token'),key)
            return Response({'competitions':[], 'token':key, 'message':'echo '+key})
        with patch.dict(os.environ,{'WCPP_FOOTBALL_DATA_API_KEY':key}):
            result=fetch('football-data',opener=opener)
        self.assertTrue(result['call']['api_success']);self.assertNotIn(key,json.dumps(result))

    def test_http200_errors_and_http_denials_are_not_access(self):
        with patch.dict(os.environ,{'WCPP_API_FOOTBALL_KEY':'TEST_ONLY'}):
            r=fetch('api-football',opener=lambda *a,**k:Response({'errors':{'plan':'forbidden'},'response':[]}))
            self.assertFalse(r['call']['api_success'])
            def denied(*a,**k):raise HTTPError('https://example.test',403,'denied',{},None)
            r=fetch('api-football',opener=denied)
            self.assertEqual(r['call']['http_status'],403);self.assertIsNone(r['response'])

    def test_credentials_cannot_be_sent_to_other_hosts_or_in_query(self):
        for path in ('https://evil.test','/../other','//evil.test','/fixtures?key=x'):
            with self.assertRaises(ValueError):fetch('api-football',path)
        with self.assertRaises(ValueError):fetch('api-football',params={'apikey':'TEST_ONLY'})
        self.assertIsNone(NoRedirect().redirect_request(None,None,302,'',{},'https://evil.test'))
        with patch.dict(os.environ,{'WCPP_API_FOOTBALL_KEY':'TEST_ONLY'}):
            with self.assertRaises(ValueError):fetch('api-football',params={'q':'TEST_ONLY'})


if __name__=='__main__':unittest.main()
