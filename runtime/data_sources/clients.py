"""Credential-safe read-only clients. A configured endpoint is not proven access."""
import hashlib
import json
import os
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, HTTPRedirectHandler, build_opener

PROVIDERS = {
    'bsd': {'base': 'https://sports.bzzoiro.com/api/v2',
            'env': 'WCPP_BSD_API_KEY', 'aliases': ['bsd'], 'header': 'Authorization',
            'prefix': 'Token ', 'probe': '/events/'},
    'openfoot': {'base': 'https://openfootapi.com/v1',
                 'env': 'WCPP_OPENFOOT_API_KEY', 'aliases': ['openfoot'], 'header': 'Authorization',
                 'prefix': 'Bearer ', 'probe': '/matches'},
    'apifootball-com': {'base': 'https://apiv3.apifootball.com',
                       'env': 'WCPP_APIFOOTBALLCOM_API_KEY',
                       'aliases': ['apifootball2'], 'query_key': 'APIkey',
                       'probe': '/'},
    '5dollar': {'base': 'https://api.5dollarfootballapi.com/v1',
               'env': 'WCPP_5DOLLAR_API_KEY', 'aliases': ['_5DOLLARS'], 'header': 'Authorization',
               'prefix': 'Bearer ', 'probe': '/status'},
    'the-odds-api': {'base': 'https://api.the-odds-api.com/v4',
                     'env': 'WCPP_THE_ODDS_API_KEY',
                     'aliases': ['THE_ODDS', 'manyzhishu_theodds_sports'],
                     'query_key': 'apiKey', 'probe': '/sports'},
    'espn': {'base': 'https://site.api.espn.com/apis/site/v2/sports/soccer',
             'public': True, 'probe': '/usa.1/scoreboard'},
    'fotmob': {'base': 'https://www.fotmob.com/api/data',
              'public': True, 'probe': '/leagues'},
    'football-data': {'base': 'https://api.football-data.org/v4',
                      'env': 'WCPP_FOOTBALL_DATA_API_KEY', 'header': 'X-Auth-Token',
                      'aliases': ['footballdata_org', 'football_data', 'Football_data_api'],
                      'probe': '/competitions'},
    'api-football': {'base': 'https://v3.football.api-sports.io',
                     'env': 'WCPP_API_FOOTBALL_KEY', 'header': 'x-apisports-key',
                     'aliases': ['APIFOOTBALL', '_apifootball', 'dashboard_api_football',
                                 'apifootball'],
                     'probe': '/status'},
    'therundown': {'base': 'https://therundown.io',
                   'env': 'WCPP_THERUNDOWN_API_KEY', 'aliases': ['therundown'],
                   'header': 'X-TheRundown-Key', 'probe': '/api/v2/markets'},
}
# Historical name is ambiguous between two distinct providers. Do not route it
# automatically, but still redact its value if echoed by another provider.
QUARANTINED_CREDENTIAL_NAMES = ('Apifootballcom_APIKEY',)
PRIVATE = {'key', 'api_key', 'apikey', 'token', 'authorization', 'access_token',
           'secret', 'x-auth-token', 'x-apisports-key', 'x-rapidapi-key',
           'x-therundown-key'}
ALLOWED = {
    'bsd': ('/events', '/leagues', '/teams', '/players'),
    'openfoot': ('/matches', '/competitions', '/standings', '/search'),
    'apifootball-com': ('/',),
    '5dollar': ('/status', '/fixtures', '/leagues'),
    'the-odds-api': ('/sports',),
    'espn': tuple('/' + x for x in ('eng.1', 'ger.1', 'esp.1', 'ita.1',
                  'fra.1', 'jpn.1', 'jpn.2', 'kor.1', 'aus.1', 'usa.1',
                  'afc.champions', 'afc.cup', 'uefa.champions')),
    'fotmob': ('/matchDetails', '/teams', '/leagues'),
    'football-data': ('/competitions', '/matches', '/teams', '/persons'),
    'api-football': ('/status', '/leagues', '/fixtures', '/standings', '/injuries',
                     '/players', '/teams', '/odds', '/timezone'),
    'therundown': ('/api/v2/markets', '/api/v2/sports', '/api/v2/affiliates'),
}


def scrub(value, secrets):
    if isinstance(value, dict):
        return {k: scrub(v, secrets) for k, v in value.items() if k.lower() not in PRIVATE}
    if isinstance(value, list): return [scrub(v, secrets) for v in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret: value = value.replace(secret, '[REDACTED]')
    return value


def response_metadata(response, secrets):
    """Retain safe diagnostics on success and failure; never retain error HTML."""
    headers = getattr(response, 'headers', {}) or {}
    lowered = {k.lower(): v for k, v in headers.items()}
    return scrub({
        'content_type': lowered.get('content-type'),
        'cf_ray': lowered.get('cf-ray'),
        'quota_headers': {k: v for k, v in lowered.items()
                          if k.startswith(('x-ratelimit', 'x-requests', 'retry-after'))},
    }, secrets)


def authentication_status(provider, path, success):
    # These catalog endpoints work without credentials; success does not prove
    # that a supplied key was checked. Add new public resources explicitly.
    if PROVIDERS[provider].get('public'):
        return 'PUBLIC_ENDPOINT'
    if provider == 'football-data' and path.rstrip('/') == '/competitions':
        return 'UNVERIFIED_PUBLIC_CATALOG'
    if provider == 'therundown' and path.rstrip('/') in ('/api/v2/sports', '/api/v2/affiliates'):
        return 'UNVERIFIED_PUBLIC_CATALOG'
    return 'CREDENTIAL_ACCEPTED' if success else 'UNVERIFIED'


def error_diagnostics(status, raw, content_type):
    """Classify observed errors, not their unknown upstream security rule.

    Return fixed labels only: error bodies can echo keys, IPs or account data.
    A CF-Ray header alone also occurs on normal API JSON responses.
    """
    try:
        data = json.loads(raw)
        fmt = 'JSON'
    except (ValueError, TypeError):
        data = None
        fmt = 'HTML' if b'<html' in raw.lower() or 'text/html' in (content_type or '').lower() else 'OTHER'
    kind = 'HTTP_ERROR'
    if status == 429:
        kind = 'RATE_LIMITED'
    elif fmt == 'HTML' and status == 403:
        body = raw.lower()
        branded_block = b'cloudflare' in body and (
            b'attention required' in body or b'cf-error-details' in body)
        kind = 'CLOUDFLARE_HTML_ACCESS_BLOCK' if branded_block else 'HTML_ACCESS_DENIED'
    elif fmt == 'JSON':
        kind = 'PROVIDER_JSON_REJECTION'
        fields = data if isinstance(data, dict) else {}
        errors = fields.get('errors') or fields.get('error') or fields.get('message') or ''
        error_keys = {k.lower() for k in errors} if isinstance(errors, dict) else set()
        detail = json.dumps(errors).lower()
        if status == 401 or error_keys & {'token', 'key', 'api_key', 'apikey', 'authentication'} or any(
                phrase in detail for phrase in ('invalid api key', 'invalid api-key', 'missing application key',
                                                'missing api key', 'expired api key')):
            kind = 'PROVIDER_AUTHENTICATION_REJECTION'
        elif error_keys & {'ratelimit', 'rate_limit'} or any(
                phrase in detail for phrase in ('too many requests', 'request limit', 'requests limit',
                                                'rate limit', 'quota exceeded')):
            kind = 'RATE_LIMITED'
        elif error_keys & {'plan', 'subscription', 'permissions'} or any(
                phrase in detail for phrase in ('plan_upgrade_required', 'subscription', 'entitlement')):
            kind = 'PROVIDER_PLAN_OR_PERMISSION_REJECTION'
    return {'response_format': fmt, 'failure_kind': kind}


def service_blocked(call):
    """Access rejection stops a batch even when the provider uses HTTP 200."""
    return (call.get('http_status') in (401, 403, 429)
            or call.get('error_code') == 'ENV_BINDING_MISSING'
            or call.get('failure_kind') in {'PROVIDER_AUTHENTICATION_REJECTION', 'RATE_LIMITED'})


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch(provider, path=None, params=None, opener=None, credential_binding=None):
    spec = PROVIDERS[provider]
    path = path or spec['probe']; params = params or {}
    if (not path.startswith('/') or any(c in path for c in ('?', '#', ':', '..', '//'))
            or not any(path == p or path.startswith(p + '/') for p in ALLOWED[provider])):
        raise ValueError('unsupported provider resource')
    if provider == 'apifootball-com' and params.get('action') not in (
            'get_events', 'get_leagues', 'get_countries', 'get_standings',
            'get_lineups', 'get_odds', 'get_H2H', 'get_players'):
        raise ValueError('explicit supported read-only APIfootball.com action required')
    if provider == '5dollar' and 'types' in params and params['types'] != 'jingcailottery':
        raise ValueError('lottery fixture type must be jingcailottery')
    if any(k.lower() in PRIVATE for k in params):
        raise ValueError('credentials must not be query parameters')
    all_keys = [os.environ.get(n) for s in PROVIDERS.values()
                for n in ([s['env']] if 'env' in s else []) + s.get('aliases', [])]
    all_keys.extend(os.environ.get(n) for n in QUARANTINED_CREDENTIAL_NAMES)
    if any(secret and secret in str(v) for secret in all_keys for v in [path, *params.values()]):
        raise ValueError('credential in query value')
    url = spec['base'] + path + ('?' + urlencode(params) if params else '')
    log = {'provider': provider, 'url': url, 'query_time': datetime.now(timezone.utc).isoformat(),
           'http_status': None, 'api_success': False, 'authenticated': False,
           'authentication_status': 'UNVERIFIED',
           'fixture_mapping_verified': False, 'model_ingestion_performed': False}
    bindings = ([spec['env']] if 'env' in spec else []) + spec.get('aliases',[])
    if credential_binding is not None and credential_binding not in bindings:
        raise ValueError('credential binding does not belong to this provider')
    credential_name = credential_binding or next((n for n in bindings if os.environ.get(n)),spec.get('env'))
    key = os.environ.get(credential_name) if credential_name else None
    log['credential_binding_name'] = credential_name
    if not key and not spec.get('public'):
        return {'call': dict(log, error_code='ENV_BINDING_MISSING'), 'response': None}
    try:
        headers = {'User-Agent': 'WCPP-research'}
        request_url = url
        if spec.get('query_key'):
            # Credentials travel to the fixed official host, never to the audit URL.
            request_url += ('&' if params else '?') + urlencode({spec['query_key']: key})
        elif spec.get('header'):
            headers[spec['header']] = spec.get('prefix', '') + key
        req = Request(request_url, headers=headers)
        with (opener or build_opener(NoRedirect()).open)(req, timeout=25) as response:
            if hasattr(response, 'geturl') and response.geturl().split('?')[0] != url.split('?')[0]:
                return {'call': dict(log, error_code='UNEXPECTED_REDIRECT'), 'response': None}
            log['http_status'] = response.status
            log.update(response_metadata(response, all_keys))
            raw = response.read(20_000_001)
            if len(raw) > 20_000_000: raise ValueError('response too large')
            parsed = json.loads(raw)
            if not isinstance(parsed, (dict, list)):
                raise ValueError('structured provider response required')
            # Error keys can also be sensitive names (notably errors.token).
            # Classify the untouched structure before redaction removes them.
            errors = (parsed.get('errors') or parsed.get('error') or parsed.get('errorCode')) if isinstance(parsed, dict) else None
            if provider == '5dollar' and (not isinstance(parsed, dict) or parsed.get('success') != 1):
                errors = True
            if errors:
                log.update(error_diagnostics(log['http_status'], raw, log.get('content_type')))
            data = scrub(parsed, all_keys)

        auth = authentication_status(provider, path, not bool(errors))
        log.update(api_success=not bool(errors), authenticated=auth == 'CREDENTIAL_ACCEPTED',
                   authentication_status=auth)
        if errors: log['error_code'] = 'PROVIDER_ERROR'
        log['response_sha256'] = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return {'call': log, 'response': data}
    except HTTPError as exc:
        log.update(http_status=exc.code, error_code='HTTP_ERROR')
        log.update(response_metadata(exc, all_keys))
        # Preserve only fixed diagnostic labels, not error text or account data.
        try:
            raw = exc.read(65536)
            log.update(error_diagnostics(exc.code, raw, log['content_type']))
        except (OSError, ValueError, AttributeError):
            log['response_format'] = 'UNKNOWN'
        finally:
            exc.close()
    except (URLError, TimeoutError, OSError): log['error_code'] = 'NETWORK_ERROR'
    except (ValueError, TypeError, AttributeError): log['error_code'] = 'INVALID_RESPONSE'
    return {'call': log, 'response': None}


def audit():
    # Retain the old bounded audit: adding an adapter must not spend every quota.
    return {p: fetch(p) for p in ('football-data', 'api-football')}


def match_context(provider, match_id):
    """Actual calls only; each field remains unmapped until identity/time verification."""
    if type(match_id) is not int or match_id <= 0: raise ValueError('provider numeric match ID required')
    if provider == 'football-data': return {'match': fetch(provider, f'/matches/{match_id}')}
    result, blocked_by = {}, None
    for name, path in (
        ('match', '/fixtures'), ('lineups', '/fixtures/lineups'),
        ('statistics', '/fixtures/statistics'), ('events', '/fixtures/events'),
        ('injuries', '/injuries'), ('odds', '/odds')):
        if blocked_by is not None:
            result[name] = {'call': {'provider': provider, 'api_success': False,
                            'error_code': 'BATCH_PROVIDER_BLOCKED', 'blocked_by': blocked_by},
                            'response': None}
            continue
        item = fetch(provider, path, {'fixture': match_id})
        result[name] = item
        call = item['call']
        if service_blocked(call):
            blocked_by = call.get('failure_kind') or call.get('error_code') or 'ACCESS_DENIED'
    return result
