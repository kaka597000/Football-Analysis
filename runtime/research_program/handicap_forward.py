"""Freeze the genuine, same-event Pinnacle handicap probe and settle registered first issues."""

import argparse
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path

import numpy as np

from . import handicap_matrix_study as handicap
from . import market_path_study as movement
from . import three_arm_forward as market
from . import three_arm_review as settlement
from .transformer_forward import _compare
from research.analysis_freeze import aware, digest
from storage_lock import exclusive_lock

TRACK = 'research-verified-pinnacle-handicap'
FWD_PROTOCOL = '070a7f2ce245edba50ef014594dbf2e1142c20b99ebf10fbece93bfc9c19aaab'
RULES = {
    'markets': (
        'https://the-odds-api.com/sports-odds-data/betting-markets.html',
        '1e58e43dbc04bc25b4fd6da696392d2e6f047b4af9a8d081ce9019c555d30d65',
    ),
    'pinrules': (
        'https://www.pinnacle.com/en/future/betting-rules',
        '61d7b4213273b289725a7e525e2582c368df6d5f499a2fdccf83b7c0930cd8ba',
    ),
}
ENDPOINT = 'https://api.the-odds-api.com/v4/sports/soccer_brazil_campeonato/odds/?regions=eu&markets=spreads%2Ch2h%2Ctotals&oddsFormat=decimal'


def _blob(root, reference):
    stored = root / reference['file']
    if stored.parent != root or market.file_sha256(stored) != reference['file_sha256']:
        raise ValueError('retained source file checksum mismatch')
    plain = gzip.decompress(stored.read_bytes())
    if hashlib.sha256(plain).hexdigest() != reference['sha256']:
        raise ValueError('raw evidence checksum mismatch')
    return plain


def parse_pinnacle(event, observed, cutoff):
    books = [b for b in event['bookmakers'] if b['key'] == 'pinnacle']
    if len(books) != 1:
        return None, 'missing or ambiguous Pinnacle event'

    def market_rows(key, names):
        entries = [m for m in books[0]['markets'] if m['key'] == key]
        if len(entries) != 1:
            raise ValueError('missing or ambiguous ' + key)
        item = entries[0]
        stamp = aware(item['last_update'])
        if not stamp <= observed <= cutoff or cutoff - stamp > timedelta(hours=1):
            raise ValueError('late or stale Pinnacle ' + key)
        rows = {x['name']: x for x in item['outcomes']}
        if len(rows) != len(item['outcomes']) or set(rows) != set(names):
            raise ValueError('Pinnacle market outcomes differ from event ' + key)
        if any(not np.isfinite(x['price']) or x['price'] <= 1 for x in rows.values()):
            raise ValueError('invalid decimal prices ' + key)
        return rows, item['last_update']

    home, away = event['home_team'], event['away_team']
    try:
        h2h, ts1 = market_rows('h2h', [home, 'Draw', away])
        totals, ts2 = market_rows('totals', ['Over', 'Under'])
        spreads, ts3 = market_rows('spreads', [home, away])
        over, under = totals['Over'], totals['Under']
        home_ah, away_ah = spreads[home], spreads[away]
        if (
            type(home_ah['point']) not in (int, float)
            or type(away_ah['point']) not in (int, float)
            or not np.isfinite(home_ah['point'])
            or home_ah['point'] != -away_ah['point']
            or not (4 * float(home_ah['point'])).is_integer()
        ):
            raise ValueError('Pinnacle full-event team points are not opposite Asian handicaps')
        if (
            over['point'] != under['point']
            or not np.isfinite(over['point'])
            or over['point'] < 0
            or not (4 * float(over['point'])).is_integer()
        ):
            raise ValueError('Pinnacle total line missing or inconsistent')
    except (TypeError, ValueError, KeyError) as exc:
        return None, str(exc)
    quote = {
        'bookmaker': 'pinnacle',
        'h2h': dict(home=h2h[home]['price'], draw=h2h['Draw']['price'], away=h2h[away]['price']),
        'total': dict(line=over['point'], over=over['price'], under=under['price']),
    }
    ah = {
        'bookmaker': 'pinnacle',
        'home': home_ah['price'],
        'away': away_ah['price'],
        'line': home_ah['point'],
    }
    return {
        'provider': 'the-odds-api',
        'original_market': 'spreads',
        'regulation': '90_minutes_including_stoppage',
        'event_id': event['id'],
        'source_teams': [home, away],
        'market': quote,
        'handicap': ah,
        'quoted_at': {'h2h': ts1, 'totals': ts2, 'spreads': ts3},
        'away_handicap_line': away_ah['point'],
    }, None


def compute(data, batch, cutoff):
    probe = batch.parent / 'probe'
    meta = market.read(probe / 'source.json')
    observed = aware(meta['captured_at'])
    if (
        meta['source_url'] != ENDPOINT
        or meta['http_status'] != 200
        or observed > cutoff
        or aware(meta['requested_at']) > observed
        or market.file_sha256(batch.parent / 'forward-protocol.json') != FWD_PROTOCOL
        or market.file_sha256(batch.parent / 'protocol.json') != handicap.PROTOCOL
    ):
        raise ValueError('incorrect probe timing, endpoint or registration')
    registration = market.read(batch.parent / 'forward-protocol.json')
    if (
        not aware(registration['registered_at']) <= aware(meta['requested_at'])
        or registration['protocol_sha256'] != handicap.PROTOCOL
    ):
        raise ValueError('unexpected registration time or candidate')
    events = json.loads(_blob(probe, meta))
    by_id = {e['id']: e for e in events}
    if len(by_id) != len(events) or len(events) != meta['events']:
        raise ValueError('provider event collision')
    rules = market.read(probe / 'rules.json')
    for key, (url, sha) in RULES.items():
        ref = rules[key]
        if ref['url'] != url or ref['sha256'] != sha or aware(ref['captured_at']) > cutoff:
            raise ValueError('unverified home handicap or settlement convention')
        _blob(probe, ref)
    movement_issue = movement._original(batch.parents[1] / 'movement/forward')
    prior = movement_issue['payload']
    fixtures = {mid: dict(f, cutoff_at=cutoff.isoformat()) for mid, f in prior['fixtures'].items()}
    if set(fixtures) != set(registration['fixtures']) or aware(movement_issue['recorded_at']) >= cutoff:
        raise ValueError('prior market-to-fixture mapping or population invalid')
    items = {}
    for mid, f in fixtures.items():
        if aware(f['kickoff_at']) <= cutoff:
            raise ValueError('pregame capture only')
        source = prior['predictions'][mid]
        prev = source['path']['latest']
        path = data / source['path']['latest_source'] / 'odds.json'
        if market.file_sha256(path) != prev['source_sha256']:
            raise ValueError('previous provider event mapping bytes changed')
        earlier = [e for e in market.read(path) if e['id'] == prev['provider_match_id']]
        event = by_id.get(prev['provider_match_id'])
        if event is None:
            items[mid] = {'arms': {}, 'status': 'not present in current probe'}
            continue
        if (
            len(earlier) != 1
            or event['sport_key'] != 'soccer_brazil_campeonato'
            or any(earlier[0][k] != event[k] for k in ('id', 'home_team', 'away_team', 'sport_key'))
            or aware(event['commence_time']) != aware(f['kickoff_at'])
        ):
            raise ValueError('new response has changed match or venue identity')
        parsed, reason = parse_pinnacle(event, observed, cutoff)
        if parsed is None:
            items[mid] = {'arms': {}, 'status': reason}
            continue
        freeze = settlement._first_freeze(
            data / movement.REFERENCE / 'freezes' / (mid.replace(':', '-') + '.json'),
            'transformer-research-score',
        )
        native = freeze['payload']['native_current']
        if (
            freeze['record_sha256'] != source['original_record_sha256']
            or digest(native['matrix']) != source['native_prior_sha256']
        ):
            raise ValueError('native goals prior identity differs')
        arms, diag = handicap.project(native['matrix'], parsed['market'], parsed['handicap'])
        picks = {k: movement.forecast(m, parsed['market']['total']['line']) for k, m in arms.items()}
        items[mid] = {
            'status': 'issued',
            'arms': picks,
            'quote': parsed,
            'diagnostic': diag,
            'source_sha256': meta['sha256'],
            'native_prior_sha256': source['native_prior_sha256'],
        }
    return {
        'fixtures': fixtures,
        'predictions': items,
        'protocol_sha256': FWD_PROTOCOL,
        'model_protocol_sha256': handicap.PROTOCOL,
        'rules_evidence': {k: v[1] for k, v in RULES.items()},
        'raw_sha256': meta['sha256'],
        'venue_mapping_freeze': movement_issue['record_sha256'],
        'automatic_promotion': False,
    }


def original(batch):
    original = settlement._first_freeze(batch / 'first-issue.json', TRACK)
    if (
        original != market.read(market.first_path(batch, original['match_id'], TRACK))
        or original['payload']['protocol_sha256'] != FWD_PROTOCOL
    ):
        raise ValueError('original AH first issue differs')
    return original


def capture(data, batch):
    batch.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(batch / '.capture.lock'):
        dest = batch / 'first-issue.json'
        if dest.exists():
            return original(batch)
        mid = 'market-ah:' + FWD_PROTOCOL
        canonical = market.first_path(batch, mid, TRACK)
        if canonical.is_file():
            old = settlement._first_freeze(canonical, TRACK)
            return market.copy_first(batch, dest, old['fixture'], TRACK)
        cutoff = datetime.now(timezone.utc)
        payload = compute(data, batch, cutoff)
        fixture = {
            'match_id': mid,
            'cutoff_at': cutoff.isoformat(),
            'kickoff_at': min(f['kickoff_at'] for f in payload['fixtures'].values()),
        }
        return market.copy_first(batch, dest, fixture, TRACK, payload)


def replay(data, batch):
    r = original(batch)
    actual = compute(data, batch, aware(r['fixture']['cutoff_at']))
    expected = r['payload']
    for mid, rebuilt in actual['predictions'].items():
        stored = expected['predictions'][mid]
        for key, x in rebuilt['arms'].items():
            if x['top3'] != stored['arms'][key]['top3']:
                raise ValueError('reconstructed top three differ')
            _compare(
                {k: v for k, v in x.items() if k != 'top3'},
                {k: v for k, v in stored['arms'][key].items() if k != 'top3'},
            )
        if rebuilt['arms'].keys() != stored['arms'].keys():
            raise ValueError('different market coverage')
        if rebuilt['arms']:
            _compare(rebuilt['diagnostic'], stored['diagnostic'])
        if {k: v for k, v in rebuilt.items() if k not in ('arms', 'diagnostic')} != {
            k: v for k, v in stored.items() if k not in ('arms', 'diagnostic')
        }:
            raise ValueError('recomputed quote or identity differs')
    if (
        actual.keys() != expected.keys()
        or actual['predictions'].keys() != expected['predictions'].keys()
        or {k: v for k, v in actual.items() if k != 'predictions'}
        != {k: v for k, v in expected.items() if k != 'predictions'}
    ):
        raise ValueError('reconstructed batch differs')
    return r


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('action', choices=('capture', 'replay', 'review'))
    ap.add_argument('--state-dir', type=Path, required=True)
    ap.add_argument('--batch', type=Path, required=True)
    args = ap.parse_args()
    data, batch = args.state_dir.resolve(), args.batch.resolve()
    if args.action == 'review':
        with exclusive_lock(batch / '.review.lock'):
            result = movement.review_first(data, batch, original(batch), handicap.NAMES, TRACK)
    else:
        r = capture(data, batch) if args.action == 'capture' else replay(data, batch)
        result = {
            'population': len(r['payload']['predictions']),
            'issued': sum(bool(v['arms']) for v in r['payload']['predictions'].values()),
            'plan_sha256': r['record_sha256'],
            'cutoff_at': r['fixture']['cutoff_at'],
        }
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
