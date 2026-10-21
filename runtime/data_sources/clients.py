"""Credential-safe read-only clients. A configured endpoint is not proven access."""
import hashlib
import json
import os
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, HTTPRedirectHandler, build_opener

PROVIDERS = {
    'football-data': {'base': 'https://api.football-data.org/v4',
                      'env': 'WCPP_FOOTBALL_DATA_API_KEY', 'header': 'X-Auth-Token',
                      'aliases': ['football_data', 'Football_data_api'],
                      'probe': '/competitions'},
    'api-football': {'base': 'https://v3.football.api-sports.io',
                     'env': 'WCPP_API_FOOTBALL_KEY', 'header': 'x-apisports-key',
                     'aliases': ['_apifootball', 'dashboard_api_football',
                                 'Apifootballcom_APIKEY', 'apifootball'],
                     'probe': '/status'},
}
PRIVATE = {'key', 'api_key', 'apikey', 'token', 'authorization', 'access_token',
           'secret', 'x-auth-token', 'x-apisports-key', 'x-rapidapi-key'}
ALLOWED = {
    'football-data': ('/competitions', '/matches', '/teams', '/persons'),
    'api-football': ('/status', '/leagues', '/fixtures', '/standings', '/injuries',
                     '/players', '/teams', '/odds', '/timezone'),
}


def scrub(value, secrets):
    if isinstance(value, dict):
        return {k: scrub(v, secrets) for k, v in value.items() if k.lower() not in PRIVATE}
    if isinstance(value, list): return [scrub(v, secrets) for v in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret: value = value.replace(secret, '[REDACTED]')
    return value


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch(provider, path=None, params=None, opener=None, credential_binding=None):
    spec = PROVIDERS[provider]
    path = path or spec['probe']; params = params or {}
    if (not path.startswith('/') or any(c in path for c in ('?', '#', ':', '..', '//'))
            or not any(path == p or path.startswith(p + '/') for p in ALLOWED[provider])):
        raise ValueError('unsupported provider resource')
    if any(k.lower() in PRIVATE for k in params):
        raise ValueError('credentials must not be query parameters')
    all_keys = [os.environ.get(n) for s in PROVIDERS.values() for n in [s['env']]+s.get('aliases',[])] + [os.environ.get('WCPP_5DOLLAR_API_KEY')]
    if any(secret and secret in str(v) for secret in all_keys for v in params.values()):
        raise ValueError('credential in query value')
    url = spec['base'] + path + ('?' + urlencode(params) if params else '')
    log = {'provider': provider, 'url': url, 'query_time': datetime.now(timezone.utc).isoformat(),
           'http_status': None, 'api_success': False, 'authenticated': False,
           'fixture_mapping_verified': False, 'model_ingestion_performed': False}
    bindings = [spec['env']]+spec.get('aliases',[])
    if credential_binding is not None and credential_binding not in bindings:
        raise ValueError('credential binding does not belong to this provider')
    credential_name = credential_binding or next((n for n in bindings if os.environ.get(n)),spec['env'])
    key = os.environ.get(credential_name)
    log['credential_binding_name'] = credential_name
    if not key:
        return {'call': dict(log, error_code='ENV_BINDING_MISSING'), 'response': None}
    try:
        req = Request(url, headers={spec['header']: key, 'User-Agent': 'WCPP-research'})
        with (opener or build_opener(NoRedirect()).open)(req, timeout=25) as response:
            if hasattr(response, 'geturl') and response.geturl().split('?')[0] != url.split('?')[0]:
                return {'call': dict(log, error_code='UNEXPECTED_REDIRECT'), 'response': None}
            raw = response.read(20_000_001)
            if len(raw) > 20_000_000: raise ValueError('response too large')
            data = scrub(json.loads(raw), all_keys)
            log['http_status'] = response.status
            headers = getattr(response, 'headers', {})
            log['quota_headers'] = scrub({k: v for k, v in headers.items()
                                    if k.lower().startswith(('x-ratelimit', 'x-requests', 'retry-after'))}, all_keys)
        errors = data.get('errors') or data.get('error') or data.get('errorCode')
        log.update(api_success=not bool(errors), authenticated=not bool(errors))
        if errors: log['error_code'] = 'PROVIDER_ERROR'
        log['response_sha256'] = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return {'call': log, 'response': data}
    except HTTPError as exc:
        log.update(http_status=exc.code, error_code='HTTP_ERROR')
    except (URLError, TimeoutError, OSError): log['error_code'] = 'NETWORK_ERROR'
    except (ValueError, TypeError, AttributeError): log['error_code'] = 'INVALID_RESPONSE'
    return {'call': log, 'response': None}


def audit():
    return {p: fetch(p) for p in PROVIDERS}


def match_context(provider, match_id):
    """Actual calls only; each field remains unmapped until identity/time verification."""
    if type(match_id) is not int or match_id <= 0: raise ValueError('provider numeric match ID required')
    if provider == 'football-data': return {'match': fetch(provider, f'/matches/{match_id}')}
    return {name: fetch(provider, path, {'fixture': match_id}) for name, path in (
        ('match', '/fixtures'), ('lineups', '/fixtures/lineups'),
        ('statistics', '/fixtures/statistics'), ('events', '/fixtures/events'),
        ('injuries', '/injuries'), ('odds', '/odds'))}
