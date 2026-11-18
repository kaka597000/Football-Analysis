"""Compare a closing/current-only market projection with a fixed same-book movement tilt."""

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import numpy as np

from . import three_arm_forward as market
from . import three_arm_model as score
from . import three_arm_review as settlement
from publication import atomic_json
from research.analysis_freeze import aware, digest, save
from storage_lock import exclusive_lock

ARMS = ('latest_only', 'latest_plus_movement')
METRICS = ('score_top1', 'score_top3', 'wdl', 'total_strict_full_win')
TRACK = 'research-same-book-market-path'
REFERENCE = Path('workspace-updates/20261118-transformer/forward')
PROTOCOL = '74720443591a2a99eea7ff9284dcd5a583fc30c81bd240a4007de89120b7f871'
HOUR = timedelta(hours=1)


def arms(base, early, latest):
    """Each endpoint is projected at its own Asian line; compare joint probabilities."""
    if not latest['bookmaker'] or early['bookmaker'] != latest['bookmaker']:
        raise ValueError('movement requires one bookmaker')
    base = score._matrix(base)
    earlier, closing = [
        score._market_matrix(base, score._quote_values(q, None, proxy=True)) for q in (early, latest)
    ]
    support = closing > 0
    if not np.array_equal(support, earlier > 0):
        raise ValueError('common goal prior has incompatible market support')
    relative = np.log(closing[support]) - np.log(earlier[support])
    moved = closing.copy()
    moved[support] *= np.exp(0.25 * np.clip(relative, -np.log(2.0), np.log(2.0)))
    return {ARMS[0]: closing, ARMS[1]: moved / moved.sum()}


def forecast(matrix, line):
    picks = score.score_picks(matrix, line=line)
    return dict(picks, top3=score.top_three_scores(matrix), matrix=matrix.tolist())


def outcome(picks, result):
    value = settlement._outcome(picks, result)
    value['score_top3'] = f"{result['home']}-{result['away']}" in picks['top3']
    return value


def summarize(rows, names=ARMS):
    reports = {}
    for name in names:
        results = [r['outcomes'][name] for r in rows if name in r['outcomes']]
        reports[name] = {
            'metrics': {
                k: {'hits': sum(r[k] is True for r in results), 'n': sum(r[k] is not None for r in results)}
                for k in METRICS
            },
            'total_settlements': dict(Counter(r['total_settlement'] for r in results)),
            'score_log_loss': float(np.mean([r['score_log_loss'] for r in results])) if results else None,
            'wdl_brier': float(np.mean([r['wdl_brier'] for r in results])) if results else None,
        }
    return {'arms': reports, 'paired': {k: settlement._paired(rows, *names, k) for k in METRICS}}


def move_type(early, latest):
    if early['total']['line'] != latest['total']['line']:
        return 'line_change'
    return 'same_line_price_move' if early['total'] != latest['total'] else 'same_line_no_total_price_move'


def _sources(data, cutoff):
    runs = []
    for path in sorted((data / 'market/runs').glob('*-bra1/market-channel.json')):
        channel = market.read(path)
        age = cutoff - aware(channel['created_at'])
        if (
            timedelta(0) <= age <= 24 * HOUR
            and channel.get('status') == 'VALIDATED'
            and not channel.get('retrospective_identity_replay')
        ):
            runs.append((aware(channel['created_at']), str(path.parent.relative_to(data))))
    return [p for _, p in sorted(runs)]


def _history(data, paths, cutoff, expected):
    runs = []
    for path in paths:
        directory = data / path
        if not directory.resolve().is_relative_to((data / 'market/runs').resolve()):
            raise ValueError('unknown market channel path')
        channel = market.read(directory / 'market-channel.json')
        created = aware(channel['created_at'])
        if not timedelta(0) <= cutoff - created <= 24 * HOUR:
            raise ValueError('market channel outside registered window')
        # Rebuild the board and bookmaker observations from both hash-checked provider responses.
        fixtures, _, _ = market.batch_inputs(directory, None, 'bra.1', channel['created_at'])
        f_by = {f['match_id']: f for f in fixtures}
        observations = {}
        for snap in channel['snapshots']:
            mid = snap['target']['match_id']
            if mid not in expected:
                continue
            f = f_by[mid]
            ref = expected[mid]
            if (
                any(ref[k] != f[k] for k in ('home', 'away', 'stage', 'cohort'))
                or aware(f['kickoff_at']) != aware(ref['kickoff_at'])
                or f['neutral'] is not None
                and f['neutral'] != ref['neutral']
            ):
                raise ValueError('different venue, sides, fixture or kickoff in market history')
            available = {}
            for book in {
                q['value']['bookmaker'] for q in snap['observations'] if q['field'] == 'market_quote'
            }:
                panel = market.market_pair(
                    dict(
                        snap,
                        observations=[
                            q
                            for q in snap['observations']
                            if q['field'] == 'market_quote' and q['value']['bookmaker'] == book
                        ],
                    )
                )
                if panel:
                    available[book] = panel
            observations[mid] = available
        runs.append((created, path, observations))
    return sorted(runs, key=lambda r: (r[0], r[1]))


def _match_path(runs, mid, cutoff):
    timeline = []
    for _, path, observations in runs:
        books = observations.get(mid, {})
        if books:
            timeline.append((path, books))
    # First choose a valid fresh latest pair using the same book priority as the existing benchmark.
    for latest_path, latest_books in reversed(timeline):
        for book in sorted(latest_books, key=lambda k: (k not in market.BOOK_PRIORITY, k)):
            latest = latest_books[book]
            clock = ('h2h_quoted_at', 'total_quoted_at')
            if any(not timedelta(0) <= cutoff - aware(latest[k]) <= HOUR for k in clock):
                continue
            for first_path, books in timeline:
                early = books.get(book)
                if (
                    early
                    and early['provider'] == latest['provider']
                    and early['provider_match_id'] == latest['provider_match_id']
                    and aware(early['captured_at']) < aware(latest['captured_at'])
                    and all(aware(early[k]) < aware(latest[k]) for k in clock)
                ):
                    return {
                        'early_source': first_path,
                        'latest_source': latest_path,
                        'early': early,
                        'latest': latest,
                        'total_movement': move_type(early, latest),
                    }
            return {'reason': 'missing_earlier_same_book_pair_with_advancing_timestamps', 'latest': latest}
    return {'reason': 'missing_fresh_latest_market_pair'}


def _compute(data, protocol, paths, cutoff):
    source = data / REFERENCE
    original = settlement._first_freeze(source / 'plan.json', 'transformer-research-plan')
    if market.file_sha256(source / 'plan.json') != protocol['fixture_registration_sha256']:
        raise ValueError('fixture registry changed')
    fixtures = {f['match_id']: dict(f, cutoff_at=cutoff.isoformat()) for f in original['payload']['fixtures']}
    if set(fixtures) != set(protocol['fixture_ids']) or any(
        aware(f['kickoff_at']) <= cutoff for f in fixtures.values()
    ):
        raise ValueError('unregistered or already started fixture')
    runs = _history(data, paths, cutoff, fixtures)
    predictions = {}
    for mid, f in fixtures.items():
        path = _match_path(runs, mid, cutoff)
        prior = settlement._first_freeze(
            source / 'freezes' / (mid.replace(':', '-') + '.json'), 'transformer-research-score'
        )
        native = prior['payload']['native_current']
        if (
            prior['match_id'] != mid
            or prior['payload']['plan_sha256'] != original['record_sha256']
            or aware(prior['recorded_at']) > cutoff
            or digest(native['matrix']) != native['matrix_sha256']
        ):
            raise ValueError('native prior does not match original first issue')
        values = {}
        if 'early' in path:
            latest = path['latest']
            values = {
                k: forecast(m, latest['total']['line'])
                for k, m in arms(native['matrix'], path['early'], latest).items()
            }
        predictions[mid] = {
            'original_record_sha256': prior['record_sha256'],
            'native_prior_sha256': native['matrix_sha256'],
            'path': path,
            'arms': values,
        }
    return {
        'fixtures': fixtures,
        'predictions': predictions,
        'sources': paths,
        'automatic_promotion': False,
        'protocol_sha256': PROTOCOL,
    }


def _original(batch):
    r = settlement._first_freeze(batch / 'first-issue.json', TRACK)
    if (
        r != market.read(market.first_path(batch, r['match_id'], TRACK))
        or r['payload']['protocol_sha256'] != PROTOCOL
    ):
        raise ValueError('market movement alias differs from original freeze')
    return r


def capture(data, batch):
    batch.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(batch / '.capture.lock'):
        path = batch / 'first-issue.json'
        protocol = market.read(batch.parent / 'protocol.json')
        if market.file_sha256(batch.parent / 'protocol.json') != PROTOCOL:
            raise ValueError('research registration changed')
        if path.is_file():
            return _original(batch)
        group_id = 'market-movement:' + PROTOCOL
        canonical = market.first_path(batch, group_id, TRACK)
        if canonical.is_file():
            stored = settlement._first_freeze(canonical, TRACK)
            return market.copy_first(batch, path, stored['fixture'], TRACK)
        cutoff = datetime.now(timezone.utc)
        if aware(protocol['registered_at']) > cutoff:
            raise ValueError('protocol postdates cutoff')
        payload = _compute(data, protocol, _sources(data, cutoff), cutoff)
        fixture = {
            'match_id': 'market-movement:' + PROTOCOL,
            'cutoff_at': cutoff.isoformat(),
            'kickoff_at': min(f['kickoff_at'] for f in payload['fixtures'].values()),
        }
        return market.copy_first(batch, path, fixture, TRACK, payload)


def replay(data, batch):
    first = _original(batch)
    p = first['payload']
    protocol = market.read(batch.parent / 'protocol.json')
    if market.file_sha256(batch.parent / 'protocol.json') != PROTOCOL:
        raise ValueError('registration checksum differs')
    computed = _compute(data, protocol, p['sources'], aware(first['fixture']['cutoff_at']))
    # Hash integrity applies to stored rows; numerical reconstruction on other CPUs uses close probabilities.
    from .transformer_forward import _compare

    for mid, value in computed['predictions'].items():
        expected = p['predictions'][mid]
        if {k: v for k, v in value.items() if k != 'arms'} != {
            k: v for k, v in expected.items() if k != 'arms'
        }:
            raise ValueError('source identity or market path differs')
        for name, new in value['arms'].items():
            old = expected['arms'][name]
            if new['top3'] != old['top3']:
                raise ValueError('top three forecast differs')
            _compare(
                {k: v for k, v in new.items() if k != 'top3'}, {k: v for k, v in old.items() if k != 'top3'}
            )
    if (
        computed['fixtures'] != p['fixtures']
        or set(computed['predictions']) != set(p['predictions'])
        or any(
            computed['predictions'][mid]['arms'].keys() != value['arms'].keys()
            for mid, value in p['predictions'].items()
        )
    ):
        raise ValueError('forward population or coverage changed')
    return {
        'population': len(p['predictions']),
        'issued': sum(bool(v['arms']) for v in p['predictions'].values()),
        'plan_sha256': first['record_sha256'],
    }


def review_first(data, batch, first, names, track):
    body = first['payload']
    results, issues = settlement._results(data.resolve(), body['fixtures'], datetime.now(timezone.utc))
    rows = []
    for mid, f in body['fixtures'].items():
        pred = body['predictions'][mid]
        result = results.get(mid)
        rows.append(
            {
                'match_id': mid,
                **settlement._matchday(f),
                'source_issues': issues.get(mid, []),
                'result': result,
                'issued': bool(pred['arms']),
                'outcomes': {k: outcome(a, result) for k, a in pred['arms'].items()} if result else {},
            }
        )
    value = {
        'population': len(rows),
        'issued': sum(r['issued'] for r in rows),
        'pending': len(rows) - len(results),
        'settled': len(results),
        'rows': rows,
        'plan_sha256': first['record_sha256'],
        **summarize(rows, names),
    }
    stamps = [first['recorded_at'], *[r['source']['captured_at'] for r in results.values()]]
    group = dict(
        first['fixture'],
        match_id='market-path-review:' + first['record_sha256'],
        cutoff_at=max(stamps, key=aware),
    )
    receipt = save(
        value,
        group,
        stage='review',
        track=track + '-review',
        root=batch / 'reviews',
        idempotency_key=digest(value),
    )
    atomic_json(batch / 'review.json', dict(value, review_sha256=receipt['sha256']))
    return {k: v for k, v in value.items() if k not in ('arms', 'paired', 'rows')}


def review(data, batch):
    with exclusive_lock(batch / '.review.lock'):
        return review_first(data, batch, _original(batch), ARMS, TRACK)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('action', choices=('capture', 'replay', 'review'))
    ap.add_argument('--state-dir', required=True, type=Path)
    ap.add_argument('--batch', required=True, type=Path)
    args = ap.parse_args()
    if args.action == 'capture':
        r = capture(args.state_dir.resolve(), args.batch.resolve())
        result = {
            'plan_sha256': r['record_sha256'],
            'cutoff_at': r['fixture']['cutoff_at'],
            'population': len(r['payload']['predictions']),
            'issued': sum(bool(p['arms']) for p in r['payload']['predictions'].values()),
        }
    elif args.action == 'replay':
        result = replay(args.state_dir.resolve(), args.batch.resolve())
    else:
        result = review(args.state_dir.resolve(), args.batch.resolve())
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
