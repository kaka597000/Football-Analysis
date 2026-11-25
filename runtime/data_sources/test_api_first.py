import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from api_first import run, pointer


class ApiFirstTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 29, tzinfo=timezone.utc)
        self.calls = []
        self.plan = {'requests': {
            'one': {'provider': 'espn', 'path': '/usa.1/summary', 'params': {'event': 1}, 'ttl_seconds': 60},
            'duplicate': {'provider': 'espn', 'path': '/usa.1/summary', 'params': {'event': 1}, 'ttl_seconds': 60},
            'backup': {'provider': 'fotmob', 'path': '/matchDetails', 'params': {'matchId': 2}, 'ttl_seconds': 60}},
            'fields': [self.field('lineup', 'one', 'backup'), self.field('history', 'duplicate')]}

    def field(self, name, *ids):
        return {'match_id': 'fixture-one', 'field': name, 'candidates': [
            {'request_id': rid, 'pointer': '/data/' + name,
             'identity_checks': [{'pointer': '/id', 'equals': 'one'}]} for rid in ids]}

    def fetch(self, **arguments):
        self.calls.append(arguments)
        return {'call': {'api_success': True, 'query_time': self.now.isoformat()},
                'response': {'id': 'one', 'data': {'lineup': [1], 'history': [2]}}}

    def collect(self, plan, folder, fetcher=None):
        return run(plan, folder, fetcher or self.fetch, clock=lambda: self.now, sleeper=lambda _: None)

    def test_dedup_cache_and_skip_unneeded_secondary_api(self):
        with tempfile.TemporaryDirectory() as folder:
            a = self.collect(self.plan, folder)
            self.assertEqual(len(self.calls), 1)
            self.assertEqual(a['web_gap_tasks'], [])
            self.collect(self.plan, folder)
            self.assertEqual(len(self.calls), 1)
            self.now += timedelta(seconds=61)
            self.collect(self.plan, folder)
            self.assertEqual(len(self.calls), 2)

    def test_fallback_is_only_for_empty_field_after_api_alternatives(self):
        def fetcher(**arguments):
            result = self.fetch(**arguments)
            result['response']['data']['lineup'] = []
            return result
        with tempfile.TemporaryDirectory() as folder:
            out = self.collect(self.plan, folder, fetcher)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual([t['field'] for t in out['web_gap_tasks']], ['lineup'])
        task = out['web_gap_tasks'][0]
        self.assertEqual(task['assigned_model'], 'gpt-6.2-sol')
        self.assertEqual(task['reasoning_effort'], 'max')
        self.assertEqual(task['status'], 'PLANNED_NOT_RUN')
        self.assertTrue(out['api_phase_complete'])
        self.assertFalse(out['model_ingestion_performed'])

    def test_field_model_or_difficulty_can_override_the_batch_model(self):
        self.plan['web_gap_model'] = 'gpt-6.2-sol'
        self.plan['fields'] = [self.field(name, 'one') for name in ('absences', 'quotes', 'shots', 'form')]
        self.plan['fields'][1]['web_gap_model'] = ' gpt-6-luna '
        self.plan['fields'][2]['web_gap_difficulty'] = 'hard'
        self.plan['fields'][3].update(web_gap_model='gpt-6.2-sol', web_gap_difficulty='breakthrough')
        with tempfile.TemporaryDirectory() as folder:
            rows = self.collect(self.plan, folder)['web_gap_tasks']
        self.assertEqual([(r['assigned_model'], r['reasoning_effort']) for r in rows], [
            ('gpt-6.2-sol', 'max'), ('gpt-6-luna', None),
            ('gpt-6.2-astra', None), ('gpt-6.2-sol', 'max')])

    def test_routine_difficulty_and_null_model_override_a_batch_model(self):
        self.plan['web_gap_model'] = 'gpt-6-luna'
        self.plan['fields'] = [self.field(name, 'one') for name in ('absences', 'quotes', 'shots')]
        self.plan['fields'][1]['web_gap_difficulty'] = 'routine'
        self.plan['fields'][2]['web_gap_model'] = None
        with tempfile.TemporaryDirectory() as folder:
            rows = self.collect(self.plan, folder)['web_gap_tasks']
        self.assertEqual([(r['assigned_model'], r['reasoning_effort']) for r in rows], [
            ('gpt-6-luna', None), ('gpt-6.2-sol', 'max'), ('gpt-6.2-sol', 'max')])

    def test_hard_and_breakthrough_fields_use_astra_with_a_null_batch_model(self):
        self.plan['web_gap_model'] = None
        self.plan['fields'] = [self.field(name, 'one') for name in ('absences', 'quotes', 'shots', 'lineup')]
        self.plan['fields'][0]['web_gap_difficulty'] = 'hard'
        self.plan['fields'][1]['web_gap_difficulty'] = 'breakthrough'
        self.plan['fields'][3]['web_gap_difficulty'] = 'hard'
        with tempfile.TemporaryDirectory() as folder:
            rows = self.collect(self.plan, folder)['web_gap_tasks']
        self.assertEqual([(r['field'], r['assigned_model'], r['reasoning_effort']) for r in rows], [
            ('absences', 'gpt-6.2-astra', None), ('quotes', 'gpt-6.2-astra', None),
            ('shots', 'gpt-6.2-sol', 'max')])

    def test_batch_difficulty_can_be_overridden_by_field(self):
        self.plan['web_gap_difficulty'] = 'hard'
        self.plan['fields'] = [self.field(name, 'one') for name in ('absences', 'quotes', 'shots', 'form')]
        self.plan['fields'][1]['web_gap_difficulty'] = 'routine'
        self.plan['fields'][2]['web_gap_difficulty'] = None
        self.plan['fields'][3]['web_gap_model'] = 'gpt-6-luna'
        with tempfile.TemporaryDirectory() as folder:
            rows = self.collect(self.plan, folder)['web_gap_tasks']
        self.assertEqual([(r['assigned_model'], r['reasoning_effort']) for r in rows], [
            ('gpt-6.2-astra', None), ('gpt-6.2-sol', 'max'),
            ('gpt-6.2-sol', 'max'), ('gpt-6-luna', None)])

    def test_explicit_astra_requires_eligible_difficulty_before_collection(self):
        cases = (
            ({'web_gap_model': 'gpt-6.2-astra'}, {}),
            ({}, {'web_gap_model': 'gpt-6.2-astra'}),
            ({'web_gap_difficulty': 'hard'},
             {'web_gap_model': 'gpt-6.2-astra', 'web_gap_difficulty': 'routine'}),
        )
        for batch_settings, field_settings in cases:
            plan = copy.deepcopy(self.plan)
            plan.update(batch_settings)
            plan['fields'][0]['web_gap_difficulty'] = 'routine'
            plan['fields'][1].update(field_settings)
            message = r'fields\[1\].*gpt-6\.2-astra.*web_gap_difficulty.*hard.*breakthrough'
            with self.subTest(batch=batch_settings, field=field_settings), tempfile.TemporaryDirectory() as folder:
                with self.assertRaisesRegex(ValueError, message):
                    self.collect(plan, folder)
        self.assertFalse(self.calls)

    def test_explicit_astra_accepts_effective_hard_or_breakthrough_difficulty(self):
        self.plan.update(web_gap_model='gpt-6.2-astra', web_gap_difficulty='hard')
        self.plan['fields'] = [self.field(name, 'one') for name in ('absences', 'quotes')]
        self.plan['fields'][1].update(web_gap_model='gpt-6.2-astra', web_gap_difficulty='breakthrough')
        with tempfile.TemporaryDirectory() as folder:
            rows = self.collect(self.plan, folder)['web_gap_tasks']
        self.assertEqual([(r['assigned_model'], r['reasoning_effort']) for r in rows], [
            ('gpt-6.2-astra', None), ('gpt-6.2-astra', None)])

    def test_invalid_web_lookup_driver_is_rejected_before_collection(self):
        for model in (['gpt-6.2-sol'], '', '  '):
            self.plan['web_gap_model'] = model
            with self.subTest(model=model), tempfile.TemporaryDirectory() as folder:
                with self.assertRaisesRegex(ValueError, 'web_gap_model'):
                    self.collect(self.plan, folder)
        self.assertFalse(self.calls)

    def test_invalid_web_lookup_difficulty_is_rejected_before_collection(self):
        self.plan['fields'][0]['web_gap_difficulty'] = 'hardest'
        with tempfile.TemporaryDirectory() as folder, self.assertRaisesRegex(ValueError, 'web_gap_difficulty'):
            self.collect(self.plan, folder)
        self.assertFalse(self.calls)

    def test_wrong_identity_or_stale_quote_does_not_fill_gap(self):
        plan = copy.deepcopy(self.plan)
        plan['fields'] = [self.field('history', 'one')]
        candidate = plan['fields'][0]['candidates'][0]
        with tempfile.TemporaryDirectory() as folder:
            candidate['identity_checks'][0]['equals'] = 'another-fixture'
            self.assertEqual(len(self.collect(plan, folder)['web_gap_tasks']), 1)
            candidate['identity_checks'][0]['equals'] = 'one'
            plan['fields'][0].update(requires_source_time=True, max_age_seconds=10)
            candidate['source_time_pointer'] = '/quote_at'
            self.assertEqual(len(self.collect(plan, folder)['web_gap_tasks']), 1)

    def test_permission_denial_stops_same_provider_and_no_success_cache(self):
        plan = copy.deepcopy(self.plan)
        plan['requests']['duplicate']['params']['event'] = 3
        def denied(**arguments):
            self.calls.append(arguments)
            return {'call': {'api_success': False, 'http_status': 403, 'error_code': 'HTTP_ERROR'}, 'response': None}
        with tempfile.TemporaryDirectory() as folder:
            self.collect(plan, folder, denied)
            self.assertEqual(list(Path(folder).iterdir()), [])
        self.assertEqual(len([c for c in self.calls if c['provider'] == 'espn']), 1)

    def test_http200_authentication_or_rate_rejection_stops_batch_without_caching(self):
        for failure_kind in ('PROVIDER_AUTHENTICATION_REJECTION', 'RATE_LIMITED'):
            self.calls.clear()
            plan = copy.deepcopy(self.plan)
            plan['requests']['duplicate']['params']['event'] = 3
            def denied(**arguments):
                self.calls.append(arguments)
                return {'call': {'api_success': False, 'http_status': 200,
                                'error_code': 'PROVIDER_ERROR',
                                'failure_kind': failure_kind}, 'response': {'errors': {}}}
            with self.subTest(failure_kind=failure_kind), tempfile.TemporaryDirectory() as folder:
                result = self.collect(plan, folder, denied)
                self.assertEqual(list(Path(folder).iterdir()), [])
            self.assertEqual(len([c for c in self.calls if c['provider'] == 'espn']), 1)
            self.assertEqual(result['requests']['one']['call']['error_code'], 'BATCH_PROVIDER_BLOCKED')
            self.assertEqual(result['api_evidence'], [])

    def test_no_web_fallback_without_api_candidates_or_identity(self):
        plan = copy.deepcopy(self.plan)
        for bad in ([], [{'request_id': 'one', 'pointer': '/data'}]):
            plan['fields'][0]['candidates'] = bad
            with tempfile.TemporaryDirectory() as folder, self.assertRaises(ValueError):
                self.collect(plan, folder)
        self.assertEqual(self.calls, [])

    def test_corrupt_or_future_cache_is_not_reused(self):
        with tempfile.TemporaryDirectory() as folder:
            self.collect(self.plan, folder)
            target = next(Path(folder).glob('*.json'))
            target.write_text(json.dumps({'sha256': 'wrong', 'result': {}}))
            self.collect(self.plan, folder)
            self.assertEqual(len(self.calls), 2)
            self.now -= timedelta(hours=1)
            self.collect(self.plan, folder)
            self.assertEqual(len(self.calls), 3)

    def test_5dollar_checks_status_once_before_fixtures(self):
        plan = copy.deepcopy(self.plan)
        plan['requests']['one'] = {'provider': '5dollar', 'path': '/fixtures', 'ttl_seconds': 60}
        plan['fields'] = [self.field('history', 'one')]
        with tempfile.TemporaryDirectory() as folder:
            self.collect(plan, folder)
        self.assertEqual([c['path'] for c in self.calls], ['/status', '/fixtures'])

    def test_quota_is_only_reserved_for_network_after_cache_and_dedup(self):
        from quota import QuotaLedger
        plan=copy.deepcopy(self.plan)
        plan['quota_policies']={'espn':{'limit':1,'period':'day'}}
        with tempfile.TemporaryDirectory() as folder:
            ledger=str(Path(folder)/'quota.sqlite')
            a=run(plan,Path(folder)/'cache',fetcher=self.fetch,clock=lambda:self.now,
                  sleeper=lambda _:None,quota_ledger=ledger)
            run(plan,Path(folder)/'cache',fetcher=self.fetch,clock=lambda:self.now,
                sleeper=lambda _:None,quota_ledger=ledger)
            self.assertEqual(len(self.calls),1)
            self.assertEqual(len([r for r in QuotaLedger(ledger).history()
                                  if r['phase']=='RESERVE' and r['status']=='RESERVED']),1)
            self.assertEqual(a['quota_guard'],'EXPLICIT_POLICIES_ONLY')

    def test_array_pointer_rejects_python_negative_and_noncanonical_indices(self):
        data = {'events': [{'id': 'first'}, {'id': 'last'}]}
        for invalid in ['-1', '+1', '01', ' 1', '-', '١']:
            self.assertIsNone(pointer(data, '/events/' + invalid + '/id'))
        self.assertEqual(pointer(data, '/events/0/id'), 'first')
        self.assertEqual(pointer({'01': 'valid object key'}, '/01'), 'valid object key')

    def test_negative_index_cannot_satisfy_identity_check(self):
        plan = copy.deepcopy(self.plan)
        plan['fields'] = [self.field('lineup', 'one')]
        plan['fields'][0]['candidates'][0]['identity_checks'] = [
            {'pointer': '/data/lineup/-1', 'equals': 1}]
        with tempfile.TemporaryDirectory() as folder:
            result = self.collect(plan, folder)
        self.assertEqual(len(result['web_gap_tasks']), 1)


if __name__ == '__main__':
    unittest.main()
