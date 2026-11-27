"""Bind a retained public InferSports club quote response to an ESPN fixture."""

from collections import defaultdict
from datetime import timedelta
from math import isfinite, log

from automation.infer_market_snapshot import BASE, BOOKMAKERS, read_evidence
from automation.market_identity import scheduled_competition
from research.analysis_freeze import aware
from .market_baseline import MAXIMUM_QUOTE_AGE

LEAGUES = {'eng.1': ('lg_T9268DXK','ENPL'), 'ger.1': ('lg_PGYWSDEA','GER'),
           'ita.1': ('lg_QXCSBRME','ITA'), 'esp.1': ('lg_PYBWSDEA','SPA'), 'fra.1': ('lg_CNSP3BPA','FRA'),
           'jpn.1': ('lg_VWS270YX','Jap1')}


def fresh_rows(raw, meta, match_id, cutoff, *, maximum_age=MAXIMUM_QUOTE_AGE):
    capture = aware(meta['captured_at'])
    groups = defaultdict(dict)
    if raw.get('stale') is not False or not capture <= cutoff:
        return []
    for row in raw['odds']:
        market = row.get('market_type')
        if (market not in ('1x2','totals') or row.get('format') != 'decimal' or row.get('status') != 'open'
                or row.get('period') != 'full_time' or row.get('bookmaker') not in BOOKMAKERS):
            continue
        prices = row.get('prices')
        keys = {'home','draw','away'} if market == '1x2' else {'over','under'}
        if not isinstance(prices, dict) or set(prices) != keys or any(
                type(v) not in (float,int) or not isfinite(v) or v <= 1 for v in prices.values()):
            continue
        try:
            live = aware(row['as_of'])
        except (KeyError, ValueError, TypeError, AttributeError):
            continue
        if live > capture or not timedelta(0) <= cutoff - live <= maximum_age:
            continue
        line = row.get('line')
        if market == '1x2' and line is not None:
            continue
        if market == 'totals':
            if type(line) not in (int,float) or not 0 <= line <= 20 or not (line*4.).is_integer():
                continue
            parts = row.get('handicap_components')
            if parts is not None and ((line*2.).is_integer() or not isinstance(parts,list) or len(parts) != 2
                                      or any(type(p) not in (int,float) for p in parts)
                                      or sorted(parts) != [line-.25, line+.25]):
                continue
            if any(row.get(side+'_line', line) != line for side in ('over','under')):
                continue
        bookmaker = row['bookmaker']
        normalized = {'match_id': match_id, 'provider_match_id': raw['event_id'], 'period': 'regulation_90',
                      'bookmaker': bookmaker, 'market': market, 'line': line, 'decimal_odds': prices,
                      'quoted_at': row['as_of'], 'query_time': meta['captured_at'], 'source_url': meta['url'],
                      'source_sha256': meta['sha256']}
        groups[bookmaker, market, line][live, tuple(sorted(prices.items()))] = normalized
    return [next(iter(group.values())) for group in groups.values() if len(group) == 1]


def choose_total(rows):
    h2h = {r['bookmaker']:r for r in rows if r['market'] == '1x2'}
    totals = defaultdict(list)
    for row in rows:
        if row['market'] == 'totals':
            totals[row['bookmaker']].append(row)
    if not totals:
        return None

    def balance(row):
        prices = row['decimal_odds']
        return abs(log(prices['over'] / prices['under'])), row['line']

    def priority(row):
        book = row['bookmaker']
        times = [aware(row['quoted_at'])]
        if book in h2h:
            times.append(aware(h2h[book]['quoted_at']))
        return book not in h2h, -min(times).timestamp(), book, row['line']

    return min((min(group, key=balance) for group in totals.values()), key=priority)


def bound_odds(fixture, root, evidence, team_ids, cutoff):
    """Read all three retained responses and verify the exact fixture and team identities."""
    league = fixture['cohort']
    board = read_evidence(root, evidence['espn'], cutoff=cutoff,
                          expected=f'https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard')
    page = read_evidence(root, evidence['events'], cutoff=cutoff, expected=BASE+'/v1/events')
    event_id = evidence['event_id']
    raw = read_evidence(root, evidence['odds'], cutoff=cutoff, expected=BASE+'/v1/events/'+event_id+'/odds')
    target, = (e for e in board['events'] if 'espn:'+e['id'] == fixture['match_id'])
    _, teams = scheduled_competition(target)
    event, = (e for e in page['data'] if e['id'] == event_id)
    if (league not in LEAGUES or (event['league']['id'],event['league']['name']) != LEAGUES[league]
            or not any(x.get('slug') == league for x in board['leagues'])
            or raw['event_id'] != event_id or event['sport'] != 'football' or event['status'] != 'scheduled'
            or target['status']['type']['state'] != 'pre' or target['status']['type']['completed'] is not False
            or not aware(cutoff) < aware(fixture['kickoff_at']) == aware(event['scheduled_at']) == aware(target['date'])):
        raise ValueError('club market competition, schedule or response identity mismatch')
    if league == 'jpn.1' and (
            fixture.get('competition', league) != league or fixture['stage'] != 'regular'
            or fixture.get('score_basis') != 'regulation_90' or target['season']['slug'] != 'regular-season'):
        raise ValueError('club market requires the original adult regular J1 fixture')
    for side in ('home','away'):
        reviewed = team_ids[league][fixture[side]]
        provider = event[side+'_team']
        if ('espn:'+teams[side]['id'] != fixture[side] or reviewed['espn_name'] != teams[side]['displayName']
                or (provider['id'],provider['name']) != (reviewed['provider_id'],reviewed['provider_name'])):
            raise ValueError('club market home/away identity mismatch')
    return raw


def attach(fixture, root, evidence, team_ids, cutoff, *, maximum_age=MAXIMUM_QUOTE_AGE):
    """Read and verify all three raw responses before admitting their prices."""
    raw = bound_odds(fixture, root, evidence, team_ids, cutoff)
    rows = fresh_rows(raw, evidence['odds'], fixture['match_id'], aware(cutoff), maximum_age=maximum_age)
    result = dict(fixture, market_snapshots=rows, market_evidence=evidence)
    if fixture['cohort'] == 'jpn.1':
        reviewed = team_ids['jpn.1']
        result['market_team_ids'] = {fixture[side]: reviewed[fixture[side]] for side in ('home', 'away')}
        result['cutoff_at'] = aware(cutoff).isoformat()
    result.pop('total_quote', None)
    choice = choose_total(rows)
    if choice:
        query = {k:choice[k] for k in ('match_id','provider_match_id','bookmaker','line','quoted_at','query_time',
                                        'source_url','source_sha256')}
        result['total_quote'] = dict(query, market='asian_total', verified=True,
                                     over_odds=choice['decimal_odds']['over'], under_odds=choice['decimal_odds']['under'])
    result['market_availability'] = ('FRESH_SAME_BOOK_PAIR' if choice and any(
        r['market']=='1x2' and r['bookmaker']==choice['bookmaker'] for r in rows)
        else 'FRESH_TOTAL_ONLY' if choice else 'NO_FRESH_VERIFIED_TOTAL')
    if maximum_age != MAXIMUM_QUOTE_AGE:
        result['market_availability'] = {'FRESH_SAME_BOOK_PAIR': 'AGE_WINDOW_SAME_BOOK_PAIR',
                                         'FRESH_TOTAL_ONLY': 'AGE_WINDOW_TOTAL_ONLY',
                                         'NO_FRESH_VERIFIED_TOTAL': 'NO_TOTAL_IN_AGE_WINDOW'}[result['market_availability']]
        result['quote_age_window_seconds'] = maximum_age.total_seconds()
    return result


def verify_j1_quote(fixture, quote, root, h2h=None):
    """Reproduce the same-book quote from retained raw rows at the original cutoff."""
    source, cutoff = fixture['market_evidence'], fixture['cutoff_at']
    if (fixture['cohort'] != 'jpn.1' or source['odds']['url'] != quote['source_url']
            or source['odds']['sha256'] != quote['source_sha256']
            or source['event_id'] != quote['provider_match_id']
            or fixture['match_id'] != quote['match_id']
            or aware(source['odds']['captured_at']) != aware(quote['query_time'])):
        return False, 'QUOTE_SOURCE_EVENT_ENDPOINT_MISMATCH', None
    raw = bound_odds(fixture, root, source, {'jpn.1': fixture['market_team_ids']}, cutoff)
    if raw.get('stale') is not False:
        return False, 'QUOTE_SOURCE_FEED_STALE', None
    rows = fresh_rows(raw, source['odds'], fixture['match_id'], aware(cutoff))
    originals = fixture['market_snapshots']
    rows = [row for row in rows if row['bookmaker'] == quote['bookmaker'] and row in originals]
    totals = [row for row in rows if row['market'] == 'totals' and row['line'] == quote['line']]
    if len(totals) != 1:
        return False, 'QUOTE_RAW_BOOKMAKER_MARKET_NOT_UNIQUE_OR_FRESH', None
    total, = totals
    if total['decimal_odds'] != dict(over=quote['over_odds'], under=quote['under_odds']):
        return False, 'QUOTE_RAW_TOTAL_LINE_OR_PRICES_DIFFER', None
    if h2h is not None and not any(row['market'] == '1x2' and row['decimal_odds'] == h2h for row in rows):
        return False, 'QUOTE_RAW_1X2_PRICES_OR_CLOCK_DIFFER', None
    return True, None, total['quoted_at']
