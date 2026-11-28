"""Capture a registered J1 kickoff wave from ESPN and the bound official match cards."""

import argparse
from datetime import datetime, timezone
import gzip
from hashlib import sha256
from http.client import HTTPException
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener
from uuid import uuid4

from data_sources.clients import NoRedirect
from research.analysis_freeze import aware
from .display_window_review import _registered
from .goal_state_study import write_new
from .j1_window_intake import read
from .jleague_kickoff_proof import bindings


ESPN = 'https://site.api.espn.com/apis/site/v2/sports/soccer/jpn.1/summary?event='
BLOCKED = (401, 403, 429)
MAX_RESPONSE_BYTES = 5_000_000


def _now():
    return datetime.now(timezone.utc)


def _get(opener, url, content_type):
    ref = dict(url=url, requested_at_utc=_now().isoformat(), method='GET', http_status=None)
    body = None
    try:
        request = Request(url, headers={'Accept': content_type})
        with opener.open(request, timeout=15) as response:
            ref['http_status'] = response.status
            ref['content_type'] = response.headers.get('Content-Type')
            if response.headers.get_content_type() != content_type:
                raise ValueError('unexpected response content type')
            body = response.read(MAX_RESPONSE_BYTES + 1)
        if not body or len(body) > MAX_RESPONSE_BYTES:
            raise ValueError('empty or oversized response')
        if content_type == 'application/json':
            parsed = json.loads(body)
            if not isinstance(parsed, dict) or not isinstance(parsed.get('header'), dict):
                raise ValueError('response lacks an ESPN summary header')
    except HTTPError as error:
        ref.update(http_status=error.code, error='HTTP_ERROR')
        error.close()
    except (URLError, TimeoutError, OSError, HTTPException):
        ref['error'] = 'NETWORK_ERROR'
    except ValueError:
        ref['error'] = 'INVALID_RESPONSE'
    ref['captured_at_utc'] = _now().isoformat()
    return ref, body if 'error' not in ref else None


def _retain(staging, relative, ref, body, field):
    if body is not None:
        ref.update({field: relative, 'bytes': len(body), 'sha256': sha256(body).hexdigest()})
        stored = gzip.compress(body, mtime=0) if relative.endswith('.gz') else body
        (staging / relative).write_bytes(stored)
    return ref


def capture(registration, window, packet_path, root, scheduled, *, binding_root=None):
    """Run one read-only pass and expose its receipts only when the batch is complete."""
    started = _now()
    plan, universe, slates, _ = _registered(read(registration))
    packet = read(packet_path)
    mapping = bindings(packet, plan, universe, binding_root or packet_path.parent, started)
    selected = [mid for mid in slates[window] if aware(universe[mid]['kickoff_at']) == scheduled]
    if not selected or any(mid not in mapping or not re.fullmatch(r'espn:[0-9]+', mid) for mid in selected):
        raise ValueError('expected a kickoff wave with verified official bindings')

    root = root.resolve()
    published = root / 'captures'
    published.mkdir(parents=True, exist_ok=True)
    capture_id = started.strftime('%Y%m%dT%H%M%S%fZ') + '-' + uuid4().hex[:8]
    opener = build_opener(NoRedirect())
    blocked, receipts, espn, official = {}, {}, [], {}
    with TemporaryDirectory(prefix='j1-request-', dir=root) as scratch:
        staging = Path(scratch)
        (staging / 'raw').mkdir()
        for mid in sorted(selected):
            code = mapping[mid]['code']
            event = mid.removeprefix('espn:')
            receipt = {}
            if 'espn' not in blocked:
                ref, body = _get(opener, ESPN + event, 'application/json')
                ref.update(kind='summary', event_id=event, league_slug='jpn.1')
                receipt['espn'] = _retain(staging, f'raw/espn-{event}.json', ref, body, 'path')
                if body is not None or ref['http_status'] != 200:
                    espn.append(ref)
                if ref['http_status'] in BLOCKED:
                    blocked['espn'] = ref['http_status']
            if 'jleague' not in blocked:
                ref, body = _get(opener, mapping[mid]['url'], 'text/html')
                ref['code'] = code
                receipt['jleague'] = _retain(staging, f'raw/jleague-{code}.html.gz', ref, body, 'raw_file')
                if body is not None or ref['http_status'] != 200:
                    official[mid] = [ref]
                if ref['http_status'] in BLOCKED:
                    blocked['jleague'] = ref['http_status']
            receipts[mid] = receipt
        with (staging / 'espn.jsonl').open('x', encoding='utf-8') as stream:
            for ref in espn:
                stream.write(json.dumps(ref, ensure_ascii=False, sort_keys=True) + '\n')
        write_new(staging / 'official-captures.json', official)
        write_new(staging / 'capture.json', dict(window=window, registration=plan['freeze_id'],
                                                scheduled_at=scheduled.isoformat(), started_at=started.isoformat(),
                                                fixtures=receipts, blocked=blocked))
        destination = published / capture_id
        staging.rename(destination)
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('registration', 'binding', 'out'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--window', required=True)
    parser.add_argument('--binding-root', type=Path)
    parser.add_argument('--scheduled', required=True, help='the registered kickoff time with a timezone')
    args = parser.parse_args()
    print(capture(args.registration, args.window, args.binding, args.out, aware(args.scheduled),
                  binding_root=args.binding_root))
