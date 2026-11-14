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
    from runtime.data_sources.clients import NoRedirect
    from runtime.v2_test.data_contract import aware, public_url as validate_public_url, snapshot, validate
except ModuleNotFoundError:
    from automation.collection_status import classify_failure
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
ALIAS_REVIEW_SHA256 = hashlib.sha256(json.dumps(
    ALIAS_REVIEW, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
ADDITIONAL_ALIAS_REVIEWS = json.loads((Path(__file__).resolve().parents[2]
    / 'configs' / 'market-team-alias-reviews.json').read_text())


def alias_reviews(competition):
    return [r for r in (ALIAS_REVIEW, *ADDITIONAL_ALIAS_REVIEWS)
            if r['cohort'] == competition]


def review_sha(review):
    return hashlib.sha256(json.dumps(review, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def team_identity(team, provider_name, cutoff, competition='uefa.nations'):
    """Return match evidence, or None. A reviewed name never admits another ID."""
    relevant = [(review, rule) for review in alias_reviews(competition) for rule in review['teams']
                if provider_name in (rule['provider_name'], rule['espn_name'])
                or team['displayName'] in (rule['provider_name'], rule['espn_name'])]
    if relevant:
        if len(relevant) != 1 or team['id'] != relevant[0][1]['espn_id']:
            return None
        review, rule = relevant[0]
        if team['displayName'] != rule['espn_name']:
            return None
        if provider_name == rule['espn_name']:
            return {}
        if provider_name == rule['provider_name'] and aware(review['reviewed_at']) <= aware(cutoff):
            return dict(rule)
        return None
    return {} if provider_name == team['displayName'] else None


def alias_evidence_for(aliases, source, sides, competition):
    reviews = [r for r in alias_reviews(competition)
               if any(rule in r['teams'] for rule in aliases.values())]
    references = [dict(review_version=r['version'], review_sha256=review_sha(r),
                       reviewed_at=r['reviewed_at']) for r in reviews]
    identity = references[0] if len(references) == 1 else {
        'review_version': 'MULTIPLE_REVIEWED_TEAM_IDENTITIES',
        'review_sha256': review_sha(references),
        'reviewed_at': max(r['reviewed_at'] for r in references)}
    return dict(identity, teams=aliases, reviews=references,
                provider_source_names={side: source[side + '_team'] for side in ('home', 'away')},
                identity_source_names={side: sides[side]['displayName'] for side in ('home', 'away')})


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


def build(root, odds_meta, identity_meta, *, cutoff, now=None,
          competition='uefa.nations', market_max_age_seconds=MAX_AGE_SECONDS):
    now = now or datetime.now(timezone.utc)
    sport_key, source_phase, stage = SCOPES[competition]
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
            if (not isinstance(event['id'], str) or not event['id'].strip()
                    or event_ids[event['id']] != 1 or len(event['competitions']) != 1):
                raise ValueError('unique event and competition required')
            comp = event['competitions'][0]
            if ('id' in comp and str(comp['id']) != event['id']
                    or 'date' in comp and aware(comp['date']) != aware(event['date'])):
                raise ValueError('conflicting competition identity')
            comp_status = comp.get('status', {}).get('type')
            if comp_status is not None and (comp_status.get('state') != 'pre'
                                            or comp_status.get('completed') is not False):
                raise ValueError('conflicting competition status')
            competitors = comp['competitors']
            sides = {x['homeAway']: x['team'] for x in competitors}
            if (len(competitors) != 2 or set(sides) != {'home', 'away'}
                    or any(not isinstance(t.get(k), str) or not t[k].strip()
                           for t in sides.values() for k in ('id', 'displayName'))
                    or sides['home']['id'] == sides['away']['id']):
                raise ValueError('two unambiguous team identities required')
            eligible.append((event, sides))
        except (AttributeError, KeyError, TypeError, ValueError):
            excluded['unverified_target_identity_or_status'] += 1
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
        alias_evidence = (alias_evidence_for(aliases, source, sides, competition)
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
            'identity_alias_review': dict(ALIAS_REVIEW, sha256=ALIAS_REVIEW_SHA256),
            'additional_identity_alias_reviews': [dict(r, sha256=review_sha(r))
                                                  for r in ADDITIONAL_ALIAS_REVIEWS],
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
    board_endpoints = {ESPN.replace('/uefa.nations/', '/' + scope + '/') for scope in SCOPES}
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
