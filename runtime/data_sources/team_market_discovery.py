"""Find the nearest listed league matchday and join provider events with ESPN."""
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener

from automation.market_identity import scheduled_competition
from automation.market_snapshot import SCOPES, team_identity, validate_response_credentials
from data_sources.clients import NoRedirect
from research.analysis_freeze import aware

SUPPORTED = ('eng.1', 'bra.1')


def _fetch(url, relative, root, key=None):
    secret = urlencode({'apiKey': key}) if key else None
    destination = url if secret is None else url + ('&' if '?' in url else '?') + secret
    receipt = {'source_url': url, 'requested_at': datetime.now(timezone.utc).isoformat()}
    try:
        response = build_opener(NoRedirect).open(Request(destination), timeout=10)
    except HTTPError as error:
        response = error
    except (OSError, URLError) as error:
        return None, dict(receipt, status='NETWORK_ERROR', failure=type(error).__name__,
                          captured_at=datetime.now(timezone.utc).isoformat())
    with response:
        status = response.status
        raw = response.read(4_000_001)
        quota = {k: int(response.headers[k]) for k in ('x-requests-remaining', 'x-requests-last')
                 if (response.headers.get(k) or '').isdigit()}
    receipt.update(http_status=status, captured_at=datetime.now(timezone.utc).isoformat(), quota=quota)
    if status != 200 or len(raw) > 4_000_000:
        return None, dict(receipt, status='HTTP_ERROR' if status != 200 else 'RESPONSE_TOO_LARGE')
    try:
        values = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None, dict(receipt, status='INVALID_JSON')
    if key and key.encode() in raw:
        raise ValueError('discovery response echoed credential')
    validate_response_credentials(values, (key,) if key else ())
    with (root / relative).open('xb') as handle:
        handle.write(raw)
    return values, dict(receipt, status='OK', source_file=relative, source_sha256=hashlib.sha256(raw).hexdigest())


def _scheduled(board, competition):
    """Verify the league and coded season, as ESPN uses a season-specific slug in the EPL."""
    if not isinstance(board, dict) or not any(l.get('slug') == competition for l in board.get('leagues', [])):
        raise ValueError('discovery league identity mismatch')
    rows = []
    events = board.get('events', [])
    ids = Counter(e.get('id') for e in events)
    for e in events:
        status = e.get('status', {}).get('type', {})
        if ids[e.get('id')] != 1 or status.get('state') != 'pre' or status.get('completed') is not False:
            continue
        season = e.get('season', {})
        year = season.get('year')
        if not isinstance(year, int):
            continue
        slug = (f'{year}-{(year + 1) % 100:02d}-english-premier-league' if competition == 'eng.1'
                else f'{year}-brasileiro-serie-a')
        if season.get('slug') not in {SCOPES[competition][1], slug}:
            continue
        try:
            _, teams = scheduled_competition(e)
            rows.append((e, teams, aware(e['date'])))
        except (KeyError, ValueError, TypeError, AttributeError):
            continue
    return rows


def discover_targets(competition, days, directory, key, now_iso):
    """Discover listed games in the next days. Independently verify the nearest UTC matchday."""
    def read_source(url, filename, authenticated):
        return _fetch(url, filename, Path(directory), key if authenticated else None)

    return _discover(competition, days, now_iso, bool(key), read_source)


def _discover(competition, days, now_iso, enabled, read_source):
    if competition not in SUPPORTED or not isinstance(days, int) or days < 1:
        raise ValueError('discovery requires a supported league and positive horizon')
    sport = SCOPES[competition][0]
    start = aware(now_iso).astimezone(timezone.utc)
    end = start + timedelta(days=days)
    receipt = dict(competition=competition, sport_key=sport, starts_at=start.isoformat(), ends_at=end.isoformat(),
                   selection='nearest listed UTC matchday in the horizon', requests=[], exclusions={},
                   catalog_events=0, eligible_in_horizon=0, checked_on_nearest_day=0, identity_verified=0)
    if not enabled:
        return [], dict(receipt, status='MISSING_CREDENTIAL')
    url = f'https://api.the-odds-api.com/v4/sports/{sport}/events'
    catalog, meta = read_source(url, 'discovery-events.json', True)
    receipt['requests'].append(meta)
    if not isinstance(catalog, list):
        return [], dict(receipt, status='EVENT_CATALOG_UNAVAILABLE')
    receipt['catalog_events'] = len(catalog)
    ids = Counter(e.get('id') for e in catalog if isinstance(e, dict))
    eligible = []
    for e in catalog:
        try:
            when = aware(e['commence_time']).astimezone(timezone.utc)
            if (e['sport_key'] == sport and start < when <= end and ids[e['id']] == 1
                    and re.fullmatch(r'[0-9a-f]{32}', e['id']) and e['home_team'] and e['away_team']
                    and e['home_team'] != e['away_team']):
                eligible.append((when, e))
        except (TypeError, AttributeError, KeyError, ValueError):
            continue
    eligible.sort(key=lambda x: (x[0], x[1]['home_team'], x[1]['id']))
    receipt['eligible_in_horizon'] = len(eligible)
    if not eligible:
        return [], dict(receipt, status='NO_UPCOMING_EVENTS')
    day = eligible[0][0].date()
    selected = [(when, e) for when, e in eligible if when.date() == day]
    receipt.update(selected_utc_day=day.isoformat(), checked_on_nearest_day=len(selected))
    tag = day.strftime('%Y%m%d')
    url = f'https://site.api.espn.com/apis/site/v2/sports/soccer/{competition}/scoreboard?dates={tag}&limit=1000'
    board, board_meta = read_source(url, 'discovery-espn-' + tag + '.json', False)
    receipt['requests'].append(board_meta)
    if board is None:
        return [], dict(receipt, status='SCOREBOARD_UNAVAILABLE')
    try:
        scheduled = _scheduled(board, competition)
    except ValueError:
        return [], dict(receipt, status='SCOREBOARD_LEAGUE_MISMATCH')
    exclusions = {}
    candidates = []
    for when, e in selected:
        matches = [(row, teams) for row, teams, tee in scheduled if tee == when and
                   all(e[side + '_team'] == teams[side].get('shortDisplayName')
                       or team_identity(teams[side], e[side + '_team'], now_iso, competition) is not None
                       for side in ('home', 'away'))]
        if len(matches) != 1:
            exclusions[e['id']] = 'UNRESOLVED_OR_AMBIGUOUS_SCHEDULE_IDENTITY'
            continue
        event, sides = matches[0]
        candidates.append(dict(match_id='espn:' + event['id'], identity_verified=True, competition=competition,
                               cohort=competition, stage='regular', phase='PreMatch', age='senior', gender='men',
                               score_basis='regulation_90', kickoff_at=event['date'],
                               home='espn:' + sides['home']['id'], away='espn:' + sides['away']['id'],
                               home_name=sides['home']['displayName'], away_name=sides['away']['displayName'],
                               provider_match_id=e['id'], provider_home=e['home_team'], provider_away=e['away_team'],
                               sport_key=sport))
    matched_ids = Counter(f['match_id'] for f in candidates)
    for f in candidates:
        if matched_ids[f['match_id']] > 1:
            exclusions[f['provider_match_id']] = 'AMBIGUOUS_PROVIDER_EVENT'
    targets = [f for f in candidates if matched_ids[f['match_id']] == 1]
    receipt.update(identity_verified=len(targets), exclusions=exclusions)
    return targets, dict(receipt, status='VERIFIED_EVENTS' if targets else 'NO_VERIFIED_EVENTS')


def replay_discovery(directory, archived_receipt):
    """Reconstruct a successful discovery from its retained inputs and verify all issued targets."""
    root = Path(directory)
    receipt = archived_receipt
    start = aware(receipt['starts_at'])
    span = aware(receipt['ends_at']) - start
    if (receipt.get('status') != 'VERIFIED_EVENTS' or span != timedelta(days=span.days)
            or len(receipt.get('requests', [])) != 2):
        raise ValueError('expected a complete discovery run')
    requests = iter(receipt['requests'])
    prior_capture = start

    def read_source(url, filename, authenticated):
        nonlocal prior_capture
        meta = next(requests)
        # The online matching path supplies the filename and route; never open a path from metadata.
        if (meta.get('source_file') != filename or meta.get('source_url') != url
                or meta.get('http_status') != 200 or meta.get('status') != 'OK'
                or not prior_capture <= aware(meta['requested_at']) <= aware(meta['captured_at'])):
            raise ValueError('discovery source identity or capture time differs')
        prior_capture = aware(meta['captured_at'])
        raw = (root / filename).read_bytes()
        if hashlib.sha256(raw).hexdigest() != meta['source_sha256']:
            raise ValueError('discovery source checksum mismatch')
        return json.loads(raw), meta

    targets, reconstructed = _discover(receipt['competition'], span.days, receipt['starts_at'], True, read_source)
    if reconstructed != receipt or targets != json.loads((root / 'targets.json').read_bytes()):
        raise ValueError('archived discovery targets or summary differ from replay')
    return targets, reconstructed
