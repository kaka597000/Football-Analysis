"""Issue and replay a matched four-model, prematch Transformer trial."""
import argparse
from collections import Counter
from datetime import datetime, timezone
from itertools import combinations
import hashlib
import json
from pathlib import Path
import tarfile
from unittest.mock import patch

import numpy as np

from . import sequence_forward as sequence
from . import three_arm_forward as market
from . import three_arm_model as score
from . import three_arm_review as settlement
from . import transformer_score as transformer
from prediction_engine import ROOT as RUNTIME, file_sha256, legacy
from publication import atomic_json
from research.analysis_freeze import aware, digest, save, verify
from storage_lock import exclusive_lock

ARMS = (*sequence.ARMS, 'transformer')
PLAN = 'transformer-research-plan'
SCORES = 'transformer-research-score'
REVIEW = 'transformer-research-review'
SEQ = Path('workspace-updates/20261118-sequence')
MARKET = Path('workspace-updates/20261118-three-arm')
read = market.read


def materials(data, batch, channel, registry):
    files = {
        'registration': batch.parent / 'registration/protocol.json',
        'transformer': batch.parent / 'history/artifact.json',
        'sequence': data / SEQ / 'history/artifact.json',
        'market': data / MARKET / 'training/artifact.json',
        'registry': registry,
        'channel': channel / 'market-channel.json',
    }
    binding = {k: file_sha256(p) for k, p in files.items()}
    binding['market_archive'] = channel.resolve().relative_to(data.resolve()).as_posix()
    binding['source'] = {k: file_sha256(p) for k, p in {
        'issuer': __file__, 'transformer': transformer.__file__, 'original_issuer': sequence.__file__,
        'sequence': sequence.sequence.__file__, 'market': score.__file__, 'market_inputs': market.__file__,
        'quote_parser': market.feed.__file__}.items()}
    return binding, {k: read(files[k]) for k in ('registration', 'transformer', 'sequence', 'market')}


def inputs(data, channel, artifacts, cutoff):
    protocol = artifacts['registration']
    if (protocol['schema'] != 'WCPP-TRANSFORMER-RESEARCH-REGISTRATION-1' or protocol['arms'] != list(ARMS)
            or protocol['automatic_promotion'] is not False or aware(protocol['recorded_at']) > aware(cutoff)
            or artifacts['market']['market_weight'] != 1
            or any(aware(artifacts[k]['cutoff_at']) > aware(cutoff) for k in ('market', 'sequence', 'transformer'))):
        raise ValueError('unexpected protocol, market baseline or training cutoff')
    fixtures, markets, sources = market.batch_inputs(channel, data / MARKET / 'identity', 'bra.1', cutoff)
    expected = {row['match_id']: row for row in protocol['fixtures']}
    if not fixtures or len(expected) != len(protocol['fixtures']) or {f['match_id'] for f in fixtures} != set(expected):
        raise ValueError('registered population mismatch')
    histories, seen = {}, {}
    for f in fixtures:
        mid = f['match_id']
        row = expected[mid]
        if (any(row[k] != f[k] for k in ('home', 'away', 'cohort', 'stage', 'neutral'))
                or aware(row['kickoff_at']) != aware(f['kickoff_at'])
                or row['identity_sha256'] not in {ref['response_sha256'] for ref in f['sources']}):
            raise ValueError('registered fixture mismatch: ' + mid)
        histories[mid] = [sequence.team_schedule(data / SEQ / 'forward/history', f[side], fixture=f, seen=seen)
                          for side in ('home', 'away')]
    return {'fixtures': fixtures, 'markets': markets, 'history': histories, 'sources': sources}


def predict(fixture, quote, panels, artifacts, registry):
    if not quote:
        return {'status': 'ABSTAIN_NO_PAIRED_MARKET', 'arms': {}}
    result = sequence.predict(fixture, quote, panels, artifacts['sequence'], artifacts['market'], registry)
    if not result['arms']:
        return result
    baseline = result['arms']['market_baseline']['matrix']
    matrix = transformer.apply(artifacts['transformer'], baseline, [p['features'] for p in panels])
    values = matrix.tolist()
    picks = score.score_picks(matrix, line=quote['total']['line'])
    result['arms']['transformer'] = dict(picks, matrix=values, matrix_sha256=digest(values))
    return result


def displayed(record):
    payload = record['payload']
    return {'match_id': record['match_id'], 'recorded_at': record['recorded_at'], 'record_sha256': record['record_sha256'],
            'status': payload['status'], 'arms': {name: {k: arm[k] for k in ('primary', 'wdl', 'total')}
                                                 for name, arm in payload['arms'].items()}}


def capture(data, batch, channel, registry):
    batch.mkdir(exist_ok=True, parents=True)
    with exclusive_lock(batch / '.transformer-capture.lock'):
        binding, artifacts = materials(data, batch, channel, registry)
        mid = 'transformer:' + binding['registration']
        plan_file = market.first_path(batch, mid, PLAN)
        if plan_file.exists():
            existing = settlement._first_freeze(plan_file, PLAN)
            if existing['payload']['binding'] != binding:
                raise ValueError('changed inputs for first plan')
            group, payload = existing['fixture'], None
        else:
            cutoff = datetime.now(timezone.utc).isoformat()
            dataset = inputs(data, channel, artifacts, cutoff)
            group = {'match_id': mid, 'cutoff_at': cutoff, 'kickoff_at': dataset['fixtures'][0]['kickoff_at']}
            payload = dict(dataset, binding=binding, arms=list(ARMS), automatic_promotion=False)
        plan = market.copy_first(batch, batch / 'plan.json', group, PLAN, payload)
        definition = plan['payload']
        rows = []
        for f in definition['fixtures']:
            mid = f['match_id']
            original = market.first_path(batch, mid, SCORES)
            payload = None
            if not original.is_file():
                if aware(f['kickoff_at']) <= datetime.now(timezone.utc):
                    rows.append({'match_id': mid, 'status': 'MISSED_PREMATCH'})
                    continue
                quote = definition['markets'][mid]
                panels = definition['history'][mid]
                payload = predict(f, quote, panels, artifacts, registry)
                payload.update(plan_sha256=plan['record_sha256'], market=quote, history_sha256=digest(panels))
            dest = batch / 'freezes' / (mid.replace(':', '-') + '.json')
            record = market.copy_first(batch, dest, f, SCORES, payload)
            if record['payload']['plan_sha256'] != plan['record_sha256']:
                raise ValueError('prediction belongs to another plan')
            rows.append(displayed(record))
        summary = {'schema': 'WCPP-TRANSFORMER-BATCH-1', 'plan_sha256': plan['record_sha256'],
                   'cutoff_at': plan['fixture']['cutoff_at'], 'population': len(rows), 'matches': rows}
        atomic_json(batch / 'summary.json', summary)
        return summary


def records(batch):
    plan = settlement._first_freeze(batch / 'plan.json', PLAN)
    definition = plan['payload']
    fixtures = {f['match_id']: f for f in definition['fixtures']}
    if (len(fixtures) != len(definition['fixtures']) or not fixtures or set(fixtures) != set(definition['markets'])
            or set(fixtures) != set(definition['history']) or definition['arms'] != list(ARMS)):
        raise ValueError('ambiguous plan or input population')
    captures = {}
    for path in sorted((batch / 'freezes').glob('*.json')):
        r = settlement._first_freeze(path, SCORES)
        mid, body = r['match_id'], r['payload']
        if (mid in captures or r['fixture'] != fixtures.get(mid) or body['plan_sha256'] != plan['record_sha256']
                or body['market'] != definition['markets'][mid] or body['history_sha256'] != digest(definition['history'][mid])
                or not aware(plan['fixture']['cutoff_at']) <= aware(plan['recorded_at']) <= aware(r['recorded_at'])
                < aware(r['fixture']['kickoff_at'])):
            raise ValueError('invalid first-issue inputs or identity')
        arms = body['arms']
        if (bool(arms) != (body['status'] == 'ISSUED_REAL_MARKET') or arms and (set(arms) != set(ARMS) or not body['market'])):
            raise ValueError('four comparable arms with a real quote required')
        for arm in arms.values():
            picks = score.score_picks(arm['matrix'], line=body['market']['total']['line'])
            if (digest(arm['matrix']) != arm['matrix_sha256'] or picks['primary'] != arm['primary'] or picks['wdl'] != arm['wdl']
                    or not arm['total'] or any(picks['total'][k] != arm['total'][k] for k in ('line', 'direction'))):
                raise ValueError('stored pick inconsistent with score matrix')
        captures[mid] = r
    return plan, fixtures, captures


def review(data, batch):
    with exclusive_lock(batch / '.transformer-review.lock'):
        plan, fixtures, captures = records(batch)
        results, issues = settlement._results(data.resolve(), fixtures, datetime.now(timezone.utc))
        rows = []
        for mid, f in fixtures.items():
            result, rec = results.get(mid), captures.get(mid)
            arms = rec['payload']['arms'] if rec else {}
            rows.append({'match_id': mid, **settlement._matchday(f), 'result': result,
                         'freeze_sha256': rec['record_sha256'] if rec else None, 'source_issues': issues.get(mid, []),
                         'status': 'SETTLED' if result else 'PENDING',
                         'outcomes': {k: settlement._outcome(arm, result) for k, arm in arms.items()} if result else {}})
        summaries = {}
        for k in ARMS:
            observed = [r['outcomes'][k] for r in rows if k in r['outcomes']]
            issued = sum(k in r['payload']['arms'] for r in captures.values())
            metrics = {}
            for metric in settlement.METRICS:
                flags = [r[metric] for r in observed if r[metric] is not None]
                metrics[metric] = {'n': len(flags), 'hits': sum(flags), 'accuracy': sum(flags) / len(flags) if flags else None}
            counts = Counter(r['total_settlement'] for r in observed)
            summaries[k] = {'forecasts': issued, 'coverage': issued / len(fixtures), 'real_line_selections': issued,
                            'metrics': metrics, 'total_settlements': {s: counts[s] for s in settlement.SETTLEMENTS},
                            'mean_score_log_loss': float(np.mean([r['score_log_loss'] for r in observed])) if observed else None,
                            'mean_wdl_brier': float(np.mean([r['wdl_brier'] for r in observed])) if observed else None}
        pairs = {b + '_minus_' + a: {k: settlement._paired(rows, a, b, k) for k in settlement.METRICS}
                 for a, b in combinations(ARMS, 2)}
        report = {'schema': 'WCPP-TRANSFORMER-REVIEW-1', 'plan_sha256': plan['record_sha256'],
                  'population': len(fixtures), 'issued': sum(bool(r['payload']['arms']) for r in captures.values()),
                  'settled': len(results), 'pending': len(fixtures) - len(results), 'arms': summaries,
                  'paired': pairs, 'rows': rows, 'automatic_promotion': False}
        stamps = [plan['recorded_at']] + [r['recorded_at'] for r in captures.values()]
        stamps.extend(r['source']['captured_at'] for r in results.values())
        group = dict(plan['fixture'], match_id='transformer-review:' + plan['record_sha256'], cutoff_at=max(stamps, key=aware))
        receipt = save(report, group, stage='review', track=REVIEW, root=batch / 'reviews', idempotency_key=digest(report))
        receipt['path'] = str(Path(receipt['path']).resolve().relative_to(batch.resolve()))
        atomic_json(batch / 'review.json', dict(report, freeze_receipt=receipt))
        return report


def _compare(actual, expected):
    if isinstance(expected, dict):
        if actual.keys() != expected.keys():
            raise ValueError('replayed fields differ')
        for k in expected:
            _compare(actual[k], expected[k])
    elif isinstance(expected, list) or isinstance(expected, float):
        np.testing.assert_allclose(actual, expected, rtol=1e-11, atol=1e-14)
    elif actual != expected:
        raise ValueError('replayed identity or selection differs')


def replay(data, batch):
    """Rebuild market, both teams' histories and each forecast from original asset bytes."""
    plan, fixtures, captures = records(batch)
    body, cutoff = plan['payload'], plan['fixture']['cutoff_at']
    binding = body['binding']
    root = data.resolve()
    channel = (root / binding['market_archive']).resolve()
    if not channel.is_relative_to(root / 'market/runs'):
        raise ValueError('unexpected market archive')
    registry = data / 'future/bundle-v2/objects' / binding['registry']
    identities, a = materials(data, batch, channel, registry)
    if identities != binding:
        raise ValueError('model, code, registration or channel hash mismatch')
    dataset = inputs(data, channel, a, cutoff)
    if any(body[k] != v for k, v in dataset.items()):
        raise ValueError('frozen plan differs from original market and fixture evidence')
    route = read(registry)['models']['bra.1']
    archive = data / 'workspace-updates/20261115-remaining-nations-brazil/remaining-evidence.tar.gz'
    with tarfile.open(archive) as tf:
        raw = tf.extractfile('WCPP/models/weighted-20261012/bra.1/artifact.json').read()
    if (hashlib.sha256(raw).hexdigest() != route['artifact_sha256']
            or route.get('current_engine', 'legacy_weighted_anchor') != 'legacy_weighted_anchor'):
        raise ValueError('archived Current differs from registered active model')
    anchor = json.loads(raw)['anchor']
    summary = read(batch / 'summary.json')
    published = {r['match_id']: r for r in summary['matches']}
    if (set(published) != set(fixtures) or len(published) != len(summary['matches'])
            or summary['population'] != len(fixtures) or summary['plan_sha256'] != plan['record_sha256']):
        raise ValueError('published population differs from first plan')
    comparisons = native_count = 0
    for mid, r in captures.items():
        body = r['payload']
        canonical = market.first_path(batch, mid, SCORES)
        if displayed(r) != published[mid] or read(canonical) != r:
            raise ValueError('published forecast differs from canonical first issue')
        if not body['arms']:
            continue
        f, quote = fixtures[mid], dataset['markets'][mid]
        issued = aware(r['recorded_at'])

        class IssueClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return issued.astimezone(tz)

        with patch.object(legacy, 'datetime', IssueClock):
            native = legacy.predict(anchor, dict(f, total_quote=market.total_quote(quote)))
        old = body['native_current']
        if (native['version'] != old['version'] or digest(old['matrix']) != old['matrix_sha256']
                or old['artifact_sha256'] != route['artifact_sha256'] or old['engine_sha256'] != file_sha256(RUNTIME / 'v5/weighted_model.py')):
            raise ValueError('stored native forecast identity differs from active engine')
        _compare(native['matrix'], old['matrix'])
        native_count += 1
        panels = [p['features'] for p in dataset['history'][mid]]
        baseline = score.apply(a['market'], native['matrix'], quote, cohort=f['cohort'], cutoff_at=cutoff)['market']
        alternatives = sequence.sequence.apply(a['sequence'], baseline, panels)
        alternatives['market_baseline'] = alternatives.pop('baseline')
        alternatives['transformer'] = transformer.apply(a['transformer'], baseline, panels)
        for arm in ARMS:
            frozen, matrix = body['arms'][arm], alternatives[arm]
            _compare(matrix, frozen['matrix'])
            _compare(score.score_picks(matrix, line=quote['total']['line']),
                     {k: v for k, v in frozen.items() if k not in ('matrix', 'matrix_sha256')})
            comparisons += 1
    return {'population': len(fixtures), 'first_issues': len(captures), 'restored_current': native_count,
            'market_pairs': sum(bool(q) for q in dataset['markets'].values()),
            'matrix_and_selection_replays': comparisons, 'plan_sha256': plan['record_sha256']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('capture', 'review', 'replay'))
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--batch', type=Path, required=True)
    parser.add_argument('--market-dir', type=Path)
    parser.add_argument('--registry', type=Path)
    args = parser.parse_args()
    if args.action == 'capture':
        if args.market_dir is None or args.registry is None:
            parser.error('capture requires --market-dir and --registry')
        result = capture(args.state_dir, args.batch, args.market_dir, args.registry)
    elif args.action == 'review':
        result = review(args.state_dir, args.batch)
    else:
        result = replay(args.state_dir, args.batch)
    print(json.dumps({k: v for k, v in result.items() if k not in ('matches', 'rows', 'arms', 'paired')}, sort_keys=True))


if __name__ == '__main__':
    main()
