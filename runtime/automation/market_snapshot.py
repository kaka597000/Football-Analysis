"""Bounded competition market collection with retained identity/time evidence.

This is a data channel, not an active-model or staking change. Exact names or
reviewed provider/team-ID aliases require the same sides, competition and kickoff.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit
from urllib.request import Request, build_opener

try:
    from runtime.automation.collection_status import classify_failure
    from runtime.automation.market_identity import scheduled_competition, team_match
    from runtime.data_sources.clients import NoRedirect
    from runtime.v2_test.data_contract import aware, public_url as validate_public_url, snapshot, validate
except ModuleNotFoundError:
    from automation.collection_status import classify_failure
    from automation.market_identity import scheduled_competition, team_match
    from data_sources.clients import NoRedirect
    from v2_test.data_contract import aware, public_url as validate_public_url, snapshot, validate

ODDS = 'https://api.the-odds-api.com/v4/sports/soccer_uefa_nations_league/odds/'
ESPN = 'https://site.api.espn.com/apis/site/v2/sports/soccer/uefa.nations/scoreboard'
MAX_AGE_SECONDS = 60
SCOPES = {
    'uefa.nations': ('soccer_uefa_nations_league', 'league-phase', 'league-phase'),
    'eng.1': ('soccer_epl', 'regular-season', 'regular'),
    'esp.1': ('soccer_spain_la_liga', 'regular-season', 'regular'),
    'ger.1': ('soccer_germany_bundesliga', 'regular-season', 'regular'),
    'ita.1': ('soccer_italy_serie_a', 'regular-season', 'regular'),
    'fra.1': ('soccer_france_ligue_one', 'regular-season', 'regular'),
    'nor.1': ('soccer_norway_eliteserien', 'regular-season', 'regular'),
    'swe.1': ('soccer_sweden_allsvenskan', 'regular-season', 'regular'),
    'bra.1': ('soccer_brazil_campeonato', 'brasileiro-serie-a', 'regular'),
}

# Reviewed from retained raw responses, not transliteration/fuzzy matching. IDs
# are national-team ESPN identities; no fixture ID is baked into future matching.
ALIAS_REVIEW = {
    'version': 'nations-national-teams-20261112-1',
    'reviewed_at': '2026-11-12T13:43:21.438909+00:00',
    'provider': 'the-odds-api', 'sport_key': 'soccer_uefa_nations_league',
    'cohort': 'uefa.nations', 'phase': 'league-phase',
    'raw_odds_sha256': '182c812ca39cb293058f4a6cf6904c16c76a2633127f2cea213ec45ef86a0224',
    'identity_sha256': '27276f59f9abaa583098f4becad5bfc74b89b75b60f3525361c9927ff51e336e',
    'archive': {
        'repository': 'kaka597000/kaka597000-Football-Analysis-Data',
        'commit': 'f7448c65ad358c8240dc3111850531d1abcb5ce3',
        'file': 'workspace-updates/20261112-nations-prematch/nations-prematch-evidence.tar.gz',
        'sha256': '0deb5a50e677e6f9bb05ffc9028217ba02d3fbb4909701045817cf334c298c76',
        'review_file': 'WCPP/reports/nations-prematch-20261112-2135/data/odds-identity-crosswalk.json',
    },
    'teams': [
        {'espn_id': '465', 'espn_name': 'Türkiye', 'provider_name': 'Turkey'},
        {'espn_id': '450', 'espn_name': 'Czechia', 'provider_name': 'Czech Republic'},
    ],
}
ADDITIONAL_ALIAS_REVIEW = {
    'version': 'nations-bosnia-20261116-1', 'reviewed_at': '2026-11-16T09:30:46+00:00',
    'provider': 'the-odds-api', 'sport_key': 'soccer_uefa_nations_league',
    'cohort': 'uefa.nations', 'phase': 'league-phase',
    'raw_odds_sha256': 'ea1b3bbaa14d2444b1eeb987e9f6b0d1bdd38da136f901a75ad8c1611b24f38e',
    'identity_sha256': '23cc80dddf22b193ae9643cc2e6e556a4f6701e9d35bac74227788c79f88331a',
    'review_event': {'espn_id':'401861196','provider_id':'3eb1d15a190bb1be8c32a020365dc7c2',
                     'home_id':'452','away_id':'473','kickoff_at':'2026-11-17T19:45:00Z'},
    'teams': [{'espn_id':'452','espn_name':'Bosnia-Herzegovina','provider_name':'Bosnia & Herzegovina'}],
}
ALIAS_REVIEWS = (ALIAS_REVIEW, ADDITIONAL_ALIAS_REVIEW)
ALIAS_REGISTRY = {'version':'nations-alias-registry-20261116-1','reviews':list(ALIAS_REVIEWS)}
ALIAS_REVIEW_SHA256 = hashlib.sha256(json.dumps(
    ALIAS_REGISTRY, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


ADDITIONAL_ALIAS_REVIEWS = json.loads((Path(__file__).resolve().parents[2]
    / 'configs' / 'market-team-alias-reviews.json').read_text(encoding='utf-8'))


def alias_registry(competition):
    # Keep PR39's Nations registry and evidence hashes unchanged.
    if competition == 'uefa.nations':
        return ALIAS_REGISTRY
    return {'version': 'brazil-alias-registry-20261118-1' if competition == 'bra.1' else 'competition-alias-registry-20261117-1',
            'reviews': [r for r in ADDITIONAL_ALIAS_REVIEWS if r['cohort'] == competition]}


def review_sha(review):
    return hashlib.sha256(json.dumps(review, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def team_identity(team, provider_name, cutoff, competition='uefa.nations'):
    """Return match evidence, or None. A reviewed name never admits another ID."""
    entries = ((rule, review['reviewed_at']) for review in alias_registry(competition)['reviews']
               for rule in review['teams'])
    return team_match(team, provider_name, cutoff, entries)


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


def scheduled_targets(board, competition, now):
    """All identified future events in scope, including those with no provider quote."""
    _, source_phase, _ = SCOPES[competition]
    eligible, excluded = [], Counter()
    event_ids = Counter(str(e.get('id')) for e in board.get('events', []) if isinstance(e, dict))
    for event in board.get('events', []):
        try:
            status = event.get('status', {}).get('type', {})
            if status.get('state') != 'pre' or status.get('completed') is not False:
                continue
            season = event.get('season', {})
            expected_phase = (f"{season.get('year')}-{source_phase}"
                              if competition == 'bra.1' else source_phase)
            if (season.get('slug') != expected_phase
                    or competition == 'bra.1' and season.get('year') != aware(event['date']).year
                    or aware(event['date']) <= now):
                continue
            if event_ids[event['id']] != 1:
                raise ValueError('unique event required')
            _, sides = scheduled_competition(event)
            eligible.append((event, sides))
        except (AttributeError, KeyError, TypeError, ValueError):
            excluded['unverified_target_identity_or_status'] += 1
    return eligible, excluded


def build(root, odds_meta, identity_meta, *, cutoff, now=None,
          competition='uefa.nations', market_max_age_seconds=MAX_AGE_SECONDS):
    now = now or datetime.now(timezone.utc)
    sport_key, _, stage = SCOPES[competition]
    if type(market_max_age_seconds) is not int or not 0 < market_max_age_seconds <= 3600:
        raise ValueError('market age must be within the locked one-hour limit')
    odds, board = (check_evidence(root, m) for m in (odds_meta, identity_meta))
    if not isinstance(odds, list) or not isinstance(board, dict):
        raise ValueError('provider event list and independent scoreboard required')
    if aware(cutoff) > now:
        raise ValueError('future cutoff')
    if not any(x.get('slug') == competition for x in board.get('leagues', [])):
        raise ValueError('requested competition source identity required')
    if max(aware(m['captured_at']) for m in (odds_meta, identity_meta)) > aware(cutoff):
        raise ValueError('identity and quote capture must precede cutoff')
    eligible, excluded = scheduled_targets(board, competition, now)
    snapshots, mappings = [], []
    provider_ids = Counter(str(e.get('id')) for e in odds if isinstance(e, dict))
    for event, sides in eligible:
        matched = []
        for source in odds:
            try:
                if (not isinstance(source.get('id'), str) or not source['id'].strip()
                        or provider_ids[source['id']] != 1 or source.get('sport_key') != sport_key
                        or aware(source['commence_time']) != aware(event['date'])):
                    continue
                identities = {side: team_identity(sides[side], source.get(side + '_team'), cutoff, competition)
                              for side in ('home', 'away')}
            except (AttributeError, KeyError, TypeError, ValueError):
                continue
            if all(value is not None for value in identities.values()):
                matched.append((source, {side: value for side, value in identities.items() if value}))
        if len(matched) != 1:
            excluded['missing_or_ambiguous_identity'] += 1
            continue
        source, aliases = matched[0]
        registry = alias_registry(competition)
        used_reviews = [r for r in registry['reviews'] if aware(r['reviewed_at']) <= aware(cutoff)
                        and any(rule in r['teams'] for rule in aliases.values())]
        alias_evidence = ({'review_version': registry['version'],
                           'review_sha256': review_sha(registry),
                           'reviewed_at': max((r['reviewed_at'] for r in used_reviews),key=aware),
                           'used_reviews':used_reviews, 'teams': aliases,
                           'provider_source_names': {side: source[side + '_team'] for side in ('home', 'away')},
                           'identity_source_names': {side: sides[side]['displayName'] for side in ('home', 'away')}}
                          if aliases else None)
        cohort = competition
        if competition == 'uefa.nations':
            group = event['competitions'][0].get('group', {}).get('name', '')
            grade = re.fullmatch(r'Group ([ABCD])[1-4]', group, re.IGNORECASE)
            if not grade:
                excluded['missing_verified_nations_grade'] += 1
                continue
            cohort += ':' + grade[1].upper() + ':league-phase'
        target = {'cohort': cohort, 'competition': competition, 'stage': stage,
                  'match_id': 'espn:' + event['id'],
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
                if alias_evidence:
                    row['identity_alias_review'] = alias_evidence
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
                        market_max_age_seconds=market_max_age_seconds, now=now)
        snapshots.append(snap)
        mapping = {'match_id': target['match_id'], 'provider_match_id': source['id'],
                   'home_id': target['home_id'], 'away_id': target['away_id'],
                   'kickoff_at': target['kickoff_at'], 'identity_evidence_sha256': identity_meta['sha256']}
        if alias_evidence:
            mapping['identity_alias_review'] = alias_evidence
            mapping['odds_evidence_sha256'] = odds_meta['sha256']
        mappings.append(mapping)
    count = sum(len(s['observations']) for s in snapshots)
    covered = sum(bool(s['observations']) for s in snapshots)
    return {'schema': 'WCPP-MARKET-CHANNEL-1', 'created_at': now.isoformat(),
            'market_model_ready': count > 0, 'verified_quote_count': count,
            'eligible_fixture_count': len(eligible), 'covered_fixture_count': covered,
            'coverage': covered / len(eligible) if eligible else None,
            'coverage_scope': f'ESPN {competition} {stage} future fixtures on requested UTC day',
            'source_odds_events': len(odds), 'market_max_age_seconds': market_max_age_seconds,
            'excluded': dict(excluded), 'identity_mappings': mappings, 'snapshots': snapshots,
            'identity_alias_review': dict(alias_registry(competition), sha256=review_sha(alias_registry(competition))),
            'tls_verification_disabled': False, 'active_model_changed': False,
            'quote_time_inferred_from_capture': False}


def validate_response_credentials(value, secrets):
    """Inspect decoded raw JSON without treating provider business keys as auth.

    TheOdds uses ``key`` for bookmaker and market identities. Normalized-row
    credential guards must not remove or reject these raw provider fields.
    """
    credential_fields = {'api_key', 'apikey', 'api-key', 'token', 'access_token',
                         'password', 'authorization', 'secret', 'x-auth-token',
                         'x-apisports-key', 'x-rapidapi-key', 'x-therundown-key'}
    if isinstance(value, dict):
        for name, item in value.items():
            if name.lower() in credential_fields:
                raise ValueError('credential fields prohibited')
            validate_response_credentials(name, secrets)
            validate_response_credentials(item, secrets)
    elif isinstance(value, list):
        for item in value:
            validate_response_credentials(item, secrets)
    elif isinstance(value, str) and any(secret in value for secret in secrets):
        raise ValueError('credential echoed by provider')


def fetch(url, public_url, path):
    """Bounded approved endpoints; never retain echoed query credentials."""
    validate_public_url(public_url)
    public, requested_url = urlsplit(public_url), urlsplit(url)
    endpoint = public.scheme + '://' + public.netloc + public.path
    odds_endpoints = {ODDS.replace('soccer_uefa_nations_league', scope[0]) for scope in SCOPES.values()}
    board_scopes = (*SCOPES, 'jpn.1', 'uefa.europa', 'uefa.europa.conf')
    board_endpoints = {ESPN.replace('/uefa.nations/', '/' + scope + '/') for scope in board_scopes}
    if (endpoint not in odds_endpoints | board_endpoints
            or requested_url._replace(query='') != public._replace(query='')):
        raise ValueError('unapproved collection endpoint')
    query = parse_qsl(requested_url.query, keep_blank_values=True)
    secrets = [value for name, value in query if name == 'apiKey']
    if ([item for item in query if item[0] != 'apiKey'] != parse_qsl(public.query, keep_blank_values=True)
            or (endpoint in odds_endpoints and (len(secrets) != 1 or not secrets[0]))
            or (endpoint in board_endpoints and secrets)):
        raise ValueError('unexpected collection query or credential')
    requested = datetime.now(timezone.utc).isoformat()
    with build_opener(NoRedirect()).open(Request(url, headers={'Accept': 'application/json'}), timeout=15) as response:
        body = response.read(20_000_001)
    if len(body) > 20_000_000:
        raise ValueError('response too large')
    data = json.loads(body)
    validate_response_credentials(data, secrets)
    if any(secret.encode() in body for secret in secrets):
        raise ValueError('credential echoed by provider')
    if not isinstance(data, list if endpoint in odds_endpoints else dict):
        raise ValueError('unexpected provider response shape')
    captured = datetime.now(timezone.utc).isoformat()
    with Path(path).open('xb') as handle:
        handle.write(body)
    return {'file': Path(path).name, 'sha256': hashlib.sha256(body).hexdigest(),
            'url': public_url, 'requested_at': requested, 'captured_at': captured}


def collect(out, *, competition='uefa.nations', date=None, market_max_age_seconds=MAX_AGE_SECONDS):
    sport_key = SCOPES[competition][0]
    day = date or datetime.now(timezone.utc).strftime('%Y%m%d')
    datetime.strptime(day, '%Y%m%d')
    out = Path(out); out.mkdir(parents=True, exist_ok=False)
    key = (os.environ.get('THE_ODDS') or os.environ.get('WCPP_ODDS_API_KEY')
           or os.environ.get('WCPP_THE_ODDS_API_KEY'))
    if not key:
        result = {'market_model_ready': False, 'verified_quote_count': 0,
                  'status': 'MISSING_CREDENTIAL', 'required_secret': 'WCPP_ODDS_API_KEY',
                  'requests': 0}
    else:
        try:
            board_url = ESPN.replace('/uefa.nations/', '/' + competition + '/')
            identity = fetch(board_url + '?dates=' + day + '&limit=1000',
                             board_url + '?dates=' + day + '&limit=1000', out / 'identity.json')
            params = {'regions': 'eu', 'markets': 'h2h,totals', 'oddsFormat': 'decimal'}
            public = ODDS.replace('soccer_uefa_nations_league', sport_key) + '?' + urlencode(params)
            odds = fetch(public + '&' + urlencode({'apiKey': key}), public, out / 'odds.json')
            write(out / 'source-metadata.json', {'odds': odds, 'identity': identity})
            now = datetime.now(timezone.utc)
            result = build(out, odds, identity, cutoff=now.isoformat(), now=now,
                           competition=competition, market_max_age_seconds=market_max_age_seconds)
            result['status'] = 'VALIDATED' if result['market_model_ready'] else 'NO_VALID_QUOTES'
        except Exception as exc:
            result = {'status': 'COLLECTION_FAILED', 'market_model_ready': False,
                      'verified_quote_count': 0, 'failure': classify_failure(exc)}
    write(out / 'market-channel.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True)
    parser.add_argument('--competition', choices=SCOPES, default='uefa.nations')
    parser.add_argument('--date', help='UTC scoreboard day in YYYYMMDD format')
    parser.add_argument('--market-max-age-seconds', type=int, default=MAX_AGE_SECONDS)
    args = parser.parse_args()
    result = collect(args.out, competition=args.competition, date=args.date,
                     market_max_age_seconds=args.market_max_age_seconds)
    print(json.dumps({k: v for k, v in result.items() if k not in {'snapshots', 'identity_mappings'}}))
