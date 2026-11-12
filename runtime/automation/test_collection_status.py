"""Synthetic transport and channel health tests; no network requests."""
import json
from pathlib import Path
import socket
import ssl
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from collection_status import classify_failure
from collector import collect, ASIAN


class CollectionStatusTests(unittest.TestCase):
    scope = {'cohort':'eng.1', 'competition':'eng.1', 'stages':['regular']}

    def test_controlled_failure_categories_without_sensitive_text(self):
        cases = [(URLError(socket.gaierror('SECRET')), 'DNS'),
                 (URLError(TimeoutError('SECRET')), 'TIMEOUT'),
                 (URLError(ssl.SSLError('SECRET')), 'TLS'),
                 (URLError('SECRET'), 'NETWORK_UNCLASSIFIED'),
                 (ConnectionResetError('SECRET'), 'CONNECTION'),
                 (json.JSONDecodeError('SECRET','SECRET',0), 'INVALID_RESPONSE_ENCODING'),
                 (ValueError('SECRET'), 'INVALID_RESPONSE'),
                 (RuntimeError('SECRET'), 'UNEXPECTED_ERROR')]
        for error, expected in cases:
            with self.subTest(expected=expected):
                result=classify_failure(error)
                self.assertEqual(result['category'],expected)
                self.assertNotIn('SECRET',json.dumps(result))

    def test_http_codes_and_retry_classification(self):
        for code, category, retry in [(401,'AUTHENTICATION',False),(403,'ACCESS_DENIED',False),
                                       (429,'RATE_LIMIT',False),(503,'HTTP_SERVER',True),(404,'HTTP_ERROR',False)]:
            e=HTTPError('https://example.test/?api_key=SECRET',code,'SECRET',{'Authorization':'SECRET'},None)
            result=classify_failure(e)
            self.assertEqual(result,dict(category=category,http_status=code,retryable=retry))
            self.assertNotIn('SECRET',json.dumps(result))

    def test_history_success_does_not_claim_market_ready(self):
        def fetch(url,headers=None):
            if url==ASIAN:raise HTTPError(url,403,'SECRET',None,None)
            return {'events':[]}
        with tempfile.TemporaryDirectory() as folder:
            result=collect(folder,[self.scope],fetcher=fetch,lookback_days=0)
            self.assertEqual(result['successful_requests'],1)
            self.assertEqual(result['channels']['history']['status'],'RESPONSE_RECEIVED')
            self.assertEqual(result['channels']['market']['status'],'FAILED')
            self.assertEqual(result['data_status'],'PARTIAL_NO_VERIFIED_MARKET')
            self.assertFalse(result['market_model_ready'])
            self.assertFalse(result['retry_used'])
            log=(Path(folder)/'requests.jsonl').read_text()
            self.assertNotIn('SECRET',log)
            self.assertIn('ACCESS_DENIED',log)

    def test_untimed_raw_market_is_not_a_verified_quote(self):
        with tempfile.TemporaryDirectory() as folder:
            result=collect(folder,[self.scope],fetcher=lambda *a,**k:{'events':[]},lookback_days=0)
            self.assertTrue(result['market_snapshot_saved'])
            self.assertEqual(result['channels']['market']['quote_status'],'RAW_WITHOUT_VERIFIED_QUOTE_TIME')
            self.assertEqual(result['channels']['market']['verified_quote_count'],0)
            self.assertFalse(result['market_model_ready'])

    def test_budget_is_not_network_failure_or_empty_market(self):
        with tempfile.TemporaryDirectory() as folder,patch('collector.time.monotonic',side_effect=[0,500,500]):
            result=collect(folder,[self.scope],fetcher=lambda *a,**k:self.fail('no request'),lookback_days=0)
            self.assertEqual(result['channels']['market']['status'],'NOT_ATTEMPTED')
            self.assertEqual(result['channels']['market']['failures'],{})
            self.assertEqual(result['data_status'],'NO_USABLE_RESPONSE')

    def test_invalid_json_not_retried_or_saved_as_success(self):
        with tempfile.TemporaryDirectory() as folder:
            result=collect(folder,[self.scope],fetcher=lambda *a,**k:b'NOT JSON SECRET',lookback_days=0)
            self.assertEqual(result['request_count'],2)
            self.assertFalse(result['retry_used'])
            self.assertEqual(result['successful_requests'],0)
            self.assertNotIn('SECRET',(Path(folder)/'requests.jsonl').read_text())


if __name__=='__main__':unittest.main()
