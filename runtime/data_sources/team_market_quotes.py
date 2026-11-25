"""Retain source-timed pre-match BTTS and team-total odds for registered fixtures."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener

from automation.market_snapshot import SCOPES, validate_response_credentials
from data_sources.clients import NoRedirect
from research.analysis_freeze import aware, verify

DEFAULT_PLAN = 'workspace-updates/20261118-transformer/forward/plan.json'
MARKETS = ('btts', 'team_totals', 'alternate_team_totals')
QUOTA_FIELDS = ('x-requests-last', 'x-requests-used', 'x-requests-remaining')


def read(path):
    return json.loads(Path(path).read_bytes())


def write(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
        stream.write('\n')


def now():
    return datetime.now(timezone.utc).isoformat()


def load_targets(data_root, plan_path=DEFAULT_PLAN):
    root = Path(data_root).resolve()
    plan = verify(read(root / plan_path))['payload']
    market_root = (root / plan['binding']['market_archive']).resolve()
    if not market_root.is_relative_to(root / 'market/runs'):
        raise ValueError('unrecognized market archive')
    raw = (market_root / 'odds.json').read_bytes()
    hashed = hashlib.sha256(raw).hexdigest()
    if hashed != plan['sources']['odds']['sha256']:
        raise ValueError('anchoring event response hash differs')
    events = {e['id']: e for e in json.loads(raw)}
    targets = []
    for f in plan['fixtures']:
        mid = f['match_id']
        m = plan['markets'][mid]
        e = events[m['provider_match_id']]
        if (m['match_id'] != mid or m['source_sha256'] != hashed or not f['identity_verified']
                or f['score_basis'] != 'regulation_90' or m['provider'] != 'the-odds-api'
                or e['sport_key'] != SCOPES[f['competition']][0]
                or aware(e['commence_time']) != aware(f['kickoff_at'])):
            raise ValueError('registered event identity mismatch: ' + mid)
        targets.append(dict(f, provider_match_id=e['id'], provider_home=e['home_team'],
                            provider_away=e['away_team'], sport_key=e['sport_key']))
    if len({f['match_id'] for f in targets}) != len(targets):
        raise ValueError('duplicate registered match')
    return targets


def _identity(f, event):
    return (event.get('id') == f['provider_match_id'] and event.get('sport_key') == f['sport_key']
            and event.get('home_team') == f['provider_home'] and event.get('away_team') == f['provider_away']
            and aware(event['commence_time']) == aware(f['kickoff_at']))


def normalize_event(fixture, event, metadata):
    """Return complete named-book pairs only, preserving provider market-level quote time."""
    rejected = Counter()
    try:
        identified = _identity(fixture, event)
    except (KeyError, TypeError, AttributeError, ValueError):
        identified = False
    if not identified:
        return [], {'EVENT_IDENTITY_MISMATCH': 1}
    requested, capture = map(aware, (metadata['requested_at'], metadata['captured_at']))
    if not requested <= capture < aware(fixture['kickoff_at']):
        return [], {'OUTSIDE_PREMATCH_WINDOW': 1}
    teams = {fixture['provider_home']: 'home', fixture['provider_away']: 'away'}
    seen = {}
    conflicts = set()
    for book in event.get('bookmakers', []):
        book_key = book.get('key')
        if not isinstance(book_key, str) or not book_key.strip():
            rejected['UNKNOWN_BOOKMAKER'] += 1
            continue
        for market in book.get('markets', []):
            key = market.get('key')
            if key not in MARKETS:
                continue
            try:
                stamp = aware(market['last_update'])
            except (KeyError, ValueError, TypeError, AttributeError):
                rejected['MISSING_MARKET_QUOTE_TIME'] += 1
                continue
            if stamp > capture:
                rejected['SOURCE_QUOTE_AFTER_CAPTURE'] += 1
                continue
            pair_type = 'btts' if key == 'btts' else 'team_total'
            sides = ('yes', 'no') if key == 'btts' else ('over', 'under')
            groups = defaultdict(lambda: defaultdict(set))
            for outcome in market.get('outcomes', []):
                side = str(outcome.get('name', '')).lower()
                price = outcome.get('price')
                if side not in sides or type(price) not in (int, float) or not math.isfinite(price) or price <= 1:
                    rejected['INVALID_OUTCOME'] += 1
                    continue
                team, line = None, None
                if key != 'btts':
                    team = teams.get(outcome.get('description'))
                    line = outcome.get('point')
                    if (team is None or type(line) not in (int, float) or not math.isfinite(line)
                            or line < 0 or not (4 * float(line)).is_integer()):
                        rejected['UNKNOWN_TEAM_OR_LINE'] += 1
                        continue
                groups[team, line][side].add(price)
            for (team, line), outcomes in groups.items():
                if set(outcomes) != set(sides) or any(len(v) != 1 for v in outcomes.values()):
                    rejected['INCOMPLETE_OR_CONFLICTING_PAIR'] += 1
                    continue
                identity = book_key, pair_type, team, line, stamp
                odds = {s: next(iter(outcomes[s])) for s in sides}
                if identity in seen:
                    if seen[identity]['decimal_odds'] != odds:
                        conflicts.add(identity)
                    continue
                seen[identity] = dict(match_id=fixture['match_id'], provider_match_id=fixture['provider_match_id'],
                    competition=fixture['competition'], kickoff_at=fixture['kickoff_at'], provider='the-odds-api',
                    provider_home=fixture['provider_home'], provider_away=fixture['provider_away'],
                    score_basis='regulation_90', bookmaker=book_key, market=pair_type, market_key=key,
                    team=team, line=line, decimal_odds=odds, quoted_at=market['last_update'],
                    **{k: metadata[k] for k in ('requested_at', 'captured_at', 'source_file', 'source_sha256', 'source_url')})
    for k in conflicts:
        del seen[k]
        rejected['CONFLICTING_MARKET_KEYS'] += 1
    return list(seen.values()), dict(rejected)


def _fetch(f, regions, key, directory, markets):
    event_id = f['provider_match_id']
    if not re.fullmatch(r'[0-9a-f]{32}', event_id):
        raise ValueError('invalid provider event id')
    endpoint = f"https://api.the-odds-api.com/v4/sports/{f['sport_key']}/events/{event_id}/odds"
    params = dict(regions=regions, markets=','.join(markets), oddsFormat='decimal')
    url = endpoint + '?' + urlencode(params)
    metadata = dict(match_id=f['match_id'], provider_match_id=event_id, source_url=url, requested_at=now(),
                    regions=regions, markets=list(markets))
    try:
        response = build_opener(NoRedirect).open(Request(url + '&' + urlencode({'apiKey': key})), timeout=10)
    except HTTPError as exc:
        response = exc
    except (URLError, TimeoutError, OSError) as exc:
        return dict(metadata, captured_at=now(), status='NETWORK_ERROR', failure=type(exc).__name__, quota={}), None
    with response:
        metadata.update(http_status=response.status,
                        quota={h: int(response.headers[h]) for h in QUOTA_FIELDS if (response.headers.get(h) or '').isdigit()})
        raw = response.read(4_000_001)
    metadata['captured_at'] = now()
    if len(raw) > 4_000_000:
        return dict(metadata, status='RESPONSE_TOO_LARGE'), None
    if key.encode() in raw:
        raise ValueError('credential echoed in provider response')
    try:
        event = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return dict(metadata, status='NON_JSON'), None
    validate_response_credentials(event, (key,))
    if metadata['http_status'] != 200 or not isinstance(event, dict):
        code = event.get('error_code', '') if isinstance(event, dict) else ''
        return dict(metadata, status='HTTP_ERROR', provider_error_code=code if re.fullmatch(r'[A-Z_0-9]{1,90}', str(code)) else None), None
    file = f'raw/{event_id}-{regions.replace(",", "_")}.json'
    (directory / file).write_bytes(raw)
    return dict(metadata, status='OK', source_file=file, source_sha256=hashlib.sha256(raw).hexdigest()), event


def _brazil_first_issue_hashes(data_root, plan, started_at):
    from research_program.team_market_candidate import DEFAULT_BATCH, preflight

    root = Path(data_root).resolve()
    if (root / plan).resolve() != root / DEFAULT_PLAN:
        raise ValueError('Brazil first-issue preflight requires the original registered plan')
    receipt = preflight(root, root / DEFAULT_BATCH, as_of=aware(started_at))
    return {row['match_id']: row['first_sha256'] for row in receipt['rows']
            if row['status'] == 'PREVIOUSLY_ISSUED'}


def verify_run(data_root, path):
    """Recompute every retained price pair from its original provider bytes, offline."""
    path = Path(path).resolve()
    report = read(path / 'coverage.json')
    targets = read(path / 'targets.json')
    if report.get('discovery_competition'):
        from data_sources.team_market_discovery import replay_discovery
        rebuilt, _ = replay_discovery(path, read(path / 'discovery-receipt.json'))
    else:
        if verify(read(Path(data_root) / report['plan']))['record_sha256'] != report['plan_sha256']:
            raise ValueError('registered comparison plan changed')
        rebuilt = load_targets(data_root, report['plan'])
    if rebuilt != targets:
        raise ValueError('source fixture identities differ')
    by_id = {f['match_id']: f for f in targets}
    requests = read(path / 'requests.json')
    if report.get('collection_settings', {}).get('brazil_first_issue_preflight'):
        first = _brazil_first_issue_hashes(data_root, report['plan'], report['started_at'])
        skipped = {f['match_id']: f.get('first_sha256') for f in report['fixtures']
                   if f['status'] == 'PREVIOUSLY_ISSUED'}
        if skipped != first or set(skipped).intersection(r['match_id'] for r in requests):
            raise ValueError('retained Brazil first-issue preflight differs')
    rows = []
    exclusions_by_id = {}
    for meta in requests:
        if meta['status'] != 'OK':
            continue
        f = by_id[meta['match_id']]
        raw_path = (path / meta['source_file']).resolve()
        if (not raw_path.is_relative_to(path / 'raw') or f['provider_match_id'] != meta['provider_match_id']
                or hashlib.sha256(raw_path.read_bytes()).hexdigest() != meta['source_sha256']):
            raise ValueError('retained source identity or raw bytes differ')
        pairs, exclusions = normalize_event(f, read(raw_path), meta)
        rows.extend(pairs)
        exclusions_by_id[f['match_id']] = exclusions
    events = {f['match_id']: f for f in report['fixtures']}
    for mid, item in events.items():
        subset = [r for r in rows if r['match_id'] == mid]
        if (item['accepted_pairs'] != len(subset) or item['exclusions'] != exclusions_by_id.get(mid, {})
                or item['btts_books'] != sorted({x['bookmaker'] for x in subset if x['market'] == 'btts'})
                or item['home_team_05_books'] != sorted({x['bookmaker'] for x in subset if x['team'] == 'home' and x['line'] == .5})
                or item['away_team_05_books'] != sorted({x['bookmaker'] for x in subset if x['team'] == 'away' and x['line'] == .5})):
            raise ValueError('retained fixture coverage differs')
    for metric, field in (('fixtures_with_btts', 'btts_books'), ('fixtures_with_home_05', 'home_team_05_books'),
                          ('fixtures_with_away_05', 'away_team_05_books')):
        if report[metric] != sum(bool(item[field]) for item in events.values()):
            raise ValueError('retained coverage denominator differs')
    if (set(events) != set(by_id) or len(events) != report['registered'] or len(requests) != report['requests']
            or len(rows) != report['accepted_pairs'] or rows != read(path / 'quotes.json')):
        raise ValueError('retained quote replay differs')
    return report


def collect(data_root, out, *, plan=DEFAULT_PLAN, regions='eu', max_events=None, discover=None,
            days=4, match_markets=False, brazil_first_issue_preflight=False, fetcher=_fetch):
    """One event call per upcoming fixture; aggregate all requested paid markets in that call."""
    if (not regions or len(set(regions.split(','))) != len(regions.split(','))
            or any(r not in {'eu', 'uk', 'us', 'us2', 'au'} for r in regions.split(','))):
        raise ValueError('unsupported bookmaker region')
    if max_events is not None and max_events <= 0:
        raise ValueError('max-events must be positive')
    out = Path(out)
    if not 1 <= days <= 14:
        raise ValueError('discovery horizon must be 1–14 days')
    if discover and max_events is None:
        max_events = 1
    markets = (*MARKETS, 'h2h', 'totals') if match_markets else MARKETS
    settings = dict(max_events=max_events, days=days if discover else None)
    if brazil_first_issue_preflight:
        if discover or match_markets:
            raise ValueError('Brazil first-issue preflight is for registered team markets only')
        settings['brazil_first_issue_preflight'] = True
    if out.exists():
        old = verify_run(data_root, out)
        if (old['plan'] != (None if discover else str(plan)) or old.get('discovery_competition') != discover
                or old['regions'] != regions or old['requested_markets'] != list(markets)
                or old.get('collection_settings', {} if brazil_first_issue_preflight else settings) != settings):
            raise ValueError('existing run was created with different collection settings')
        return old
    out.parent.mkdir(parents=True, exist_ok=True)
    started = now()
    first = _brazil_first_issue_hashes(data_root, plan, started) if brazil_first_issue_preflight else {}
    key = os.environ.get('THE_ODDS') or os.environ.get('WCPP_ODDS_API_KEY') or os.environ.get('WCPP_THE_ODDS_API_KEY')
    requests, quotes, coverage = [], [], []
    remaining = None
    with tempfile.TemporaryDirectory(dir=out.parent, prefix='.team-markets-') as tmp:
        stage = Path(tmp) / 'run'
        (stage / 'raw').mkdir(parents=True)
        if discover:
            from data_sources.team_market_discovery import discover_targets
            targets, discovery = discover_targets(discover, days, stage, key, started)
            write(stage / 'discovery-receipt.json', discovery)
            plan_sha = None
        else:
            targets = load_targets(data_root, plan)
            plan_sha = verify(read(Path(data_root)/plan))['record_sha256']
        write(stage / 'targets.json', targets)
        order = targets
        prior_coverage = set()
        if discover:
            for prior_file in out.parent.glob('*/coverage.json'):
                prior = read(prior_file)
                if prior.get('discovery_competition') == discover and prior.get('regions') == regions:
                    prior_coverage.update(f['match_id'] for f in prior['fixtures'] if f['accepted_pairs'])
            order = sorted(targets, key=lambda f: f['match_id'] in prior_coverage)
            catalog = discovery['requests'][0] if discovery.get('requests') else {}
            remaining = catalog.get('quota', {}).get('x-requests-remaining')
        stop = None
        for f in order:
            mid = f['match_id']
            reason = stop
            if mid in first:
                reason = 'PREVIOUSLY_ISSUED'
            elif aware(f['kickoff_at']) <= aware(now()):
                reason = 'KICKED_OFF'
            elif not key:
                reason = 'MISSING_CREDENTIAL'
            elif max_events is not None and len(requests) >= max_events:
                reason = 'MAX_EVENTS'
            # Cost is charged per returned market per region. Never issue a call beyond the known balance.
            elif remaining is not None and remaining < len(markets) * len(regions.split(',')):
                reason = 'QUOTA_BUDGET'
            rows, exclusions = [], {}
            if reason is None:
                meta, event = fetcher(f, regions, key, stage, markets)
                requests.append(meta)
                remaining = meta['quota'].get('x-requests-remaining', remaining)
                if meta.get('http_status') in (401, 403, 429):
                    stop = 'PROVIDER_STOP_' + str(meta['http_status'])
                if event is None:
                    reason = meta['status']
                else:
                    rows, exclusions = normalize_event(f, event, meta)
                    reason = 'COLLECTED' if rows else 'NO_VALID_MARKETS'
            quotes.extend(rows)
            coverage.append(dict(match_id=mid, status=reason, accepted_pairs=len(rows), exclusions=exclusions,
                btts_books=sorted({r['bookmaker'] for r in rows if r['market'] == 'btts'}),
                home_team_05_books=sorted({r['bookmaker'] for r in rows if r['team'] == 'home' and r['line'] == .5}),
                away_team_05_books=sorted({r['bookmaker'] for r in rows if r['team'] == 'away' and r['line'] == .5})))
            if mid in first:
                coverage[-1]['first_sha256'] = first[mid]
        write(stage / 'requests.json', requests)
        write(stage / 'quotes.json', quotes)
        summary = dict(schema='WCPP-TEAM-MARKETS-1', registered=len(targets), started_at=started, completed_at=now(),
            source='the-odds-api', plan=None if discover else str(plan), plan_sha256=plan_sha,
            discovery_competition=discover, requested_markets=list(markets), regions=regions, requests=len(requests),
            credits_reported=sum(r['quota'].get('x-requests-last', 0) for r in requests), remaining_credits=remaining,
            accepted_pairs=len(quotes), fixtures_with_btts=sum(bool(x['btts_books']) for x in coverage),
            fixtures_with_home_05=sum(bool(x['home_team_05_books']) for x in coverage),
            fixtures_with_away_05=sum(bool(x['away_team_05_books']) for x in coverage), fixtures=coverage)
        summary['collection_settings'] = settings
        if discover:
            summary['previously_covered_in_selected_population'] = sorted(prior_coverage & {f['match_id'] for f in targets})
        write(stage / 'coverage.json', summary)
        stage.rename(out)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--out', type=Path)
    mode.add_argument('--verify-run', type=Path)
    parser.add_argument('--plan', default=DEFAULT_PLAN)
    parser.add_argument('--regions', default='eu')
    parser.add_argument('--max-events', type=int)
    parser.add_argument('--discover', choices=['eng.1', 'bra.1'], help='rolling official fixture discovery, independent of the original six')
    parser.add_argument('--days', type=int, default=4)
    parser.add_argument('--match-markets', action='store_true', help='also retain same-response raw 1X2 and full-match O/U')
    parser.add_argument('--brazil-first-issue-preflight', action='store_true',
                        help='skip verified first issues in the registered Brazil research batch')
    args = parser.parse_args()
    if args.verify_run:
        result = verify_run(args.data_root, args.verify_run)
    else:
        result = collect(args.data_root, args.out, plan=args.plan, regions=args.regions, max_events=args.max_events,
                         discover=args.discover, days=args.days, match_markets=args.match_markets,
                         brazil_first_issue_preflight=args.brazil_first_issue_preflight)
    print(json.dumps({k: v for k, v in result.items() if k != 'fixtures'}, sort_keys=True))
