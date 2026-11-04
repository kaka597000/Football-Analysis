"""Read explicit ESPN venue state from retained, identity-checked responses.

No venue-name heuristics: missing, late or conflicting evidence is not a value.
"""
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from datetime import datetime, timezone


def _time(value):
    value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('timezone required')
    return value


def resolve(fixture, evidence, evidence_root):
    """Return VERIFIED, UNKNOWN or CONFLICT without modifying a fixture.

    Evidence metadata: file, sha256, url and actual query_time. Only ESPN
    summary/scoreboard records with the target identity are supported.
    """
    cutoff, kickoff = _time(fixture['cutoff_at']), _time(fixture['kickoff_at'])
    if cutoff >= kickoff:
        raise ValueError('prematch cutoff required')
    if not fixture['match_id'].startswith('espn:'):
        raise ValueError('ESPN identity required')
    event_id = fixture['match_id'].removeprefix('espn:')
    root = Path(evidence_root).resolve()
    observations, excluded = [], []
    for ref in evidence:
        url = urlsplit(ref['url'])
        if (url.scheme != 'https' or url.hostname not in ('site.api.espn.com', 'site.web.api.espn.com')
                or url.username or url.password or url.fragment):
            raise ValueError('unsupported source URL')
        query = parse_qs(url.query)
        if any(k.lower() in {'key','api_key','apikey','token','access_token','secret'} for k in query):
            raise ValueError('credential in evidence URL')
        if url.path.endswith('/summary'):
            if query.get('event') != [event_id]:
                raise ValueError('summary URL identity mismatch')
        elif not url.path.endswith('/scoreboard'):
            raise ValueError('unsupported ESPN endpoint')
        captured = _time(ref['query_time'])
        if captured > datetime.now(timezone.utc):
            raise ValueError('future evidence capture')
        if captured > cutoff:
            excluded.append('captured_after_cutoff')
            continue
        path = (root / ref['file']).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError('evidence path unavailable')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != ref['sha256']:
            raise ValueError('evidence checksum mismatch')
        doc = json.loads(raw)
        events = [doc['header']] if 'header' in doc else doc.get('events', [])
        matches = [e for e in events if str(e.get('id')) == event_id]
        if len(matches) != 1 or len(matches[0].get('competitions', [])) != 1:
            raise ValueError('event identity mismatch')
        event = matches[0]; competition = event['competitions'][0]
        event_kickoff = competition.get('date') or event.get('date')
        if not event_kickoff or _time(event_kickoff) != kickoff:
            raise ValueError('kickoff mismatch')
        for side in ('home', 'away'):
            teams = [c for c in competition.get('competitors', []) if c.get('homeAway') == side]
            if len(teams) != 1 or 'espn:' + str(teams[0]['team']['id']) != fixture[side]:
                raise ValueError('team identity mismatch')
        neutral = competition.get('neutralSite')
        if type(neutral) is not bool:
            excluded.append('neutral_state_missing')
            continue
        observations.append({'neutral': neutral, 'url': ref['url'], 'query_time': ref['query_time'],
                             'sha256': ref['sha256']})
    values = {r['neutral'] for r in observations}
    status = 'UNKNOWN' if not values else ('CONFLICT' if len(values) > 1 else 'VERIFIED')
    return {'status': status, 'neutral': next(iter(values)) if status == 'VERIFIED' else None,
            'observations': observations, 'excluded': excluded,
            'note': 'Explicit provider field only; venue name alone does not establish neutrality.'}
