"""Bounded InferSports totals collection; evidence preparation, not model promotion.

Only independently matched Nations League A/C league-phase fixtures are supported.
Raw row ``as_of`` is the quote time. Response/capture timestamps never replace it.
The one-hour window matches the existing forward quote audit and is retained in
every snapshot; callers can still require a stricter window downstream.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

try:
    from runtime.automation.collection_status import classify_failure
    from runtime.v2_test.data_contract import aware, digest, public_url, safe_content, snapshot, validate
except ModuleNotFoundError:
    from automation.collection_status import classify_failure
    from v2_test.data_contract import aware, digest, public_url, safe_content, snapshot, validate

BASE = 'https://api.infersports.dev'
ESPN = 'https://site.api.espn.com/apis/site/v2/sports/soccer/uefa.nations/scoreboard'
MAX_AGE_SECONDS = 3600
# These IDs/names were observed together, not inferred from a league-name search.
LEAGUES = {'lg_BEJAVP29': ('UEFANLA', 'A'), 'lg_FDN4KCTA': ('UEFANLC', 'C')}
BOOKMAKERS = frozenset({'crown', 'hkjc', 'm8bet', 'macau', 'nova88', 'sbobet'})
ALIAS_REVIEW = {'version': 'infer-nations-turkey-20261112-1',
                'reviewed_at': '2026-11-12T13:43:21.438909+00:00',
                'espn_id': '465', 'espn_name': 'Türkiye', 'provider_name': 'Turkey'}


def team_identity(team, name, cutoff):
    """Exact names, plus the explicitly reviewed national-team alias and ID."""
    rule = ALIAS_REVIEW
    if name in (rule['provider_name'], rule['espn_name']) or team.get('displayName') in (
            rule['provider_name'], rule['espn_name']):
        return (team.get('id') == rule['espn_id'] and team.get('displayName') == rule['espn_name']
                and (name == rule['espn_name'] or name == rule['provider_name']
                     and aware(rule['reviewed_at']) <= aware(cutoff)))
    return bool(name) and name == team.get('displayName')


def read_evidence(root, meta, *, cutoff, expected):
    """Verify local raw bytes, safe canonical source and actual capture cutoff."""
    safe_content(meta)
    public_url(meta['url'])
    url = urlsplit(meta['url'])
    if url.scheme + '://' + url.netloc + url.path != expected:
        raise ValueError('unexpected evidence endpoint')
    relative = Path(meta['file'])
    path = (Path(root) / relative).resolve()
    if relative.is_absolute() or not path.is_relative_to(Path(root).resolve()):
        raise ValueError('evidence outside retained root')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != meta['sha256']:
        raise ValueError('source evidence checksum mismatch')
    if aware(meta['captured_at']) > aware(cutoff):
        raise ValueError('source capture after cutoff')
    if meta.get('requested_at') and aware(meta['requested_at']) > aware(meta['captured_at']):
        raise ValueError('request after capture')
    return json.loads(raw)


def eligible_targets(board, *, cutoff, now, fixtures=None):
    if not any(x.get('slug') == 'uefa.nations' for x in board.get('leagues', [])):
        raise ValueError('Nations League independent identity required')
    selected = None
    if fixtures is not None:
        selected = {f['match_id']: f for f in fixtures}
        if len(selected) != len(fixtures):
            raise ValueError('duplicate requested fixture')
    eligible, excluded = [], Counter()
    ids = Counter(str(e.get('id')) for e in board.get('events', []))
    for event in board.get('events', []):
        mid = 'espn:' + str(event.get('id', ''))
        if selected is not None and mid not in selected:
            continue
        try:
            status = event.get('status', {}).get('type', {})
            if (ids[str(event['id'])] != 1 or status.get('state') != 'pre'
                    or status.get('completed') is not False or aware(event['date']) <= now):
                raise ValueError('not unique future scheduled fixture')
            if event.get('season', {}).get('slug') != 'league-phase':
                raise ValueError('unverified phase')
            if len(event['competitions']) != 1:
                raise ValueError('ambiguous competition')
            competition = event['competitions'][0]
            if ('id' in competition and str(competition['id']) != str(event['id'])
                    or 'date' in competition and aware(competition['date']) != aware(event['date'])):
                raise ValueError('conflicting competition identity')
            comp_status = competition.get('status', {}).get('type')
            if comp_status is not None and (comp_status.get('state') != 'pre' or comp_status.get('completed') is not False):
                raise ValueError('conflicting competition status')
            group = re.fullmatch(r'Group ([AC])[1-4]', competition.get('group', {}).get('name', ''))
            if not group:
                raise ValueError('unverified grade')
            grade = group[1]
            competitors = competition['competitors']
            sides = {x['homeAway']: x['team'] for x in competitors}
            if (len(competitors) != 2 or set(sides) != {'home', 'away'}
                    or sides['home']['id'] == sides['away']['id']):
                raise ValueError('ambiguous teams')
            target = {'match_id': mid, 'competition': 'uefa.nations',
                      'cohort': f'uefa.nations:{grade}:league-phase', 'stage': 'league-phase',
                      'home_id': 'espn:' + sides['home']['id'],
                      'away_id': 'espn:' + sides['away']['id'],
                      'kickoff_at': event['date'], 'cutoff_at': cutoff}
            if selected is not None:
                f = selected[mid]
                for field in ('competition', 'cohort', 'stage'):
                    if f.get(field) != target[field]:
                        raise ValueError('requested fixture identity mismatch')
                for side in ('home', 'away'):
                    if f.get(side + '_id', f.get(side)) != target[side + '_id']:
                        raise ValueError('requested team identity mismatch')
                if aware(f['kickoff_at']) != aware(target['kickoff_at']):
                    raise ValueError('requested kickoff mismatch')
            eligible.append((target, sides, grade))
        except (KeyError, TypeError, ValueError):
            excluded['unverified_target_identity_or_phase'] += 1
    if selected is not None:
        excluded['requested_fixture_absent_from_identity'] += len(set(selected) - {
            'espn:' + str(e.get('id', '')) for e in board.get('events', [])})
    return eligible, excluded


def matched_events(events, target, sides, grade, cutoff):
    found = []
    ids = Counter(e.get('id') for e in events)
    for event in events:
        try:
            league = event['league']
            if (ids[event['id']] != 1 or event.get('sport') != 'football' or event.get('status') != 'scheduled'
                    or LEAGUES.get(league['id']) != (league['name'], grade)
                    or aware(event['scheduled_at']) != aware(target['kickoff_at'])):
                continue
            if all(team_identity(sides[s], event[s + '_team']['name'], cutoff) for s in ('home', 'away')):
                found.append(event)
        except (KeyError, TypeError, ValueError):
            continue
    return found


def build(root, events_meta, odds_meta, identity_meta, *, cutoff, now=None, fixtures=None):
    """Build from direct provider responses; odds_meta is one metadata object or a list."""
    now = now or datetime.now(timezone.utc)
    if aware(cutoff) > now:
        raise ValueError('future cutoff')
    events_response = read_evidence(root, events_meta, cutoff=cutoff, expected=BASE + '/v1/events')
    board = read_evidence(root, identity_meta, cutoff=cutoff, expected=ESPN)
    events = events_response['data']
    if not isinstance(events, list):
        raise ValueError('events list required')
    eligible, excluded = eligible_targets(board, cutoff=cutoff, now=now, fixtures=fixtures)
    odds_by_event = defaultdict(list)
    for meta in [odds_meta] if isinstance(odds_meta, dict) else odds_meta:
        route = re.fullmatch(r'/v1/events/(evt_[A-Za-z0-9]+)/odds', urlsplit(meta['url']).path)
        if not route:
            raise ValueError('unverified odds endpoint')
        response = read_evidence(root, meta, cutoff=cutoff, expected=BASE + route[0])
        if response.get('event_id') != route[1]:
            raise ValueError('odds response identity mismatch')
        odds_by_event[route[1]].append((response, meta))
    snapshots, mappings = [], []
    for target, sides, grade in eligible:
        matches = matched_events(events, target, sides, grade, cutoff)
        if len(matches) != 1:
            excluded['missing_or_ambiguous_provider_identity'] += 1
            continue
        source = matches[0]
        responses = odds_by_event.get(source['id'], [])
        if len(responses) != 1:
            excluded['missing_or_ambiguous_odds_response'] += 1
            continue
        response, meta = responses[0]
        if response.get('stale') is not False:
            excluded['provider_response_stale_or_unknown'] += 1
            continue
        rows = []
        for quote in response.get('odds', []):
            if (quote.get('market_type') != 'totals' or quote.get('period') != 'full_time'
                    or quote.get('format') != 'decimal' or quote.get('status') != 'open'):
                excluded['unsupported_market_period_or_status'] += 1
                continue
            if quote.get('bookmaker') not in BOOKMAKERS:
                excluded['unreviewed_or_aggregate_bookmaker'] += 1
                continue
            value = {'bookmaker': quote['bookmaker'], 'market': 'totals', 'line': quote.get('line'),
                     'odds': quote.get('prices'), 'quoted_at': quote.get('as_of')}
            row = {'provider': 'infersports', 'cohort': target['cohort'],
                   'match_id': target['match_id'], 'entity_id': target['home_id'],
                   'field': 'market_quote', 'period': 'regulation_90',
                   'event_kickoff_at': target['kickoff_at'], 'observed_at': meta['captured_at'],
                   'provider_match_id': source['id'], 'value': value,
                   'identity_evidence': identity_meta, 'provider_event_evidence': events_meta,
                   'evidence': {'file': meta['file'], 'sha256': meta['sha256'], 'url': meta['url'],
                                'query_time': meta['captured_at'], 'identity_verified': True}}
            if source['home_team']['name'] == 'Turkey' or source['away_team']['name'] == 'Turkey':
                row['identity_alias_review'] = dict(ALIAS_REVIEW, sha256=digest(ALIAS_REVIEW))
            try:
                validate(row, root, now=now)
                line = value['line']
                for side in ('over', 'under'):
                    if side + '_line' in quote and quote[side + '_line'] != line:
                        raise ValueError('different side lines')
                components = quote.get('handicap_components')
                if components is not None and (line * 2 == round(line * 2) or components != [line - .25, line + .25]):
                    raise ValueError('different split-line components')
            except (ValueError, KeyError, TypeError):
                excluded['invalid_quote_or_timestamp'] += 1
                continue
            rows.append(row)
        groups = defaultdict(dict)
        for row in rows:
            value = row['value']
            group = groups[(value['bookmaker'], value['line'])]
            signature = digest(value)
            if signature in group:
                excluded['duplicate_quote'] += 1
            group[signature] = row
        rows = []
        for group in groups.values():
            if len(group) != 1:
                excluded['conflicting_market_group'] += 1
            else:
                rows.extend(group.values())
        snapshots.append(snapshot(rows, target, root, provider='infersports',
                                  market_max_age_seconds=MAX_AGE_SECONDS, now=now))
        mappings.append({'match_id': target['match_id'], 'provider_match_id': source['id'],
                         'provider_league': source['league'], 'cohort': target['cohort'],
                         'identity_evidence_sha256': identity_meta['sha256'],
                         'provider_event_evidence_sha256': events_meta['sha256']})
    count = sum(len(s['observations']) for s in snapshots)
    covered = sum(bool(s['observations']) for s in snapshots)
    return {'schema': 'WCPP-MARKET-CHANNEL-1', 'created_at': now.isoformat(),
            'provider': 'infersports', 'market_model_ready': count > 0,
            'verified_quote_count': count, 'eligible_fixture_count': len(eligible),
            'covered_fixture_count': covered, 'coverage': covered / len(eligible) if eligible else None,
            'coverage_scope': 'explicit requested fixtures' if fixtures is not None else 'ESPN Nations League A/C league-phase future fixtures',
            'provider_events_page_truncated': bool(events_response.get('page', {}).get('next_cursor')),
            'source_odds_events': len(odds_by_event), 'market_max_age_seconds': MAX_AGE_SECONDS,
            'excluded': dict(excluded), 'identity_mappings': mappings, 'snapshots': snapshots,
            'quote_time_inferred_from_capture': False, 'tls_verification_disabled': False,
            'active_model_changed': False}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch(url, path, *, credential=None):
    """Fixed HTTPS hosts, verified default TLS, no credential-bearing redirects."""
    public_url(url)
    parsed = urlsplit(url)
    allowed = (parsed.netloc == 'api.infersports.dev' and (
        parsed.path == '/v1/events' or re.fullmatch(r'/v1/events/evt_[A-Za-z0-9]+/odds', parsed.path)))
    if not allowed and (parsed.scheme + '://' + parsed.netloc + parsed.path != ESPN or credential):
        raise ValueError('unapproved collection endpoint')
    headers = {'Accept': 'application/json'}
    if credential:
        headers['Authorization'] = 'Bearer ' + credential
    requested = datetime.now(timezone.utc).isoformat()
    with build_opener(NoRedirect()).open(Request(url, headers=headers), timeout=15) as response:
        body = response.read(2 * 1024 * 1024 + 1)
    if len(body) > 2 * 1024 * 1024 or credential and credential.encode() in body:
        raise ValueError('unsafe or oversized response')
    safe_content(json.loads(body))
    captured = datetime.now(timezone.utc).isoformat()
    with Path(path).open('xb') as handle:
        handle.write(body)
    return {'file': Path(path).name, 'sha256': hashlib.sha256(body).hexdigest(),
            'url': url, 'requested_at': requested, 'captured_at': captured}


def write(path, value):
    with Path(path).open('x', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)


def collect(out, fixtures, *, limit=50, max_events=8):
    """One bounded schedule page and at most max_events exact target requests.

    No retries, subscriptions, secret installation or registry changes. Fixtures
    restrict the scope; the live ESPN response must independently confirm them.
    """
    if type(limit) is not int or not 1 <= limit <= 100 or type(max_events) is not int or not 1 <= max_events <= 10:
        raise ValueError('bounded limits required')
    if not isinstance(fixtures, list) or not fixtures:
        raise ValueError('explicit fixture list required')
    days = {aware(f['kickoff_at']).astimezone(timezone.utc).strftime('%Y%m%d') for f in fixtures}
    if len(days) != 1:
        raise ValueError('single UTC date per collection required')
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    credential = os.environ.get('WCPP_INFERSPORTS_KEY') or os.environ.get('infer_yapan_zhishu')
    metadata, failures, requests = {}, [], 0
    result = {'provider': 'infersports', 'market_model_ready': False, 'verified_quote_count': 0,
              'status': 'MISSING_CREDENTIAL', 'requests': 0, 'active_model_changed': False}
    if credential:
        try:
            requests += 1
            identity = fetch(ESPN + '?' + urlencode({'dates': next(iter(days)), 'limit': 1000}), out / 'identity.json')
            metadata['identity'] = identity
            requests += 1
            events_meta = fetch(BASE + '/v1/events?' + urlencode({'sport': 'football', 'status': 'scheduled', 'limit': limit}),
                                out / 'events.json', credential=credential)
            metadata.update(events=events_meta, odds=[])
            now = datetime.now(timezone.utc)
            board = read_evidence(out, identity, cutoff=now.isoformat(), expected=ESPN)
            events = read_evidence(out, events_meta, cutoff=now.isoformat(), expected=BASE + '/v1/events')['data']
            targets, _ = eligible_targets(board, cutoff=now.isoformat(), now=now, fixtures=fixtures)
            requested_ids = []
            for target, sides, grade in targets:
                matches = matched_events(events, target, sides, grade, now.isoformat())
                if len(matches) == 1 and matches[0]['id'] not in requested_ids:
                    requested_ids.append(matches[0]['id'])
            for event_id in requested_ids[:max_events]:
                try:
                    requests += 1
                    meta = fetch(BASE + '/v1/events/' + event_id + '/odds?' + urlencode({'markets': 'totals', 'format': 'decimal'}),
                                 out / (event_id + '-odds.json'), credential=credential)
                    metadata['odds'].append(meta)
                except Exception as exc:
                    failure = classify_failure(exc)
                    failures.append(dict(failure, provider_match_id=event_id))
                    if failure.get('http_status') in {401, 403, 429}:
                        break
            now = datetime.now(timezone.utc)
            result = build(out, events_meta, metadata['odds'], identity, cutoff=now.isoformat(), now=now, fixtures=fixtures)
            result['request_budget_truncated'] = len(requested_ids) > max_events
            result['status'] = ('PARTIAL_VALIDATED' if failures else 'VALIDATED') if result['market_model_ready'] else (
                'COLLECTION_FAILED' if failures else 'NO_VALID_QUOTES')
        except Exception as exc:
            failures.append(classify_failure(exc))
            result['status'] = 'COLLECTION_FAILED'
    result.update(requests=requests, failures=failures)
    write(out / 'source-metadata.json', metadata)
    write(out / 'market-channel.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True)
    parser.add_argument('--fixtures', required=True, help='Explicit fixture JSON list; identities are independently rechecked')
    parser.add_argument('--limit', type=int, default=50)
    parser.add_argument('--max-events', type=int, default=8)
    args = parser.parse_args()
    result = collect(args.out, json.loads(Path(args.fixtures).read_text()), limit=args.limit, max_events=args.max_events)
    print(json.dumps({k: v for k, v in result.items() if k not in {'snapshots', 'identity_mappings'}}))
