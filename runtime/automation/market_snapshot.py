"""Bounded Nations League market collection with retained identity/time evidence.

This is a data channel, not an active-model or staking change. Only exact team
names, home/away order and kickoff matches are admitted; aliases remain missing.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

try:
    from runtime.automation.collection_status import classify_failure
    from runtime.v2_test.data_contract import aware, snapshot, validate
except ModuleNotFoundError:
    from automation.collection_status import classify_failure
    from v2_test.data_contract import aware, snapshot, validate

ODDS = 'https://api.the-odds-api.com/v4/sports/soccer_uefa_nations_league/odds/'
ESPN = 'https://site.api.espn.com/apis/site/v2/sports/soccer/uefa.nations/scoreboard'
MAX_AGE_SECONDS = 60


def write(path, value):
    with Path(path).open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)


def check_evidence(root, metadata):
    path = (Path(root) / metadata['file']).resolve()
    if not path.is_relative_to(Path(root).resolve()):
        raise ValueError('evidence outside retained root')
    if hashlib.sha256(path.read_bytes()).hexdigest() != metadata['sha256']:
        raise ValueError('source evidence checksum mismatch')
    return json.loads(path.read_text())


def build(root, odds_meta, identity_meta, *, cutoff, now=None):
    now = now or datetime.now(timezone.utc)
    odds, board = (check_evidence(root, m) for m in (odds_meta, identity_meta))
    if not any(x.get('slug') == 'uefa.nations' for x in board.get('leagues', [])):
        raise ValueError('Nations League source identity required')
    if max(aware(m['captured_at']) for m in (odds_meta, identity_meta)) > aware(cutoff):
        raise ValueError('identity and quote capture must precede cutoff')
    eligible = []
    for event in board.get('events', []):
        status = event.get('status', {}).get('type', {})
        if status.get('state') != 'pre' or status.get('completed') is not False:
            continue
        if event.get('season', {}).get('slug') != 'league-phase' or aware(event['date']) <= now:
            continue
        sides = {x['homeAway']: x['team'] for x in event['competitions'][0]['competitors']}
        if set(sides) != {'home', 'away'} or sides['home']['id'] == sides['away']['id']:
            continue
        eligible.append((event, sides))
    snapshots, excluded, mappings = [], Counter(), []
    for event, sides in eligible:
        matched = [o for o in odds if o.get('sport_key') == 'soccer_uefa_nations_league'
                   and o.get('home_team') == sides['home']['displayName']
                   and o.get('away_team') == sides['away']['displayName']
                   and aware(o['commence_time']) == aware(event['date'])]
        if len(matched) != 1:
            excluded['missing_or_ambiguous_identity'] += 1
            continue
        source = matched[0]
        target = {'cohort': 'uefa.nations', 'match_id': 'espn:' + event['id'],
                  'home_id': 'espn:' + sides['home']['id'], 'away_id': 'espn:' + sides['away']['id'],
                  'kickoff_at': event['date'], 'cutoff_at': cutoff}
        rows = []
        for bookmaker in source.get('bookmakers', []):
            for market in bookmaker.get('markets', []):
                key = market.get('key')
                if key not in {'h2h', 'totals'}:
                    excluded['unsupported_market'] += 1
                    continue
                names = ({source['home_team']: 'home', source['away_team']: 'away', 'Draw': 'draw'}
                         if key == 'h2h' else {'Over': 'over', 'Under': 'under'})
                outcomes = market.get('outcomes', [])
                if len(outcomes) != len(names) or {x.get('name') for x in outcomes} != set(names):
                    excluded['incomplete_market'] += 1
                    continue
                value = {'bookmaker': bookmaker.get('key'), 'market': key,
                         'quoted_at': market.get('last_update'),
                         'odds': {names[x['name']]: x.get('price') for x in outcomes}}
                if key == 'totals':
                    lines = {x.get('point') for x in outcomes}
                    if len(lines) != 1:
                        excluded['different_lines'] += 1
                        continue
                    value['line'] = lines.pop()
                row = {'provider': 'the-odds-api', 'cohort': target['cohort'],
                       'match_id': target['match_id'], 'entity_id': target['home_id'],
                       'field': 'market_quote', 'period': 'regulation_90',
                       'event_kickoff_at': target['kickoff_at'], 'observed_at': odds_meta['captured_at'],
                       'value': value, 'provider_match_id': source['id'],
                       'identity_evidence': identity_meta,
                       'evidence': {'file': odds_meta['file'], 'sha256': odds_meta['sha256'],
                                    'url': odds_meta['url'], 'query_time': odds_meta['captured_at'],
                                    'request_started_at': odds_meta.get('requested_at'),
                                    'identity_verified': True}}
                try:
                    validate(row, root, now=now)
                except (ValueError, KeyError, TypeError):
                    excluded['invalid_quote_or_timestamp'] += 1
                    continue
                rows.append(row)
        # All rows in this response share one capture time. Keep exact repeats
        # once; conflicting versions of a book/market/line remain untrusted.
        groups = defaultdict(dict)
        for row in rows:
            value = row['value']
            group = groups[(value['bookmaker'], value['market'], value.get('line'))]
            signature = json.dumps(value, sort_keys=True, allow_nan=False)
            if signature in group:
                excluded['duplicate_quote'] += 1
            group[signature] = row
        rows = []
        for group in groups.values():
            if len(group) > 1:
                excluded['conflicting_market_group'] += 1
            else:
                rows.extend(group.values())
        snap = snapshot(rows, target, root, provider='the-odds-api',
                        market_max_age_seconds=MAX_AGE_SECONDS, now=now)
        snapshots.append(snap)
        mappings.append({'match_id': target['match_id'], 'provider_match_id': source['id'],
                         'home_id': target['home_id'], 'away_id': target['away_id'],
                         'kickoff_at': target['kickoff_at'], 'identity_evidence_sha256': identity_meta['sha256']})
    count = sum(len(s['observations']) for s in snapshots)
    covered = sum(bool(s['observations']) for s in snapshots)
    return {'schema': 'WCPP-MARKET-CHANNEL-1', 'created_at': now.isoformat(),
            'market_model_ready': count > 0, 'verified_quote_count': count,
            'eligible_fixture_count': len(eligible), 'covered_fixture_count': covered,
            'coverage': covered / len(eligible) if eligible else None,
            'coverage_scope': 'ESPN Nations League league-phase future fixtures on requested UTC day',
            'source_odds_events': len(odds), 'market_max_age_seconds': MAX_AGE_SECONDS,
            'excluded': dict(excluded), 'identity_mappings': mappings, 'snapshots': snapshots,
            'tls_verification_disabled': False, 'active_model_changed': False,
            'quote_time_inferred_from_capture': False}


def fetch(url, public_url, path):
    requested = datetime.now(timezone.utc).isoformat()
    with urlopen(url, timeout=15) as response:
        body = response.read()
    captured = datetime.now(timezone.utc).isoformat()
    json.loads(body)
    path.write_bytes(body)
    return {'file': path.name, 'sha256': hashlib.sha256(body).hexdigest(),
            'url': public_url, 'requested_at': requested, 'captured_at': captured}


def collect(out):
    out = Path(out); out.mkdir(parents=True, exist_ok=False)
    key = os.environ.get('THE_ODDS') or os.environ.get('WCPP_ODDS_API_KEY')
    if not key:
        result = {'market_model_ready': False, 'verified_quote_count': 0,
                  'status': 'MISSING_CREDENTIAL', 'required_secret': 'WCPP_ODDS_API_KEY',
                  'requests': 0}
    else:
        try:
            day = datetime.now(timezone.utc).strftime('%Y%m%d')
            identity = fetch(ESPN + '?dates=' + day + '&limit=1000',
                             ESPN + '?dates=' + day + '&limit=1000', out / 'identity.json')
            params = {'regions': 'eu', 'markets': 'h2h,totals', 'oddsFormat': 'decimal'}
            public = ODDS + '?' + urlencode(params)
            odds = fetch(public + '&' + urlencode({'apiKey': key}), public, out / 'odds.json')
            write(out / 'source-metadata.json', {'odds': odds, 'identity': identity})
            now = datetime.now(timezone.utc)
            result = build(out, odds, identity, cutoff=now.isoformat(), now=now)
            result['status'] = 'VALIDATED' if result['market_model_ready'] else 'NO_VALID_QUOTES'
        except Exception as exc:
            result = {'status': 'COLLECTION_FAILED', 'market_model_ready': False,
                      'verified_quote_count': 0, 'failure': classify_failure(exc)}
    write(out / 'market-channel.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    result = collect(args.out)
    print(json.dumps({k: v for k, v in result.items() if k not in {'snapshots', 'identity_mappings'}}))
