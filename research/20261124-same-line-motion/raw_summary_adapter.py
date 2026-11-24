"""Strict ESPN summary adapter for a separately locked result-evidence index.

The caller must filter the index by frozen fixture ID and capture cutoff before
opening the raw response. This adapter only accepts exact event identity and a
completed two-period FULL_TIME result; AET and penalty-shootout summaries fail.
"""
from datetime import datetime, timedelta, timezone
import json
from urllib.parse import parse_qs, urlsplit


def _utc(value):
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('timezone required')
    return stamp.astimezone(timezone.utc)


def _goal(value):
    if isinstance(value, bool):
        raise ValueError('invalid score value')
    number = float(value)
    if not number.is_integer() or not 0 <= number <= 40:
        raise ValueError('invalid score value')
    return int(number)


def authenticate(raw_bytes, *, expected, source_url, captured_at, cutoff_at, evidence_sha256):
    """Authenticate one raw ESPN summary after caller has gated ID and clock."""
    import hashlib
    if hashlib.sha256(raw_bytes).hexdigest() != evidence_sha256:
        raise ValueError('raw evidence SHA mismatch')
    observed, cutoff = _utc(captured_at), _utc(cutoff_at)
    if observed >= cutoff or observed < _utc(expected['kickoff_at']) + timedelta(minutes=90):
        raise ValueError('raw capture outside allowed result window')
    parsed_url = urlsplit(source_url)
    query = parse_qs(parsed_url.query)
    if (parsed_url.scheme != 'https' or parsed_url.hostname != 'site.api.espn.com'
            or parsed_url.path != f"/apis/site/v2/sports/soccer/{expected['competition']}/summary"):
        raise ValueError('unsupported ESPN summary URL')
    expected_id = str(expected['id']).removeprefix('espn:')
    if query.get('event') != [expected_id]:
        raise ValueError('source URL event mismatch')
    summary = json.loads(raw_bytes)
    header = summary['header']
    if str(header['id']) != expected_id:
        raise ValueError('summary event ID mismatch')
    league = header.get('league', {}).get('slug')
    if league != expected['competition']:
        raise ValueError('summary competition mismatch')
    competitions = header['competitions']
    if len(competitions) != 1:
        raise ValueError('ambiguous summary competition')
    match = competitions[0]
    if str(match.get('id')) != expected_id:
        raise ValueError('summary competition event ID mismatch')
    status = match['status']['type']
    if status.get('completed') is not True or status.get('state') != 'post' or status.get('name') != 'STATUS_FULL_TIME':
        raise ValueError('summary is not completed regulation full time')
    if summary['format']['regulation']['periods'] != 2:
        raise ValueError('summary regulation period count is not two')
    kickoff = _utc(match['date'])
    if kickoff != _utc(expected['kickoff_at']):
        raise ValueError('summary kickoff mismatch')
    teams = match['competitors']
    if len(teams) != 2 or {t.get('homeAway') for t in teams} != {'home', 'away'}:
        raise ValueError('ambiguous home/away competitors')
    goals = {}
    for team in teams:
        side = team['homeAway']
        if expected[side + '_id'] != 'espn:' + str(team['team']['id']):
            raise ValueError('summary team identity mismatch')
        periods = team.get('linescores', [])
        if len(periods) != 2:
            raise ValueError('summary does not have exactly two regulation linescores')
        regulation = sum(_goal(p.get('value', p.get('displayValue'))) for p in periods)
        if _goal(team['score']) != regulation:
            raise ValueError('FT score conflicts with regulation linescores')
        goals[side] = regulation
    return {
        'id': expected['id'], 'source_event_ids': {'espn': expected_id},
        'home_id': expected['home_id'], 'away_id': expected['away_id'],
        'kickoff_at': expected['kickoff_at'], 'score': [goals['home'], goals['away']],
        'observed_at': captured_at, 'evidence_sha256': evidence_sha256,
        'verification': 'VERIFIED_RAW_ESPN_FULL_TIME_90',
    }
