"""Read authorized fixture feeds; never persist credentials or imply model ingestion."""
import hashlib
import json
import os
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = 'https://api.5dollarfootballapi.com/v1'
PRIVATE = {'key', 'api_key', 'apikey', 'token', 'authorization', 'access_token', 'secret'}


def scrub(value, key):
    if isinstance(value, dict):
        return {k: scrub(v, key) for k, v in value.items() if k.lower() not in PRIVATE}
    if isinstance(value, list):
        return [scrub(v, key) for v in value]
    if isinstance(value, str):
        return value.replace(key, '[REDACTED]') if key else value
    return value


def fetch_fixtures(league_id, start_time, end_time, page=1, opener=urlopen):
    """Existing verified /leagues/{id}/fixtures protocol; caller verifies identity/coverage."""
    if any(type(v) is not int or v < 0 for v in (league_id, start_time, end_time, page)):
        raise ValueError('integer league/time/page required')
    if not league_id or page < 1 or end_time <= start_time:
        raise ValueError('invalid league/window/page')
    key = os.environ.get('WCPP_5DOLLAR_API_KEY')
    calls = []

    def get(path):
        log = {'provider': '5DollarFootballAPI', 'url': BASE + path,
               'query_time': datetime.now(timezone.utc).isoformat(), 'http_status': None,
               'api_success': False, 'retrieved_fields': [], 'analysis_fields': [], 'model_input_fields': [],
               'fixture_mapping_verified': False}
        calls.append(log)
        if not key:
            log['error_code'] = 'ENV_BINDING_MISSING'
            return None
        try:
            request = Request(log['url'], headers={'Authorization': 'Bearer ' + key, 'User-Agent': 'WCPP-research'})
            with opener(request, timeout=25) as response:
                log['http_status'] = response.status
                data = scrub(json.loads(response.read()), key)
            log['api_success'] = data.get('success') == 1
            if not log['api_success']:
                log['error_code'] = 'API_SUCCESS_FLAG_FAILED'
            safe = json.dumps(data, ensure_ascii=False, sort_keys=True, allow_nan=False)
            log['response_hash'] = hashlib.sha256(safe.encode()).hexdigest()
            return data if log['api_success'] else None
        except HTTPError as exc:
            log.update(http_status=exc.code, error_code='HTTP_ERROR')
        except (URLError, TimeoutError, OSError):
            log['error_code'] = 'NETWORK_ERROR'
        except (ValueError, TypeError, AttributeError):
            log['error_code'] = 'INVALID_RESPONSE'
        return None

    status = get('/status')
    data = None
    if status is not None:
        query = urlencode({'start_time': start_time, 'end_time': end_time,
                           'status': 'all', 'per_page': 100, 'page': page})
        data = get('/leagues/' + str(league_id) + '/fixtures?' + query)
        if data and isinstance(data.get('data'), list):
            calls[-1]['retrieved_fields'] = sorted({k for row in data['data'] if isinstance(row, dict) for k in row})
    return {'provider': '5DollarFootballAPI', 'league_id': league_id, 'calls': calls,
            'status': status, 'response': data, 'fixture_mapping_verified': False,
            'model_ingestion_performed': False, 'pagination_complete': bool(data and not data.get('pagination', {}).get('has_more', True))}
