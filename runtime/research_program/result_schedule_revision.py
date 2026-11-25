"""Reconcile a revised ESPN schedule for an original, verified research issue.

evidence.schedule_revision supplies two retained {file, sha256} references:
scoreboard (the original pregame response) and queries (the acquisition log).
The ordinary result evidence points to the full-time summary. The result row
and forecast keep their registered kickoff. Only the final _actual call sees
the revised scheduled instant read from that summary.
"""

from datetime import timedelta
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from research.analysis_freeze import aware, verify
from .display_window_review import summary_kickoff
from .kpi_validation import _actual, fixture_identity


def _retained(root, ref):
    path = (root / ref['file']).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError('schedule evidence file missing or outside the evidence root')
    body = path.read_bytes()
    if hashlib.sha256(body).hexdigest() != ref['sha256']:
        raise ValueError('schedule evidence checksum mismatch')
    return body


def _original_board(root, revision, fixture, issued):
    board_ref = revision['scoreboard']
    body = _retained(root, board_ref)
    queries = json.loads(_retained(root, revision['queries']))
    matches = [q for q in queries if q.get('sha256') == board_ref['sha256']]
    if len(matches) != 1:
        raise ValueError('expected one retained query for the original scoreboard bytes')
    query = matches[0]
    league = fixture['cohort']
    url = urlsplit(query['url'])
    if (query['competition'] != league or query['status'] != 200 or query['bytes'] != len(body)
            or url.scheme != 'https' or url.hostname not in {'site.api.espn.com', 'site.web.api.espn.com'}
            or url.username is not None or url.password is not None or url.port not in (None, 443)
            or url.path != f'/apis/site/v2/sports/soccer/{league}/scoreboard'
            or parse_qs(url.query, keep_blank_values=True).get('dates') != [query['query_date']]
            or aware(query['query_observed_at']) > issued):
        raise ValueError('original scoreboard query provenance or acquisition time conflict')

    board = json.loads(body)
    event_id = fixture['match_id'].removeprefix('espn:')
    events = [e for e in board['events'] if str(e['id']) == event_id]
    if [item['slug'] for item in board['leagues']] != [league] or len(events) != 1:
        raise ValueError('original scoreboard league or event identity conflict')
    event = events[0]
    game, = event['competitions']
    original = aware(fixture['kickoff_at'])
    if (str(game['id']) != event_id or game.get('timeValid', True) is not True
            or aware(event['date']) != original or aware(game['date']) != original):
        raise ValueError('original scoreboard differs from registered event or schedule')
    for entry in (event, game):
        status = entry['status']['type']
        if status.get('state') != 'pre' or status.get('name') != 'STATUS_SCHEDULED' or status.get('completed') is not False:
            raise ValueError('original scoreboard must show the scheduled pregame state')
    teams = sorted((c['homeAway'], 'espn:' + str(c['team']['id'])) for c in game['competitors'])
    if teams != [('away', fixture['away']), ('home', fixture['home'])]:
        raise ValueError('original scoreboard home or away identity conflict')
    return event['season']


def _season_stage(season):
    year, stage = season['year'], season['type']
    if type(year) is not int or type(stage) is not int:
        raise ValueError('numeric ESPN season year and stage type required')
    return year, stage


def revised_actual(result, record, evidence_root, now):
    """Grade a flagged label via the strict path first, then an evidenced schedule revision."""
    fixture = record['fixture']
    try:
        return _actual(result, fixture, evidence_root, now)
    except ValueError as exc:
        # A fixture brought forward can finish before the original schedule + 90.
        if str(exc) not in ('retained kickoff conflict', 'invalid full-time observation time'):
            raise

    verify(record)
    if (record['stage'] != 'prematch' or type(record['revision']) is not int or record['revision'] != 1
            or record['previous_sha256'] is not None
            or record['evaluation_slot'] != 'FIRST_PREMATCH' or record['match_id'] != fixture['match_id']
            or fixture_identity(result) != fixture_identity(fixture)):
        raise ValueError('schedule reconciliation requires the registered, signed first forecast')
    root = Path(evidence_root).resolve()
    issued = aware(record['recorded_at'])
    evidence = result['evidence']
    try:
        before = _original_board(root, evidence['schedule_revision'], fixture, issued)
        body = _retained(root, evidence)
        header = json.loads(body)['header']
        game, = header['competitions']
        final = game['status']['type']
        if (final.get('state') != 'post' or final.get('name') != 'STATUS_FULL_TIME'
                or final.get('completed') is not True or _season_stage(before) != _season_stage(header['season'])):
            raise ValueError('full-time summary season, stage or final state conflict')
        revised = aware(game['date'])
        observed = aware(result['observed_at'])
        start = summary_kickoff(body, evidence['url'], dict(fixture, competition=fixture['cohort']), observed)
        original = aware(fixture['kickoff_at'])
        if (revised == original or not aware(fixture['cutoff_at']) <= issued < min(original, revised, start)
                or not start + timedelta(minutes=90) < observed <= now):
            raise ValueError('signed issue, actual kickoff or full-time capture chronology conflict')
    except (AttributeError, IndexError, KeyError, OSError, TypeError, ValueError) as exc:
        raise ValueError(f"schedule revision for {fixture['match_id']}: {exc}") from exc
    return _actual(dict(result, kickoff_at=game['date']), dict(fixture, kickoff_at=game['date']), root, now)
