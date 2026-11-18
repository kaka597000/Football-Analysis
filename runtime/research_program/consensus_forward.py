"""Freeze and settle the prospective named-bookmaker comparison."""
import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import book_consensus_study as consensus
from . import three_arm_forward as market
from . import three_arm_model as score
from . import three_arm_review as settlement
from . import transformer_forward
from publication import atomic_json
from research.analysis_freeze import aware, digest, save
from storage_lock import exclusive_lock

EXPERIMENT = Path('workspace-updates/20261118-market-signals')
PARENT = Path('workspace-updates/20261118-transformer/forward')
TRACK = 'named-book-consensus-first'
ARMS = ['same_cutoff_pinnacle', 'equal_named_books', 'inverse_overround_named_books']


def build_groups(snapshot):
    """Keep the newest regional feed per named brand; discard ambiguous tied prices."""
    by_brand = defaultdict(list)
    for row in snapshot['observations']:
        value = row['value']
        key = value['bookmaker']
        if value['market'] != 'h2h' or key == 'matchbook' or key.startswith(('betfair_ex', 'smarkets')):
            continue
        # The provider labels regional offerings of the same brand with these suffixes.
        brand = key.rsplit('_', 1)[0] if key.endswith(('_fr', '_de', '_nl', '_se', '_it', '_uk', '_eu')) else key
        by_brand[brand].append((aware(value['quoted_at']), key, value))
    groups = {}
    for brand, rows in by_brand.items():
        newest = max(r[0] for r in rows)
        tied = [r for r in rows if r[0] == newest]
        prices = {tuple(r[2]['odds'][side] for side in score.SIDES) for r in tied}
        if len(prices) == 1:
            groups[brand] = {'decimal': list(prices.pop()), 'quoted_at': newest.isoformat(),
                             'keys': sorted(r[1] for r in tied)}
    return groups


def matched(data, cutoff, run):
    if not run.endswith('-bra1') or Path(run).name != run:
        raise ValueError('expected Brazil archive run name')
    path = data / 'market/runs' / run
    meta = market.read(path / 'source-metadata.json')
    reconstructed = market.feed.build(path, meta['odds'], meta['identity'], competition='bra.1', cutoff=cutoff,
                                      now=aware(cutoff), market_max_age_seconds=3600)
    return {r['target']['match_id']: r for r in reconstructed['snapshots']}


def forecast(parent, snapshot, cutoff, run):
    f = parent['fixture']
    t = snapshot['target']
    if (t['home_id'], t['away_id'], aware(t['kickoff_at'])) != (f['home'], f['away'], aware(f['kickoff_at'])):
        raise ValueError('market and registered team identities differ')
    reference, brands = market.market_pair(snapshot), build_groups(snapshot)
    if not reference or reference['bookmaker'] != 'pinnacle' or 'pinnacle' not in brands or len(brands) < 3:
        return None
    pool = consensus.book_panel({brand: row['decimal'] for brand, row in brands.items()})
    quote = score._quote_values(reference, aware(cutoff))
    old = score._matrix(parent['payload']['arms']['market_baseline']['matrix'])
    base = score._market_matrix(old, quote)
    line = float(quote[1])
    others = consensus.project_panel(base, pool, line, quote[2])
    result = {}
    for arm, matrix in zip(ARMS, (base, others['equal'], others['inverse_margin']), strict=True):
        picks = score.score_picks(matrix, line=line)
        ranked = np.argsort(-matrix.ravel(), kind='stable')[:3]
        picks['top3'] = [f'{int(i)//matrix.shape[1]}-{int(i)%matrix.shape[1]}' for i in ranked]
        picks['matrix'] = matrix.tolist()
        result[arm] = picks
    return {'run': run, 'source_sha256': reference['source_sha256'], 'registered_parent': parent['record_sha256'],
            'reference': reference, 'named_books': brands, 'panel': pool, 'arms': result}


def registered(data):
    registration = market.read(data / EXPERIMENT / 'registration.json')
    plan, fixtures, parents = transformer_forward.records(data / PARENT)
    if (registration['forward_plan_parent'] != plan['record_sha256'] or set(registration['forward_ids']) != set(fixtures)
            or registration['forward_arms']['consensus'] != ARMS):
        raise ValueError('forward registration differs')
    return registration, fixtures, parents


def firsts(batch, fixtures):
    records = {}
    for mid, fixture in fixtures.items():
        path = market.first_path(batch, mid, TRACK)
        if path.exists():
            row = settlement._first_freeze(path, TRACK)
            f = row['fixture']
            if (f['match_id'] != mid or (f['home'], f['away']) != (fixture['home'], fixture['away'])
                    or not aware(f['cutoff_at']) <= aware(row['recorded_at']) < aware(fixture['kickoff_at'])):
                raise ValueError('bad consensus freeze')
            records[mid] = row
    return records


def issue(data, batch, run):
    with exclusive_lock(batch / '.issue.lock'):
        reg, fixtures, parents = registered(data)
        now = datetime.now(timezone.utc)
        cutoff = now.isoformat()
        if aware(reg['registered_at']) > now:
            raise ValueError('future registration')
        previous = firsts(batch, fixtures)
        pending = [mid for mid, f in fixtures.items() if mid not in previous and now < aware(f['kickoff_at'])]
        if pending:
            snapshots = matched(data, cutoff, run)
            for mid in pending:
                if mid not in snapshots:
                    continue
                payload = forecast(parents[mid], snapshots[mid], cutoff, run)
                if payload:
                    payload['registration'] = digest(reg)
                    f = dict(fixtures[mid], cutoff_at=cutoff)
                    market.copy_first(batch, batch / 'freezes' / (mid.replace(':', '-') + '.json'), f, TRACK, payload)
        records = firsts(batch, fixtures)
        return {'population': len(fixtures), 'issued': len(records), 'registered_books': {
            mid: row['payload']['panel']['n_books'] for mid, row in records.items()}}


def review(data, batch, replay=False):
    reg, fixtures, parents = registered(data)
    records = firsts(batch, fixtures)
    matches, issues = settlement._results(data, fixtures, datetime.now(timezone.utc))
    observations = []
    cache = {}
    for mid, fixture in fixtures.items():
        row, actual = records.get(mid), matches.get(mid)
        outcomes = {}
        if row:
            body = row['payload']
            if body['registration'] != digest(reg) or body['registered_parent'] != parents[mid]['record_sha256']:
                raise ValueError('first-issue registration or parent differs')
            if replay:
                key = (row['fixture']['cutoff_at'], body['run'])
                if key not in cache:
                    cache[key] = matched(data, *key)
                original = forecast(parents[mid], cache[key][mid], *key)
                for k in ('source_sha256', 'registered_parent', 'reference', 'named_books'):
                    if body[k] != original[k]:
                        raise ValueError('consensus replay mismatch in ' + k)
                for arm in ARMS:
                    transformer_forward._compare(body['arms'][arm]['matrix'], original['arms'][arm]['matrix'])
                    for field in ('primary', 'wdl', 'top3'):
                        if body['arms'][arm][field] != original['arms'][arm][field]:
                            raise ValueError('consensus selection differs')
                    for field in ('line', 'direction'):
                        if body['arms'][arm]['total'][field] != original['arms'][arm]['total'][field]:
                            raise ValueError('consensus total differs')
            if actual:
                for arm, detail in body['arms'].items():
                    outcomes[arm] = settlement._outcome(detail, actual)
                    outcomes[arm]['score_top3'] = f"{actual['home']}-{actual['away']}" in detail['top3']
        observations.append({'match_id': mid, 'result': actual, 'source_issues': issues.get(mid, []),
                             'freeze_sha256': row['record_sha256'] if row else None, 'outcomes': outcomes,
                             **settlement._matchday(fixture)})
    summary = {}
    for arm in ARMS:
        summary[arm] = {}
        for m in ('score_top1', 'score_top3', 'wdl', 'total_strict_full_win'):
            values = [r['outcomes'][arm][m] for r in observations if arm in r['outcomes'] and r['outcomes'][arm][m] is not None]
            summary[arm][m] = {'hits': sum(values), 'n': len(values)}
    report = {'population': len(fixtures), 'issued': len(records), 'settled': len(matches), 'pending': len(fixtures)-len(matches),
              'metrics': summary, 'rows': observations,
              'paired': {arm: {m: settlement._paired(observations, ARMS[0], arm, m)
                               for m in ('score_top1', 'score_top3', 'wdl', 'total_strict_full_win')} for arm in ARMS[1:]}}
    if not replay:
        group = dict(next(iter(fixtures.values())), match_id='consensus-review:' + digest(reg))
        stamps = [reg['registered_at'], *(row['recorded_at'] for row in records.values()),
                  *(result['source']['captured_at'] for result in matches.values())]
        group['cutoff_at'] = max(stamps, key=aware)
        save(report, group, stage='review', track='named-book-consensus-review', root=batch / 'reviews',
             idempotency_key=digest([group['cutoff_at'], report]))
        atomic_json(batch / 'review.json', report)
    return {k: report[k] for k in ('population', 'issued', 'settled', 'pending')}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('issue', 'review', 'replay'))
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--run')
    args = parser.parse_args()
    batch = args.state_dir / EXPERIMENT / 'consensus/forward'
    batch.mkdir(exist_ok=True, parents=True)
    if args.action == 'issue' and not args.run:
        parser.error('--run required for issue')
    print(json.dumps(issue(args.state_dir, batch, args.run) if args.action == 'issue'
                     else review(args.state_dir, batch, args.action == 'replay'), sort_keys=True))
