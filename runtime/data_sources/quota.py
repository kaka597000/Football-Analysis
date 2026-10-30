"""Process-safe, credential-free reservations for explicitly configured free quotas.

No network request occurs here. History is append-only; account rows are a cache.
An unknown budget blocks automatic calls rather than assuming unlimited access.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sqlite3
import uuid

try:
    from .clients import PROVIDERS
except ImportError:
    from clients import PROVIDERS


def _clock(now):
    value = datetime.now(timezone.utc) if now is None else now
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError('timezone-aware UTC-compatible time required')
    return value.astimezone(timezone.utc)


def _period(now, period):
    if period == 'day':
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start.isoformat(), (start + timedelta(days=1)).timestamp()
    if period == 'month':
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        return start.isoformat(), end.timestamp()
    raise ValueError('supported quota periods are UTC day/month')


def _account(provider, account):
    """Only registered variable names are permitted, not arbitrary account text."""
    spec = PROVIDERS.get(provider)
    if not spec:
        raise ValueError('unknown provider')
    if spec.get('public'):
        if account != 'PUBLIC':
            raise ValueError('public account must use PUBLIC')
        return 'PUBLIC'
    names = [spec['env']] + spec.get('aliases', [])
    if account not in names:
        raise ValueError('registered credential binding name required')
    # A provider's configured aliases represent the same account unless a future
    # explicit multi-account policy proves otherwise. No alias quota splitting.
    return spec['env']


def _number(value, name, *, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError('nonnegative finite '+name+' required')
    if integer and type(value) is not int:
        raise ValueError('integer '+name+' required')
    return value


class QuotaLedger:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._transaction() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS policies(
                provider TEXT, account TEXT, period_kind TEXT NOT NULL,
                unit_limit INTEGER NOT NULL, locked_at TEXT NOT NULL,
                PRIMARY KEY(provider,account))''')
            c.execute('''CREATE TABLE IF NOT EXISTS accounts(
                provider TEXT, account TEXT, period_kind TEXT, period_start TEXT,
                reserved_units INTEGER NOT NULL, blocked_until REAL NOT NULL,
                provider_remaining INTEGER, PRIMARY KEY(provider,account,period_kind,period_start))''')
            c.execute('''CREATE TABLE IF NOT EXISTS events(
                event_id INTEGER PRIMARY KEY, recorded_at TEXT NOT NULL,
                reservation_id TEXT, provider TEXT NOT NULL, account TEXT NOT NULL,
                period_kind TEXT NOT NULL, period_start TEXT NOT NULL,
                phase TEXT NOT NULL, status TEXT NOT NULL, units INTEGER NOT NULL,
                next_allowed_at REAL, metadata TEXT NOT NULL)''')
            c.execute('CREATE INDEX IF NOT EXISTS quota_reservation ON events(reservation_id,phase)')
            c.execute('''CREATE TRIGGER IF NOT EXISTS quota_events_no_update
                BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT,'append-only quota history'); END''')
            c.execute('''CREATE TRIGGER IF NOT EXISTS quota_events_no_delete
                BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT,'append-only quota history'); END''')

    @contextmanager
    def _transaction(self):
        c = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        c.row_factory = sqlite3.Row
        try:
            c.execute('BEGIN IMMEDIATE')
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    @staticmethod
    def _event(c, now, rid, provider, account, period, start, phase, status, units, next_at=None, metadata=None):
        c.execute('INSERT INTO events(recorded_at,reservation_id,provider,account,period_kind,period_start,phase,status,units,next_allowed_at,metadata) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                  (now.isoformat(), rid, provider, account, period, start, phase, status, units,
                   next_at, json.dumps(metadata or {}, sort_keys=True, allow_nan=False)))

    def reserve(self, provider, account, *, limit=None, period='day',
                estimated_units=1, min_interval_seconds=0, now=None):
        now = _clock(now); account = _account(provider, account)
        _number(estimated_units, 'estimated units', integer=True)
        if estimated_units == 0:
            raise ValueError('positive estimated units required')
        _number(min_interval_seconds, 'interval')
        if limit is not None:
            _number(limit, 'limit', integer=True)
        start, end = _period(now, period); stamp = now.timestamp()
        rid = uuid.uuid4().hex
        with self._transaction() as c:
            policy = c.execute('SELECT * FROM policies WHERE provider=? AND account=?',
                               (provider, account)).fetchone()
            if policy is None and limit is not None:
                # Existing ledgers are pinned to their first explicit budget,
                # not silently granted a fresh policy after a software upgrade.
                locked_period, locked_limit, locked_at = period, limit, now.isoformat()
                for previous_event in c.execute("SELECT period_kind,metadata,recorded_at FROM events WHERE provider=? AND account=? AND phase='RESERVE' ORDER BY event_id", (provider, account)):
                    configured_limit = json.loads(previous_event['metadata']).get('configured_limit')
                    if configured_limit is not None:
                        locked_period, locked_limit, locked_at = previous_event['period_kind'], configured_limit, previous_event['recorded_at']
                        break
                c.execute('INSERT INTO policies VALUES(?,?,?,?,?)',
                          (provider, account, locked_period, locked_limit, locked_at))
                policy = c.execute('SELECT * FROM policies WHERE provider=? AND account=?',
                                   (provider, account)).fetchone()
            policy_conflict = (limit is not None and policy is not None
                               and (policy['period_kind'] != period or policy['unit_limit'] != limit))
            c.execute('INSERT OR IGNORE INTO accounts VALUES(?,?,?,?,0,0,NULL)', (provider, account, period, start))
            row = c.execute('SELECT * FROM accounts WHERE provider=? AND account=? AND period_kind=? AND period_start=?', (provider, account, period, start)).fetchone()
            previous = c.execute("SELECT MAX(next_allowed_at) FROM events WHERE provider=? AND account=? AND phase='RESERVE' AND status='RESERVED'", (provider, account)).fetchone()[0] or 0
            global_block = c.execute("SELECT MAX(next_allowed_at) FROM events WHERE provider=? AND account=? AND phase='SETTLE' AND status='HTTP_429'", (provider, account)).fetchone()[0] or 0
            blocked_until = max(global_block, row['blocked_until'])
            next_at = max(previous, blocked_until)
            status = ('QUOTA_UNKNOWN' if limit is None else
                      'POLICY_CONFLICT' if policy_conflict else
                      'CIRCUIT_OPEN' if stamp < blocked_until else
                      'RATE_LIMITED' if stamp < previous else
                      'QUOTA_EXHAUSTED' if row['reserved_units'] + estimated_units > limit
                           or row['provider_remaining'] is not None and row['provider_remaining'] < estimated_units else
                      'RESERVED')
            if status == 'RESERVED':
                next_at = stamp + min_interval_seconds
                c.execute('UPDATE accounts SET reserved_units=reserved_units+?,provider_remaining=CASE WHEN provider_remaining IS NULL THEN NULL ELSE provider_remaining-? END WHERE provider=? AND account=? AND period_kind=? AND period_start=?',
                          (estimated_units, estimated_units, provider, account, period, start))
            self._event(c, now, rid, provider, account, period, start, 'RESERVE', status,
                        estimated_units if status == 'RESERVED' else 0, next_at,
                        {'configured_limit': limit, 'estimated_units': estimated_units})
        return {'reservation_id': rid if status == 'RESERVED' else None,
                'status': status, 'allowed': status == 'RESERVED',
                'retry_after_seconds': max(0, next_at-stamp), 'period_end_utc': datetime.fromtimestamp(end,timezone.utc).isoformat()}

    def settle(self, reservation_id, *, http_status=None, quota_headers=None,
               actual_units=None, outcome='RETURNED', now=None):
        """Record results without returning possibly spent quota after a failure.

        Accepts only quota header names; bodies/errors/URLs/credentials are never
        persisted. Lower actual cost is recorded but reservation is not refunded.
        """
        now = _clock(now)
        if http_status is not None and (type(http_status) is not int or not 100 <= http_status <= 599):
            raise ValueError('valid HTTP status required')
        if outcome not in ('RETURNED','NETWORK_ERROR','TIMEOUT','INVALID_RESPONSE'):
            raise ValueError('fixed safe outcome code required')
        if actual_units is not None:
            _number(actual_units, 'actual units', integer=True)
        allowed = {'x-requests-last', 'x-requests-remaining', 'x-requests-used',
                   'x-ratelimit-requests-remaining', 'x-ratelimit-remaining', 'retry-after'}
        parsed = {}
        for key, value in (quota_headers or {}).items():
            name = key.lower()
            if name not in allowed:
                raise ValueError('only explicit quota headers may be recorded')
            try:
                number = float(value)
            except (TypeError, ValueError):
                raise ValueError('numeric quota header required') from None
            _number(number, 'quota header')
            parsed[name] = number
        if actual_units is None and 'x-requests-last' in parsed:
            value = parsed['x-requests-last']
            if not value.is_integer(): raise ValueError('integer actual quota units required')
            actual_units = int(value)
        with self._transaction() as c:
            event = c.execute("SELECT * FROM events WHERE reservation_id=? AND phase='RESERVE' AND status='RESERVED'",(reservation_id,)).fetchone()
            if event is None: raise ValueError('unknown reservation')
            if c.execute("SELECT 1 FROM events WHERE reservation_id=? AND phase='SETTLE'",(reservation_id,)).fetchone():
                raise ValueError('reservation already settled')
            provider, account, period, start = (event[k] for k in ('provider','account','period_kind','period_start'))
            _, period_end = _period(datetime.fromisoformat(start), period)
            extra = max(0, (actual_units or 0)-event['units'])
            # A 429 without a reset interval conservatively blocks this UTC period.
            blocked = (now.timestamp()+parsed['retry-after'] if http_status==429 and parsed.get('retry-after',0)>0
                       else max(now.timestamp(), period_end) if http_status==429 else 0)
            row = c.execute('SELECT * FROM accounts WHERE provider=? AND account=? AND period_kind=? AND period_start=?',(provider,account,period,start)).fetchone()
            remaining_values = [v for k,v in parsed.items() if k in ('x-requests-remaining','x-ratelimit-requests-remaining','x-ratelimit-remaining')]
            old_remaining = row['provider_remaining']
            # Never increase a remaining value based on a potentially out-of-order
            # concurrent response. Higher observation is retained in history only.
            remaining = min(remaining_values + ([max(0,old_remaining-extra)] if old_remaining is not None else [])) if remaining_values or old_remaining is not None else None
            c.execute('UPDATE accounts SET reserved_units=reserved_units+?,blocked_until=MAX(blocked_until,?),provider_remaining=? WHERE provider=? AND account=? AND period_kind=? AND period_start=?',
                      (extra, blocked, remaining, provider, account, period, start))
            self._event(c, now, reservation_id, provider, account, period, start, 'SETTLE',
                        'HTTP_429' if http_status==429 else outcome,
                        actual_units if actual_units is not None else event['units'], blocked,
                        {'http_status':http_status,'quota_headers':parsed,
                         'actual_cost_known':actual_units is not None,'reservation_not_refunded':True})
        return {'status':'SETTLED','actual_units':actual_units,'reservation_not_refunded':True}

    def history(self):
        with self._transaction() as c:
            return [dict(row) for row in c.execute('SELECT * FROM events ORDER BY event_id')]


def run_with_quota(ledger, provider, account, request, *, limit=None, period='day',
                   estimated_units=1, min_interval_seconds=0):
    """Execute an already-safe zero-argument fetcher iff reservation succeeds."""
    ticket = ledger.reserve(provider, account, limit=limit, period=period,
                            estimated_units=estimated_units, min_interval_seconds=min_interval_seconds)
    if not ticket['allowed']:
        return {'call':{'provider':provider,'api_success':False,
                        'error_code':ticket['status'],'quota_reservation':ticket},'response':None}
    try:
        result = request()
    except Exception:
        ledger.settle(ticket['reservation_id'], outcome='NETWORK_ERROR')
        # Do not echo arbitrary exception strings or request credentials.
        return {'call':{'provider':provider,'api_success':False,'error_code':'REQUEST_FAILED'},'response':None}
    if not isinstance(result, dict) or not isinstance(result.get('call'), dict):
        ledger.settle(ticket['reservation_id'], outcome='INVALID_RESPONSE')
        return {'call':{'provider':provider,'api_success':False,'error_code':'INVALID_RESPONSE'},'response':None}
    call = result['call']
    headers = {k:v for k,v in call.get('quota_headers', {}).items()
               if k.lower() in {'x-requests-last','x-requests-remaining','x-requests-used',
                   'x-ratelimit-requests-remaining','x-ratelimit-remaining','retry-after'}}
    try:
        code = call.get('error_code')
        outcome = ('NETWORK_ERROR' if code == 'NETWORK_ERROR' else
                   'TIMEOUT' if code == 'TIMEOUT' else
                   'INVALID_RESPONSE' if code == 'INVALID_RESPONSE' else 'RETURNED')
        ledger.settle(ticket['reservation_id'], http_status=call.get('http_status'),
                      quota_headers=headers, outcome=outcome)
    except ValueError:
        # Bad quota headers do not undo the reservation or expose provider text.
        status = call.get('http_status')
        if type(status) is not int or not 100 <= status <= 599:
            status = None
        ledger.settle(ticket['reservation_id'], http_status=status, outcome='INVALID_RESPONSE')
    return result
