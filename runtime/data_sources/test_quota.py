"""Synthetic software tests only; no live service or credential access."""
from datetime import datetime, timedelta, timezone
import multiprocessing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from quota import QuotaLedger, run_with_quota

PROVIDER = 'the-odds-api'
ACCOUNT = 'WCPP_THE_ODDS_API_KEY'
NOW = datetime(2026,10,30,8,tzinfo=timezone.utc)


def compete(path, gate, output):
    ledger = QuotaLedger(path)
    gate.wait()
    output.put(ledger.reserve(PROVIDER, ACCOUNT, limit=1, now=NOW)['allowed'])


class QuotaTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name)/'quota.sqlite'
        self.ledger = QuotaLedger(self.path)

    def reserve(self, **kw):
        return self.ledger.reserve(PROVIDER, ACCOUNT, now=NOW, **kw)

    def test_unknown_budget_blocks_without_calling_fetcher(self):
        result=run_with_quota(self.ledger,PROVIDER,ACCOUNT,
                              lambda:self.fail('network must not run'))
        self.assertEqual(result['call']['error_code'],'QUOTA_UNKNOWN')
        self.assertEqual(self.ledger.history()[0]['status'],'QUOTA_UNKNOWN')

    def test_last_credit_only_one_process_reserves_it(self):
        ctx=multiprocessing.get_context('spawn')
        gate=ctx.Event();out=ctx.Queue()
        jobs=[ctx.Process(target=compete,args=(str(self.path),gate,out)) for _ in range(4)]
        for job in jobs:job.start()
        gate.set()
        for job in jobs:
            job.join(20)
            self.assertEqual(job.exitcode,0)
        self.assertEqual(sum(out.get(timeout=2) for _ in jobs),1)

    def test_budget_policy_cannot_change_limit_or_period(self):
        self.assertTrue(self.reserve(limit=1)['allowed'])
        self.assertEqual(self.reserve(limit=500)['status'],'POLICY_CONFLICT')
        self.assertEqual(self.reserve(limit=1,period='month')['status'],'POLICY_CONFLICT')
        self.assertEqual(self.reserve(limit=0)['status'],'POLICY_CONFLICT')
        self.assertEqual(self.reserve(limit=1)['status'],'QUOTA_EXHAUSTED')

    def test_unknown_budget_does_not_lock_policy_and_zero_cost_is_rejected(self):
        self.assertEqual(self.reserve()['status'],'QUOTA_UNKNOWN')
        self.assertTrue(self.reserve(limit=4,period='month')['allowed'])
        with self.assertRaisesRegex(ValueError,'positive estimated units'):
            self.reserve(limit=4,estimated_units=0,period='month')
        with self.assertRaisesRegex(ValueError,'positive estimated units'):
            run_with_quota(self.ledger,PROVIDER,ACCOUNT,
                           lambda:self.fail('network must not run'),limit=4,
                           period='month',estimated_units=0)

    def test_legacy_history_is_pinned_to_first_explicit_budget(self):
        self.assertTrue(self.reserve(limit=1)['allowed'])
        with sqlite3.connect(self.path) as c:
            c.execute('DELETE FROM policies')
        self.assertEqual(self.reserve(limit=5)['status'],'POLICY_CONFLICT')
        self.assertEqual(self.reserve(limit=1)['status'],'QUOTA_EXHAUSTED')

    def test_rate_limit_and_alias_share_account(self):
        a=self.reserve(limit=5,min_interval_seconds=10)
        self.assertTrue(a['allowed'])
        b=self.ledger.reserve(PROVIDER,'manyzhishu_theodds_sports',limit=5,
                               now=NOW+timedelta(seconds=5))
        self.assertEqual(b['status'],'RATE_LIMITED')
        self.assertEqual(b['retry_after_seconds'],5)
        c=self.ledger.reserve(PROVIDER,ACCOUNT,limit=5,now=NOW+timedelta(seconds=10))
        self.assertTrue(c['allowed'])

    def test_failure_does_not_refund_possibly_spent_request(self):
        ticket=self.reserve(limit=1)
        self.ledger.settle(ticket['reservation_id'],outcome='NETWORK_ERROR',now=NOW)
        self.assertEqual(self.reserve(limit=1)['status'],'QUOTA_EXHAUSTED')
        with self.assertRaises(ValueError):
            self.ledger.settle(ticket['reservation_id'],outcome='TIMEOUT',now=NOW)

    def test_extra_actual_cost_and_provider_remaining_update(self):
        ticket=self.reserve(limit=10,estimated_units=1)
        self.ledger.settle(ticket['reservation_id'],http_status=200,
            quota_headers={'x-requests-last':'3','x-requests-remaining':'1'},now=NOW)
        self.assertEqual(self.reserve(limit=10,estimated_units=2)['status'],'QUOTA_EXHAUSTED')
        self.assertTrue(self.reserve(limit=10,estimated_units=1)['allowed'])
        self.assertEqual(self.reserve(limit=10)['status'],'QUOTA_EXHAUSTED')
        history=self.ledger.history()
        self.assertEqual(history[1]['units'],3)

    def test_429_open_circuit_and_period_reset(self):
        ticket=self.reserve(limit=5)
        self.ledger.settle(ticket['reservation_id'],http_status=429,now=NOW)
        self.assertEqual(self.reserve(limit=5)['status'],'CIRCUIT_OPEN')
        self.assertTrue(self.ledger.reserve(PROVIDER,ACCOUNT,limit=5,
            now=NOW+timedelta(days=1))['allowed'])

    def test_retry_after_survives_midnight(self):
        stamp=NOW.replace(hour=23,minute=59,second=50)
        t=self.ledger.reserve(PROVIDER,ACCOUNT,limit=5,now=stamp)
        self.ledger.settle(t['reservation_id'],http_status=429,
                          quota_headers={'retry-after':'120'},now=stamp)
        next_day=stamp+timedelta(seconds=20)
        self.assertEqual(self.ledger.reserve(PROVIDER,ACCOUNT,limit=5,now=next_day)['status'],'CIRCUIT_OPEN')

    def test_monthly_window_and_no_credential_fields_persisted(self):
        t=self.reserve(limit=2,period='month')
        self.assertEqual(t['period_end_utc'],'2026-11-01T00:00:00+00:00')
        with self.assertRaises(ValueError):
            self.ledger.settle(t['reservation_id'],quota_headers={'Authorization':'TEST_PRIVATE_VALUE'},now=NOW)
        with self.assertRaises(ValueError):
            self.ledger.reserve(PROVIDER,'TEST_PRIVATE_VALUE',limit=2,now=NOW)
        self.assertNotIn('TEST_PRIVATE_VALUE',str(self.ledger.history()))

    def test_history_cannot_be_updated_or_deleted(self):
        self.reserve(limit=2)
        with sqlite3.connect(self.path) as c:
            for sql in ('UPDATE events SET status="FAKE"','DELETE FROM events'):
                with self.assertRaises(sqlite3.IntegrityError):c.execute(sql)

    def test_wrapper_records_safe_network_error(self):
        def broken():raise RuntimeError('TEST_SENSITIVE_EXCEPTION_DO_NOT_RECORD')
        out=run_with_quota(self.ledger,PROVIDER,ACCOUNT,broken,limit=1)
        self.assertEqual(out['call']['error_code'],'REQUEST_FAILED')
        self.assertNotIn('TEST_SENSITIVE_EXCEPTION',str(self.ledger.history()))


if __name__=='__main__':unittest.main()
