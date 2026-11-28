"""Read independently bound official J.League kickoff minutes for J1 review."""

from datetime import timezone
import gzip
from hashlib import sha256
from pathlib import Path

from research.analysis_freeze import aware, verify

from .nov25_first_review import official_binding, official_card, official_crosscheck


TRACK = 'j1-official-kickoff-binding'
EVIDENCE_ERRORS = (OSError, EOFError, KeyError, AttributeError, TypeError, ValueError)


def raw(root, ref, limit):
    path = (root / ref['raw_file']).resolve()
    if (not path.is_relative_to(root) or ref['http_status'] != 200
            or aware(ref['captured_at_utc']) > limit):
        raise ValueError('unavailable official capture or evidence clock outside the review')
    body = path.read_bytes()
    if body.startswith(b'\x1f\x8b'):
        body = gzip.decompress(body)
    if sha256(body).hexdigest() != ref['sha256']:
        raise ValueError('retained J.League response SHA-256 differs')
    return body


def bindings(packet, registered, fixtures, root, at):
    """Rebuild the pregame identity mapping from the original archived match cards."""
    root = Path(root).resolve()
    record = verify(packet['binding_record'])
    payload = record['payload']
    original_sha = registered['record_sha256']
    binding_scope = 'j1-official-kickoff:' + original_sha
    entries = payload['bindings']
    received = aware(record['recorded_at'])
    if (record['stage'] != 'prematch' or record['track'] != TRACK or record['revision'] != 1
            or record['evaluation_slot'] != 'FIRST_PREMATCH' or record['previous_sha256'] is not None
            or record['match_id'] != binding_scope or record['fixture']['match_id'] != binding_scope
            or payload['registration_sha256'] != original_sha or not entries or not entries.keys() <= fixtures.keys()
            or not packet['captures'].keys() <= entries.keys()
            or not aware(registered['recorded_at']) <= received <= at
            or received >= min(aware(fixtures[mid]['kickoff_at']) for mid in entries)):
        raise ValueError('expected the pregame J.League binding for the original registered fixtures')
    expected = {}
    urls = set()
    for mid, row in entries.items():
        identity = row['identity']
        source = row['source']
        try:
            paired = official_binding(raw(root, source, received), source, fixtures[mid], identity['names'])
            if paired != identity or paired['url'] in urls:
                raise ValueError('official card or one-to-one match identity differs')
        except EVIDENCE_ERRORS as exc:
            raise ValueError(f'{mid}: {exc}') from exc
        urls.add(paired['url'])
        expected[mid] = paired
    return expected


def compare_sources(captures, mapping, fixtures, root, at, exact, results):
    """Preserve exact clocks and use minute intervals where retained evidence supports them."""
    root = Path(root).resolve()
    starts, audit = dict(exact), {}
    for mid, refs in captures.items():
        try:
            fixture, binding = fixtures[mid], mapping[mid]
            pages = [official_card(raw(root, ref, at), ref, fixture, binding=binding) for ref in refs]
            if not pages:
                continue
            pages.sort(key=lambda row: aware(row['source']['captured_at_utc']))
            precise = dict(at=exact[mid]['actual_kickoff_at']) if mid in exact else None
            check = official_crosscheck(pages, dict(kickoff=precise), results.get(mid))
        except EVIDENCE_ERRORS as exc:
            raise ValueError(f'{mid}: {exc}') from exc
        interval = check['kickoff_interval']
        latest = pages[-1]
        issues = list(check['issues'])
        if interval and latest['state'] not in ('live', 'final'):
            issues.append('later official page no longer establishes actual play')
        if issues:
            starts.pop(mid, None)
            interval = None
        elif interval is not None and mid not in exact:
            times = {key: aware(interval[key]).astimezone(timezone.utc).isoformat()
                     for key in ('start_inclusive', 'end_exclusive')}
            starts[mid] = dict(match_id=mid, home=fixture['home'], away=fixture['away'], competition='jpn.1',
                               identity_verified=True, actual_kickoff_interval=times)
        audit[mid] = dict(sources=len(pages), official_state=latest['state'],
                          actual_kickoff_interval=interval, kickoff_source=check['kickoff_source'] if interval else None,
                          official_final=check['official_final'], issues=issues,
                          matched_original_schedule_minute=latest['ko_matches_schedule'])
    return starts, audit
