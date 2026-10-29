"""API-first staging: deduplicate, cache, then emit only unresolved web tasks.

This collects evidence; it neither certifies a lineup nor changes model inputs.
Plans contain environment binding names, never credentials or arbitrary URLs.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time

try:
    from .clients import fetch, PROVIDERS, PRIVATE
except ImportError:
    from clients import fetch, PROVIDERS, PRIVATE


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False).encode()).hexdigest()


def instant(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('timezone required')
    return result


def pointer(value, path):
    """JSON Pointer; no expression execution or guessed schema aliases."""
    if not path:
        return value
    if not path.startswith('/'):
        raise ValueError('JSON Pointer required')
    try:
        for bit in path[1:].split('/'):
            bit = bit.replace('~1', '/').replace('~0', '~')
            value = value[int(bit)] if isinstance(value, list) else value[bit]
        return value
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def run(plan, cache_dir, fetcher=fetch, clock=None, sleeper=time.sleep):
    now = clock or (lambda: datetime.now(timezone.utc))
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    requests = plan['requests']
    fields = plan['fields']
    # Reject secret-bearing plans before saving or making any request.
    def safe_keys(value):
        if isinstance(value, dict):
            if any(k.lower() in PRIVATE for k in value):
                raise ValueError('credentials are forbidden in plans')
            for item in value.values():
                safe_keys(item)
        elif isinstance(value, list):
            for item in value:
                safe_keys(item)
    safe_keys(plan)
    encoded = json.dumps(plan, ensure_ascii=False)
    for provider in PROVIDERS.values():
        for binding in ([provider['env']] if 'env' in provider else []) + provider.get('aliases', []):
            secret = os.environ.get(binding)
            if secret and secret in encoded:
                raise ValueError('credential value is forbidden in plans')
    for spec in requests.values():
        if spec['provider'] not in PROVIDERS:
            raise ValueError('unsupported adapter; do not guess credential destination')
        if not 0 <= spec.get('ttl_seconds', 0) <= 86400 * 30:
            raise ValueError('invalid cache TTL')
    for field in fields:
        if not field.get('candidates'):
            raise ValueError('record applicable API attempts before web fallback')
        for candidate in field['candidates']:
            if candidate['request_id'] not in requests:
                raise ValueError('unknown request ID')
            if not candidate.get('identity_checks'):
                raise ValueError('explicit provider identity checks required')
    results, by_key, blocked, last_call = {}, {}, {}, {}

    def get(spec):
        provider = spec['provider']
        arguments = {k: spec[k] for k in ('provider', 'path', 'params', 'credential_binding') if k in spec}
        key = digest(arguments)
        if key in by_key:
            previous = by_key[key]
            if not previous.get('cache_hit'):
                return dict(previous, deduplicated=True)
            age = (now() - instant(previous['call']['query_time'])).total_seconds()
            if 0 <= age <= spec.get('ttl_seconds', 0):
                return dict(previous, deduplicated=True)
        target = cache / (key + '.json')
        try:
            saved = json.loads(target.read_text())
            item = saved['result']
            age = (now() - instant(item['call']['query_time'])).total_seconds()
            if (saved['sha256'] == digest(item) and item['call']['api_success']
                    and 0 <= age <= spec.get('ttl_seconds', 0)):
                item = dict(item, cache_hit=True)
                by_key[key] = item
                return item
        except (OSError, ValueError, TypeError, KeyError):
            pass  # Corrupt/stale caches are refetched, never trusted silently.
        if provider in blocked:
            item = {'call': {'provider': provider, 'api_success': False,
                    'error_code': 'BATCH_PROVIDER_BLOCKED',
                    'blocked_by': blocked[provider]}, 'response': None}
            by_key[key] = item
            return item
        if provider == '5dollar' and arguments.get('path') != '/status':
            status = get({'provider': provider, 'path': '/status', 'ttl_seconds': 300,
                          **({'credential_binding': arguments['credential_binding']}
                             if 'credential_binding' in arguments else {})})
            if not status['call']['api_success']:
                item = {'call': {'provider': provider, 'api_success': False,
                        'error_code': 'STATUS_CHECK_FAILED'}, 'response': None}
                by_key[key] = item
                return item
        # Independent providers parallelize; one provider is strictly serial.
        interval = 10 if provider == 'api-football' else 0.2
        delay = last_call.get(provider, 0) + interval - time.monotonic()
        if delay > 0:
            sleeper(delay)
        item = fetcher(**arguments)
        last_call[provider] = time.monotonic()
        item['cache_hit'] = False
        call = item['call']
        if call.get('http_status') in (401, 403, 429) or call.get('error_code') == 'ENV_BINDING_MISSING':
            blocked[provider] = call.get('error_code', 'ACCESS_DENIED')
        by_key[key] = item
        if call.get('api_success'):
            record = {'sha256': digest(item), 'result': item}
            # Hash-named evidence cache; output/freeze records remain append-only.
            tmp = cache / (key + '.' + str(time.time_ns()) + '.tmp')
            tmp.write_text(json.dumps(record, ensure_ascii=False, allow_nan=False))
            tmp.replace(target)
        return item

    def available(field, candidate):
        item = results.get(candidate['request_id'])
        if not item or not item['call'].get('api_success'):
            return False
        try:
            if (now() - instant(item['call']['query_time'])).total_seconds() < 0:
                return False
        except (ValueError, KeyError, AttributeError):
            return False
        body = item['response']
        if any(pointer(body, check['pointer']) != check['equals']
               for check in candidate['identity_checks']):
            return False
        if any(pointer(body, check['pointer']) != check['equals']
               for check in candidate.get('value_checks', [])):
            return False
        value = pointer(body, candidate['pointer'])
        if value is None or value == [] or value == {} or value == '':
            return False
        if field.get('requires_source_time'):
            try:
                age = (now() - instant(pointer(body, candidate['source_time_pointer']))).total_seconds()
                if not 0 <= age <= field['max_age_seconds']:
                    return False
            except (ValueError, TypeError, KeyError, AttributeError):
                return False
        return True

    def group_fetch(ids):
        return {rid: get(requests[rid]) for rid in ids}

    # Priority waves: do not spend secondary API quota for already filled fields.
    depth = max((len(f['candidates']) for f in fields), default=0)
    for index in range(depth):
        pending = {}
        for field in fields:
            if any(available(field, c) for c in field['candidates']):
                continue
            if index < len(field['candidates']):
                rid = field['candidates'][index]['request_id']
                if rid not in results:
                    pending.setdefault(requests[rid]['provider'], set()).add(rid)
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(group_fetch, sorted(ids)) for ids in pending.values()]
            for future in futures:
                results.update(future.result())
    evidence, gaps = [], []
    for field in fields:
        selected = next((c for c in field['candidates'] if available(field, c)), None)
        base = {'match_id': field['match_id'], 'field': field['field']}
        if selected:
            evidence.append(dict(base, status='API_EVIDENCE_PENDING_REVIEW',
                                 request_id=selected['request_id'], pointer=selected['pointer']))
        else:
            gaps.append(dict(base, assigned_model='gpt-6-luna',
                             candidate_checks=field['candidates'],
                             reason='APPLICABLE_APIS_EXHAUSTED_OR_EVIDENCE_INVALID',
                             constraints='仅查此缺项；保存来源与时间；禁止中国体彩及镜像'))
    return {'schema': 'WCPP-API-FIRST-1', 'created_at': now().isoformat(),
            'plan_sha256': digest(plan), 'requests': results,
            'supporting_requests': list(by_key.values()),
            'api_evidence': evidence, 'web_gap_tasks': gaps,
            'model_ingestion_performed': False, 'api_phase_complete': True,
            'note': '网页任务只在接口阶段结束后生成；证据仍需主代理核验。'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('plan')
    p.add_argument('--cache-dir', required=True)
    p.add_argument('--out', required=True)
    a = p.parse_args()
    target = Path(a.out)
    if target.exists():
        raise FileExistsError(a.out)
    result = run(json.loads(Path(a.plan).read_text()), a.cache_dir)
    with target.open('x') as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2, allow_nan=False)
    print(a.out)


if __name__ == '__main__':
    main()
