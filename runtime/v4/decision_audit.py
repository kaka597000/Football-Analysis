"""Audit evidence coverage, not the truth or predictive value of a narrative."""
from urllib.parse import urlparse, parse_qsl
from model import aware
from competition_router import competition

CATEGORIES = ('form', 'process', 'motivation', 'format', 'lineup', 'load',
              'public_information', 'paths', 'anomalies')
API_COHORTS = ('jpn.1', 'jpn.2', 'eng.1', 'esp.1', 'ger.1', 'ita.1', 'fra.1')
VERSION = 'WCPP-DECISION-AUDIT-1'


def evidence_ok(e, fixture, category):
    u = urlparse(e.get('source_url', ''))
    if u.scheme != 'https' or not u.netloc or u.username or u.password:
        raise ValueError('HTTPS source required')
    if e.get('source_tier') not in ('L1', 'L2', 'L3'):
        raise ValueError('source tier required')
    if category not in e.get('supports_field', []):
        raise ValueError('source does not support category')
    query = aware(e['query_time'])
    if query > aware(fixture['cutoff_at']):
        raise ValueError('post-cutoff evidence')
    for key in ('known_at', 'source_published_at'):
        if e.get(key) and aware(e[key]) > query:
            raise ValueError('publication/knowledge after query')


def api_audit(fixture):
    """Status alone is not fixture data; unavailable feeds may use disclosed fallback."""
    code = competition(fixture.get('competition') or fixture['cohort'].split(':', 1)[0])
    required = code in API_COHORTS
    calls, invalid = [], []
    for raw in fixture.get('source_calls', []):
        if raw.get('provider') != '5DollarFootballAPI':
            continue
        try:
            u = urlparse(raw['url'])
            if (u.scheme != 'https' or u.netloc != 'api.5dollarfootballapi.com'
                    or not u.path.startswith('/v1/') or u.username or u.password or u.fragment):
                raise ValueError('invalid provider endpoint')
            if any(k.lower() in ('key', 'api_key', 'apikey', 'token', 'authorization')
                   for k, _ in parse_qsl(u.query)):
                raise ValueError('credential-bearing URL prohibited')
            if any(k.lower() in ('key', 'api_key', 'apikey', 'token', 'authorization', 'headers') for k in raw):
                raise ValueError('credential/header fields prohibited')
            if aware(raw['query_time']) > aware(fixture['cutoff_at']):
                raise ValueError('post-cutoff API call')
            if raw.get('match_id') != fixture['match_id']:
                raise ValueError('API call fixture mismatch')
            status = raw.get('http_status')
            if status is not None and (type(status) is not int or not 100 <= status <= 599):
                raise ValueError('invalid HTTP status')
            retrieved = raw.get('retrieved_fields', [])
            used = raw.get('analysis_fields', [])
            model = raw.get('model_input_fields', [])
            for fields in (retrieved, used, model):
                if not isinstance(fields, list) or any(not isinstance(x, str) or not x for x in fields):
                    raise ValueError('invalid field list')
            if not set(used + model) <= set(retrieved):
                raise ValueError('used field not retrieved')
            data_call = u.path != '/v1/status'
            success = status == 200 and raw.get('api_success') is True
            available = data_call and success and bool(retrieved) and raw.get('fixture_mapping_verified') is True
            calls.append({'url': raw['url'], 'query_time': raw['query_time'], 'http_status': status,
                          'data_call': data_call, 'data_available': available,
                          'retrieved_fields': retrieved, 'analysis_fields': used,
                          'model_input_fields': model,
                          'error_code': raw.get('error_code'),
                          'response_hash': raw.get('response_hash')})
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            # Do not echo input containing credentials into the audit output.
            invalid.append(type(exc).__name__ + ': ' + str(exc))
    data = [c for c in calls if c['data_call']]
    available = any(c['data_available'] for c in data)
    fallback = fixture.get('source_fallback') or {}
    disclosed = bool(fallback.get('reason') and fallback.get('source_urls'))
    if disclosed:
        disclosed = all(urlparse(u).scheme == 'https' and urlparse(u).netloc
                        for u in fallback['source_urls'])
    reasons = list(invalid)
    if required and not data:
        reasons.append('5Dollar fixture-data call missing; /status alone is insufficient')
    elif required and not available and not disclosed:
        reasons.append('5Dollar data unavailable/unmapped; fallback disclosure missing')
    return {'required': required, 'attempted_fixture_data': bool(data), 'data_available': available,
            'fallback_disclosed': bool(disclosed), 'calls': calls, 'reasons': reasons,
            'compliant': not reasons, 'execution_verified_by_audit': False,
            'limitation': 'Metadata audit only; verify saved response and fixture identity separately'}


def assess_decision(fixture):
    audit = fixture.get('decision_evidence') or {}
    blocks, reasons = {}, []
    for side in ('home', 'away'):
        blocks[side] = {}
        for category in CATEGORIES:
            try:
                b = audit[side][category]
                status = b.get('status')
                if status not in ('documented', 'unknown', 'conflict', 'not_applicable'):
                    raise ValueError('explicit evidence status required')
                if not isinstance(b.get('summary'), str) or not b['summary'].strip():
                    raise ValueError('summary/reason required')
                if status in ('unknown', 'conflict'):
                    raise ValueError(status + ': ' + b['summary'])
                if status == 'not_applicable' and category != 'public_information':
                    raise ValueError('category cannot be waived; record unknown instead')
                evidence = b.get('evidence') or []
                if not isinstance(evidence, list) or not evidence:
                    raise ValueError('source evidence required')
                for e in evidence:
                    evidence_ok(e, fixture, category)
                if category == 'paths' and not b.get('counterargument'):
                    raise ValueError('counterargument required for proposed path')
                if category == 'lineup' and (b.get('official_confirmed') is not True
                        or not any(e['source_tier'] == 'L1' for e in evidence)
                        or len(b.get('starters', [])) != 11 or len(set(b['starters'])) != 11):
                    raise ValueError('eleven unique official starters required')
                if category == 'process' and b.get('xg_origin') == 'narrative_inference':
                    raise ValueError('narrative-derived xG is not measured xG')
                blocks[side][category] = {'documented': True, 'status': status}
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                reason = side + '.' + category + ': ' + str(exc)
                reasons.append(reason)
                blocks[side][category] = {'documented': False, 'reason': reason}
    provider = api_audit(fixture)
    reasons.extend(provider['reasons'])
    return {'version': VERSION, 'blocks': blocks, 'reasons': reasons,
            'documented_blocks': sum(b['documented'] for side in blocks.values() for b in side.values()),
            'expected_blocks': 18, 'evidence_documentation_complete': not reasons,
            'source_call_audit': provider, 'numerical_fusion_validated': False,
            'comprehensive_primary_ready': False,
            'limitation': 'Source/time/coverage checks do not validate football reasoning or coefficients'}
