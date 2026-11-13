import io
import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from clients import fetch, match_context, NoRedirect


class Response(io.BytesIO):
    status = 200
    headers = {'x-ratelimit-remaining':'98'}
    def __init__(self,data): super().__init__(json.dumps(data).encode())


class Tests(unittest.TestCase):
    def test_match_context_stops_after_access_denial_with_explicit_missing_fields(self):
        for failure in ({'http_status': 401, 'error_code': 'HTTP_ERROR'},
                        {'http_status': 403, 'error_code': 'HTTP_ERROR'},
                        {'http_status': 429, 'error_code': 'HTTP_ERROR'},
                        {'error_code': 'ENV_BINDING_MISSING'},
                        {'http_status': 200, 'error_code': 'PROVIDER_ERROR',
                         'failure_kind': 'PROVIDER_AUTHENTICATION_REJECTION'},
                        {'http_status': 200, 'error_code': 'PROVIDER_ERROR', 'failure_kind': 'RATE_LIMITED'}):
            with self.subTest(failure=failure), patch(fetch.__module__ + '.fetch',
                    return_value={'call': dict(failure, api_success=False), 'response': None}) as request:
                result = match_context('api-football', 1)
            self.assertEqual(request.call_count, 1)
            self.assertEqual(set(result), {'match', 'lineups', 'statistics', 'events', 'injuries', 'odds'})
            for name in ('lineups', 'statistics', 'events', 'injuries', 'odds'):
                self.assertEqual(result[name]['call']['error_code'], 'BATCH_PROVIDER_BLOCKED')
                self.assertIsNone(result[name]['response'])

    def test_match_context_stops_mid_batch_and_keeps_previous_evidence(self):
        success = {'call': {'http_status': 200, 'api_success': True}, 'response': {'fixture': 1}}
        failure = {'call': {'http_status': 429, 'api_success': False, 'error_code': 'HTTP_ERROR'}, 'response': None}
        with patch(fetch.__module__ + '.fetch', side_effect=[success, failure]) as request:
            result = match_context('api-football', 1)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(result['match'], success)
        self.assertEqual(result['lineups'], failure)
        self.assertEqual(result['statistics']['call']['error_code'], 'BATCH_PROVIDER_BLOCKED')

    def test_match_context_keeps_full_successful_response_shape(self):
        success = {'call': {'http_status': 200, 'api_success': True}, 'response': {}}
        with patch(fetch.__module__ + '.fetch', return_value=success) as request:
            result = match_context('api-football', 1)
        self.assertEqual(request.call_count, 6)
        self.assertTrue(all(item['call']['api_success'] for item in result.values()))

    def test_bsd_and_openfoot_header_schemes_and_missing_bindings(self):
        for provider,binding,prefix in [('bsd','WCPP_BSD_API_KEY','Token '),('openfoot','WCPP_OPENFOOT_API_KEY','Bearer ')]:
            with patch.dict(os.environ,{},clear=True):
                r=fetch(provider,opener=lambda *a,**k:self.fail('network with missing secret'))
                self.assertEqual(r['call']['error_code'],'ENV_BINDING_MISSING')
            def opener(req,**kwargs):
                self.assertEqual(req.get_header('Authorization'),prefix+'SYNTHETIC_CREDENTIAL_ONLY')
                return Response({'data':[]})
            with patch.dict(os.environ,{binding:'SYNTHETIC_CREDENTIAL_ONLY'},clear=True):
                r=fetch(provider,opener=opener)
            self.assertTrue(r['call']['authenticated'])
            self.assertNotIn('SYNTHETIC_CREDENTIAL_ONLY',json.dumps(r))

    def test_query_authenticated_services_never_persist_key_or_redirect_it(self):
        for provider, binding, path, params, parameter in (
                ('the-odds-api', 'manyzhishu_theodds_sports', '/sports', {}, 'apiKey'),
                ('apifootball-com', 'apifootball2', '/', {'action': 'get_events'}, 'APIkey')):
            key = 'TEST_QUERY_KEY_NOT_REAL_123456'
            def opener(req, **kwargs):
                self.assertIn(parameter + '=' + key, req.full_url)
                return Response([{'message': 'echo ' + key, 'apiKey': key}])
            with self.subTest(provider=provider), patch.dict(os.environ, {binding: key}, clear=True):
                result = fetch(provider, path, params, opener=opener)
            self.assertTrue(result['call']['api_success'])
            self.assertNotIn(key, json.dumps(result))
            self.assertNotIn(parameter + '=', result['call']['url'])

    def test_5dollar_and_public_json_have_distinct_authentication(self):
        def dollar(req, **kwargs):
            self.assertEqual(req.get_header('Authorization'), 'Bearer TEST_DOLLAR_ONLY')
            return Response({'success': 1, 'data': []})
        with patch.dict(os.environ, {'WCPP_5DOLLAR_API_KEY': 'TEST_DOLLAR_ONLY'}, clear=True):
            result = fetch('5dollar', opener=dollar)
            self.assertTrue(result['call']['authenticated'])
            bad = fetch('5dollar', opener=lambda *a, **k: Response({'success': 0}))
            self.assertFalse(bad['call']['api_success'])
        with patch.dict(os.environ, {}, clear=True):
            result = fetch('espn', opener=lambda *a, **k: Response({'events': []}))
        self.assertTrue(result['call']['api_success'])
        self.assertFalse(result['call']['authenticated'])

    def test_verified_new_binding_takes_priority(self):
        def opener(req, **kwargs):
            self.assertEqual(req.get_header('X-apisports-key'), 'NEW_BINDING_TEST_ONLY')
            return Response({'errors': [], 'response': []})
        with patch.dict(os.environ, {'_apifootball': 'NEW_BINDING_TEST_ONLY',
                                    'dashboard_api_football': 'OLD_BINDING_TEST_ONLY'}, clear=True):
            result = fetch('api-football', opener=opener)
        self.assertEqual(result['call']['credential_binding_name'], '_apifootball')

    def test_ambiguous_legacy_binding_is_never_automatically_routed(self):
        with patch.dict(os.environ,{'Apifootballcom_APIKEY':'TEST_ALIAS_ONLY'},clear=True):
            r=fetch('api-football',opener=lambda *a,**k:self.fail('ambiguous key sent'))
            self.assertEqual(r['call']['error_code'],'ENV_BINDING_MISSING')
            with self.assertRaises(ValueError):
                fetch('api-football',credential_binding='Apifootballcom_APIKEY')
            with self.assertRaises(ValueError):
                fetch('apifootball-com',path='/',params={'action':'get_countries'},
                      credential_binding='Apifootballcom_APIKEY')
            with self.assertRaises(ValueError):
                fetch('espn',params={'team':'TEST_ALIAS_ONLY'})

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

    def test_current_cloud_aliases_are_credential_safe(self):
        cases=[('bsd','bsd',{'events':[]}),('openfoot','openfoot',{'matches':[]}),
               ('5dollar','_5DOLLARS',{'success':1,'data':[]}),
               ('the-odds-api','THE_ODDS',[]),('football-data','footballdata_org',{'competitions':[]}),
               ('api-football','APIFOOTBALL',{'errors':[],'response':[]})]
        for provider,binding,body in cases:
            with self.subTest(provider=provider),patch.dict(os.environ,{binding:'CURRENT_ALIAS_TEST_ONLY'},clear=True):
                r=fetch(provider,opener=lambda *a,**k:Response(body))
                self.assertEqual(r['call']['authenticated'], provider != 'football-data')
                self.assertEqual(r['call']['credential_binding_name'],binding)
                self.assertNotIn('CURRENT_ALIAS_TEST_ONLY',json.dumps(r))

    def test_explicit_binding_is_used_and_cannot_cross_providers(self):
        def opener(req, **kwargs):
            self.assertEqual(req.get_header('X-apisports-key'), 'SELECTED_TEST_ONLY')
            return Response({'errors': [], 'response': []})
        with patch.dict(os.environ, {'dashboard_api_football': 'DEFAULT_TEST_ONLY',
                                    '_apifootball': 'SELECTED_TEST_ONLY'}, clear=True):
            result = fetch('api-football', opener=opener,
                           credential_binding='_apifootball')
            self.assertEqual(result['call']['credential_binding_name'], '_apifootball')
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

    def test_http200_sensitive_error_keys_are_classified_before_redaction(self):
        for field in ('token', 'key', 'api_key', 'apikey', 'authentication'):
            key = 'SYNTHETIC_AUTH_ERROR_KEY'
            with self.subTest(field=field), patch.dict(os.environ, {'WCPP_API_FOOTBALL_KEY': key}, clear=True):
                result = fetch('api-football', opener=lambda *a, **k: Response(
                    {'errors': {field: 'Invalid API key ' + key}, 'response': []}))
            self.assertFalse(result['call']['api_success'])
            self.assertFalse(result['call']['authenticated'])
            self.assertEqual(result['call']['error_code'], 'PROVIDER_ERROR')
            self.assertEqual(result['call']['failure_kind'], 'PROVIDER_AUTHENTICATION_REJECTION')
            self.assertNotIn(key, json.dumps(result))

    def test_http200_explicit_rate_or_daily_quota_rejection_stops_context(self):
        cases = [{'rateLimit': 'Too many requests. Please slow down.'},
                 {'requests': 'You have reached the request limit for the day.'}]
        for errors in cases:
            def request(*args, **kwargs):
                return fetch(*args, **kwargs, opener=lambda *a, **k: Response({'errors': errors, 'response': []}))
            with self.subTest(errors=errors), patch.dict(os.environ, {'WCPP_API_FOOTBALL_KEY': 'SYNTHETIC_ONLY'}, clear=True), \
                    patch(fetch.__module__ + '.fetch', side_effect=request) as mocked:
                result = match_context('api-football', 1)
            self.assertEqual(mocked.call_count, 1)
            self.assertFalse(result['match']['call']['api_success'])
            self.assertEqual(result['match']['call']['failure_kind'], 'RATE_LIMITED')
            self.assertEqual(result['lineups']['call']['error_code'], 'BATCH_PROVIDER_BLOCKED')

    def test_http200_resource_plan_rejection_does_not_block_other_resources(self):
        with patch.dict(os.environ, {'WCPP_API_FOOTBALL_KEY': 'SYNTHETIC_ONLY'}, clear=True):
            rejected = fetch('api-football', opener=lambda *a, **k: Response(
                {'errors': {'plan': 'Your plan does not cover this season.'}, 'response': []}))
        self.assertEqual(rejected['call']['failure_kind'], 'PROVIDER_PLAN_OR_PERMISSION_REJECTION')
        with patch(fetch.__module__ + '.fetch', return_value=rejected) as request:
            match_context('api-football', 1)
        self.assertEqual(request.call_count, 6)

    def test_credentials_cannot_be_sent_to_other_hosts_or_in_query(self):
        for path in ('https://evil.test','/../other','//evil.test','/fixtures?key=x'):
            with self.assertRaises(ValueError):fetch('api-football',path)
        with self.assertRaises(ValueError):fetch('api-football',params={'apikey':'TEST_ONLY'})
        self.assertIsNone(NoRedirect().redirect_request(None,None,302,'',{},'https://evil.test'))
        with patch.dict(os.environ,{'WCPP_API_FOOTBALL_KEY':'TEST_ONLY'}):
            with self.assertRaises(ValueError):fetch('api-football',params={'q':'TEST_ONLY'})

    def test_public_catalog_does_not_certify_key_authentication(self):
        with patch.dict(os.environ, {'footballdata_org': 'SYNTHETIC_ONLY'}, clear=True):
            catalog = fetch('football-data', opener=lambda *a, **k: Response({'competitions': []}))
            matches = fetch('football-data', '/matches', opener=lambda *a, **k: Response({'matches': []}))
        self.assertTrue(catalog['call']['api_success'])
        self.assertFalse(catalog['call']['authenticated'])
        self.assertEqual(catalog['call']['authentication_status'], 'UNVERIFIED_PUBLIC_CATALOG')
        self.assertTrue(matches['call']['authenticated'])

    def test_html_denial_retains_safe_diagnostics_without_body_or_secret(self):
        key = 'SYNTHETIC_EDGE_SECRET'
        def denied(*a, **k):
            raise HTTPError('https://example.test/?apiKey=' + key, 403, key,
                            {'Content-Type': 'text/html', 'CF-Ray': 'synthetic-ray',
                             'Retry-After': '60', 'Set-Cookie': key},
                            io.BytesIO(('<html>blocked ' + key + '</html>').encode()))
        with patch.dict(os.environ, {'apifootball': key}, clear=True):
            result = fetch('api-football', opener=denied)
        call = result['call']
        self.assertEqual(call['http_status'], 403)
        self.assertEqual(call['content_type'], 'text/html')
        self.assertEqual(call['cf_ray'], 'synthetic-ray')
        self.assertEqual(call['quota_headers']['retry-after'], '60')
        self.assertEqual(call['response_format'], 'HTML')
        self.assertEqual(call['authentication_status'], 'UNVERIFIED')
        self.assertIsNone(result['response'])
        self.assertNotIn(key, json.dumps(result))
        self.assertNotIn('Set-Cookie', json.dumps(result))

    def test_invalid_json_preserves_http_status_for_diagnosis(self):
        response = io.BytesIO(b'<html>maintenance</html>')
        response.status = 200
        response.headers = {'Content-Type': 'text/html'}
        with patch.dict(os.environ, {}, clear=True):
            result = fetch('espn', opener=lambda *a, **k: response)
        self.assertEqual(result['call']['http_status'], 200)
        self.assertEqual(result['call']['content_type'], 'text/html')
        self.assertEqual(result['call']['error_code'], 'INVALID_RESPONSE')
        self.assertFalse(result['call']['api_success'])

    def test_403_body_distinguishes_edge_auth_and_plan_without_leaking_data(self):
        key = 'SYNTHETIC_DENIAL_CREDENTIAL'
        cases = [
            ('text/html', '<html><title>Attention Required! | Cloudflare</title>' + key + '</html>',
             'CLOUDFLARE_HTML_ACCESS_BLOCK'),
            ('text/html', '<html>Forbidden ' + key + '</html>', 'HTML_ACCESS_DENIED'),
            ('application/json', json.dumps({'errors': {'token': 'Invalid API key ' + key}}),
             'PROVIDER_AUTHENTICATION_REJECTION'),
            ('application/json', json.dumps({'errors': {'plan': 'Access denied ' + key}}),
             'PROVIDER_PLAN_OR_PERMISSION_REJECTION'),
            ('application/json', json.dumps({'error': 'plan_upgrade_required'}),
             'PROVIDER_PLAN_OR_PERMISSION_REJECTION'),
            ('application/json', json.dumps({'message': 'Forbidden ' + key}), 'PROVIDER_JSON_REJECTION'),
        ]
        for content_type, body, kind in cases:
            def denied(*a, **k):
                raise HTTPError('https://example.test/?key=' + key, 403, key,
                                {'Content-Type': content_type, 'CF-Ray': 'synthetic-ray', 'Set-Cookie': key},
                                io.BytesIO(body.encode()))
            with self.subTest(kind=kind), patch.dict(os.environ, {'apifootball': key}, clear=True):
                result = fetch('api-football', opener=denied)
                self.assertEqual(result['call']['failure_kind'], kind)
                self.assertFalse(result['call']['authenticated'])
                self.assertIsNone(result['response'])
                self.assertNotIn(key, json.dumps(result))
                self.assertNotIn('Set-Cookie', json.dumps(result))

    def test_therundown_official_header_protected_probe_and_public_catalogs(self):
        key = 'SYNTHETIC_RUNDOWN_CREDENTIAL'
        def opener(req, **kwargs):
            self.assertTrue(req.full_url.startswith('https://therundown.io/api/v2/'))
            self.assertEqual(req.get_header('X-therundown-key'), key)
            self.assertNotIn(key, req.full_url)
            return Response({'data': [], 'X-TheRundown-Key': key, 'message': 'echo ' + key})
        with patch.dict(os.environ, {'therundown': key}, clear=True):
            protected = fetch('therundown', opener=opener)
            self.assertEqual(protected['call']['url'], 'https://therundown.io/api/v2/markets')
            self.assertTrue(protected['call']['authenticated'])
            self.assertNotIn(key, json.dumps(protected))
            self.assertNotIn('X-TheRundown-Key', json.dumps(protected))
            for path in ('/api/v2/sports', '/api/v2/affiliates/'):
                catalog = fetch('therundown', path, opener=opener)
                self.assertTrue(catalog['call']['api_success'])
                self.assertEqual(catalog['call']['authentication_status'], 'UNVERIFIED_PUBLIC_CATALOG')
                self.assertFalse(catalog['call']['authenticated'])
            with self.assertRaises(ValueError):
                fetch('therundown', params={'X-TheRundown-Key': 'any-value'})
            with self.assertRaises(ValueError):
                fetch('therundown', credential_binding='apifootball')
        with patch.dict(os.environ, {}, clear=True):
            missing = fetch('therundown', opener=lambda *a, **k: self.fail('missing key sent'))
            self.assertEqual(missing['call']['error_code'], 'ENV_BINDING_MISSING')


if __name__=='__main__':unittest.main()
