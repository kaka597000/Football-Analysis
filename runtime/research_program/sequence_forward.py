"""Freeze and score paired sequence residual models at one real prematch cutoff."""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import numpy as np

from . import sequence_models as sequence
from . import three_arm_forward as market_forward
from . import three_arm_model as market_model
from . import three_arm_review as scored
from prediction_engine import file_sha256, predict_models
from publication import atomic_json
from research.analysis_freeze import aware, digest, save, verify
from storage_lock import exclusive_lock

ARMS = ('market_baseline', 'lightgbm', 'lstm')
PLAN_TRACK = 'sequence-research-plan'
SCORE_TRACK = 'sequence-research-score'
read = market_forward.read


def team_schedule(history, team, *, fixture, seen):
    """Prior 90-minute league finals, chronologically ordered and traceable by ESPN ID."""
    numeric = team.removeprefix('espn:')
    if not numeric.isdigit() or team != 'espn:' + numeric:
        raise ValueError('ESPN team identity required')
    kickoff = aware(fixture['kickoff_at']).astimezone(timezone.utc)
    cutoff = aware(fixture['cutoff_at'])
    ref = read(history / (numeric + '.meta.json'))
    season = kickoff.year
    endpoint = f'https://site.api.espn.com/apis/site/v2/sports/soccer/bra.1/teams/{numeric}/schedule?season={season}'
    stamp = aware(ref['captured_at'])
    if ref['url'] != endpoint or stamp > cutoff or aware(ref['requested_at']) > stamp:
        raise ValueError('team history evidence time or endpoint mismatch')
    doc = market_forward.feed.check_evidence(history, ref)
    if (doc['status'] != 'success' or doc['team']['id'] != numeric or doc['season']['year'] != season
            or doc['requestedSeason']['year'] != season):
        raise ValueError('team schedule identity mismatch')
    matches = []
    encountered = set()
    for e in doc['events']:
        if e['league']['slug'] != 'bra.1' or e['season']['year'] != season:
            continue
        c, = e['competitions']
        start = aware(e['date']).astimezone(timezone.utc)
        if start.date() >= kickoff.date() or start + timedelta(minutes=90) > stamp:
            continue
        if (c['status']['type']['name'] != 'STATUS_FULL_TIME' or c['status']['type']['completed'] is not True
                or c['status']['type']['state'] != 'post' or c['status']['period'] != 2):
            continue
        if c['id'] != e['id'] or aware(c['date']) != start or e['id'] in encountered:
            raise ValueError('ambiguous historical fixture')
        encountered.add(e['id'])
        sides = {v['homeAway']: v for v in c['competitors']}
        if set(sides) != {'home', 'away'} or len(c['competitors']) != 2:
            raise ValueError('malformed historical sides')
        row = {'match_id': 'espn:' + e['id'], 'kickoff_at': start.isoformat()}
        for side in ('home', 'away'):
            value = sides[side]['score']['value']
            if type(value) not in (int, float) or int(value) != value or value < 0:
                raise ValueError('invalid historical goals')
            row[side] = 'espn:' + sides[side]['team']['id']
            row[side + '_goals'] = int(value)
        if (team not in (row['home'], row['away']) or row['home'] == row['away']
                or seen.setdefault(row['match_id'], row) != row):
            raise ValueError('conflicting historical teams or score')
        matches.append(row)
    matches.sort(key=lambda row: (row['kickoff_at'], row['match_id']))
    recent = matches[-sequence.WINDOW:]
    panel = [[0, 0, 0, 0] for _ in range(sequence.WINDOW - len(recent))]
    for row in recent:
        home = row['home'] == team
        panel.append([row['home_goals'] if home else row['away_goals'],
                      row['away_goals'] if home else row['home_goals'], int(home), 1])
    return {'team': team, 'source': ref, 'eligible_history': len(matches), 'matches': recent, 'features': panel}


def check_enrollment(protocol, fixtures, cutoff):
    expected = {f['match_id']: f for f in protocol['fixtures']}
    if (protocol.get('schema') != 'WCPP-SEQUENCE-FORWARD-REGISTRATION-1' or protocol['arms'] != list(ARMS)
            or protocol.get('automatic_promotion') is not False or aware(protocol['recorded_at']) > aware(cutoff)
            or len(expected) != len(protocol['fixtures']) or set(expected) != {f['match_id'] for f in fixtures}
            or not expected):
        raise ValueError('invalid registration or changed enrollment')
    for f in fixtures:
        row = expected[f['match_id']]
        if (any(row[k] != f[k] for k in ('home', 'away', 'cohort', 'stage', 'neutral'))
                or aware(row['kickoff_at']) != aware(f['kickoff_at'])
                or row['identity_sha256'] not in {ref['response_sha256'] for ref in f['sources']}):
            raise ValueError('fixture differs from enrollment')


def predict(fixture, quote, panels, candidate, calibration, registry):
    inputs = dict(fixture)
    if quote:
        inputs['total_quote'] = market_forward.total_quote(quote)
    try:
        raw = predict_models(inputs, registry, retain_current_on_test_failure=True)
    except (OSError, ValueError) as exc:
        return {'status': 'ABSTAIN_CURRENT_UNAVAILABLE', 'detail': str(exc), 'arms': {}}
    current = raw['current']
    current_matrix = current['matrix']
    if current['matrix_hash'] != digest(current_matrix):
        raise ValueError('invalid active Current matrix')
    common = market_model.apply(calibration, current_matrix, quote,
                                cohort=fixture['cohort'], cutoff_at=fixture['cutoff_at'])['market']
    features = [p['features'] for p in panels]
    matrices = sequence.apply(candidate, common, features)
    if set(matrices) != {'baseline', 'lightgbm', 'lstm'} or not np.array_equal(common, matrices['baseline']):
        raise ValueError('sequence baseline differs from same-cutoff market-adjusted Current')
    arms = {}
    for name in ARMS:
        m = matrices['baseline' if name == 'market_baseline' else name]
        values = m.tolist()
        picks = market_model.score_picks(m, line=quote['total']['line'] if quote else None)
        arms[name] = dict(picks, matrix=values, matrix_sha256=digest(values))
    return {'status': 'ISSUED_REAL_MARKET' if quote else 'ISSUED_NO_MARKET',
            'native_current': {'version': current['version'], 'artifact_sha256': raw['route']['artifact_sha256'],
                               'engine_sha256': file_sha256(raw['current_engine_path']),
                               'matrix': current_matrix, 'matrix_sha256': current['matrix_hash']},
            'arms': arms}


def capture(*, market_dir, identity_dir, history, registration, artifact, market_artifact, registry, out):
    out.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(out / '.sequence-capture.lock'):
        protocol = read(registration)
        candidate = read(artifact)
        calibration = read(market_artifact)
        binding = {'registration_sha256': file_sha256(registration), 'artifact_sha256': file_sha256(artifact),
                   'market_artifact_sha256': file_sha256(market_artifact), 'registry_sha256': file_sha256(registry),
                   'channel_sha256': file_sha256(market_dir / 'market-channel.json'),
                   'sequence_model_source_sha256': file_sha256(sequence.__file__),
                   'market_model_source_sha256': file_sha256(market_model.__file__),
                   'market_capture_source_sha256': file_sha256(market_forward.__file__),
                   'capture_source_sha256': file_sha256(__file__)}
        mid = 'sequence-plan:' + binding['registration_sha256']
        canonical = market_forward.first_path(out, mid, PLAN_TRACK)
        alias = out / 'plan.json'
        if canonical.exists() or alias.exists():
            old = verify(read(alias if alias.exists() else canonical))
            if binding != old['payload']['binding']:
                raise ValueError('cannot change a frozen batch')
            plan = market_forward.copy_first(out, alias, old['fixture'], PLAN_TRACK)
        else:
            cutoff = datetime.now(timezone.utc).isoformat()
            if calibration['market_weight'] != 1:
                raise ValueError('registered market baseline requires unit mixing weight')
            if max(aware(a['cutoff_at']) for a in (candidate, calibration)) > aware(cutoff):
                raise ValueError('training after cutoff')
            fixtures, markets, sources = market_forward.batch_inputs(market_dir, identity_dir, 'bra.1', cutoff)
            check_enrollment(protocol, fixtures, cutoff)
            timelines = {}
            seen = {}
            for f in fixtures:
                timelines[f['match_id']] = [team_schedule(history, f[side], fixture=f, seen=seen) for side in ('home', 'away')]
            group = {'match_id': mid, 'cutoff_at': cutoff, 'kickoff_at': fixtures[0]['kickoff_at']}
            payload = {'schema': 'WCPP-SEQUENCE-PLAN-1', 'binding': binding, 'fixtures': fixtures, 'markets': markets,
                       'history': timelines, 'sources': sources, 'arms': list(ARMS), 'automatic_promotion': False}
            plan = market_forward.copy_first(out, alias, group, PLAN_TRACK, payload)
        rows = []
        for f in plan['payload']['fixtures']:
            mid = f['match_id']
            quote, history_rows = (plan['payload'][k][mid] for k in ('markets', 'history'))
            dest = out / 'freezes' / (mid.replace(':', '-') + '.json')
            canonical = market_forward.first_path(out, mid, SCORE_TRACK)
            payload = None
            if not canonical.is_file():
                if aware(f['kickoff_at']) <= datetime.now(timezone.utc):
                    rows.append({'match_id': mid, 'status': 'MISSED_PREMATCH'})
                    continue
                payload = predict(f, quote, history_rows, candidate, calibration, registry)
                payload.update(plan_sha256=plan['record_sha256'], market=quote, history_sha256=digest(history_rows),
                               artifact_sha256=binding['artifact_sha256'])
            record = market_forward.copy_first(out, dest, f, SCORE_TRACK, payload)
            payload = record['payload']
            if payload['plan_sha256'] != plan['record_sha256']:
                raise ValueError('different first issue')
            rows.append({'match_id': mid, 'status': payload['status'], 'recorded_at': record['recorded_at'],
                         'record_sha256': record['record_sha256'], 'file': str(dest.relative_to(out)),
                         'arms': {k: {key: v[key] for key in ('primary', 'wdl', 'total')} for k, v in payload['arms'].items()}})
        result = {'schema': 'WCPP-SEQUENCE-BATCH-1', 'population': len(rows), 'cutoff_at': plan['fixture']['cutoff_at'],
                  'plan_sha256': plan['record_sha256'], 'matches': rows, 'automatic_promotion': False}
        atomic_json(out / 'summary.json', result)
        return result


def inspect_batch(batch):
    plan = scored._first_freeze(batch / 'plan.json', PLAN_TRACK)
    definition = plan['payload']
    planned = {f['match_id']: f for f in definition['fixtures']}
    if (set(planned) != set(definition['markets']) or set(planned) != set(definition['history'])
            or len(planned) != len(definition['fixtures']) or not planned):
        raise ValueError('registered population and input sets differ')
    captures = {}
    for path in sorted((batch / 'freezes').glob('*.json')):
        r = scored._first_freeze(path, SCORE_TRACK)
        mid, f, payload = r['match_id'], r['fixture'], r['payload']
        if (mid in captures or planned.get(mid) != f or payload['plan_sha256'] != plan['record_sha256']
                or payload['market'] != definition['markets'][mid] or payload['history_sha256'] != digest(definition['history'][mid])
                or payload['artifact_sha256'] != definition['binding']['artifact_sha256']
                or not aware(f['cutoff_at']) <= aware(plan['recorded_at']) <= aware(r['recorded_at']) < aware(f['kickoff_at'])):
            raise ValueError('first freeze or input identity conflict')
        arms, quote = payload['arms'], payload['market']
        if (bool(arms) != (payload['status'] in ('ISSUED_REAL_MARKET', 'ISSUED_NO_MARKET'))
                or (arms and set(arms) != set(ARMS)) or (arms and bool(quote) != (payload['status'] == 'ISSUED_REAL_MARKET'))):
            raise ValueError('partial or unidentified sequence forecast')
        if arms and digest(payload['native_current']['matrix']) != payload['native_current']['matrix_sha256']:
            raise ValueError('Current MATRIX checksum mismatch')
        for a in arms.values():
            m = np.array(a['matrix'])
            if digest(a['matrix']) != a['matrix_sha256']:
                raise ValueError('arm matrix checksum mismatch')
            picks = market_model.score_picks(m, line=quote['total']['line'] if quote else None)
            if (a['primary'] != picks['primary'] or a['wdl'] != picks['wdl']
                    or (a['total'] is None) != (picks['total'] is None)
                    or (quote and (a['total']['direction'], a['total']['line'])
                        != (picks['total']['direction'], picks['total']['line']))):
                raise ValueError('selection differs from its frozen score distribution')
        captures[mid] = r
    return plan, planned, captures


def review(batch, state, now=None):
    now = now or datetime.now(timezone.utc)
    plan, population, captures = inspect_batch(batch)
    if any(aware(r['recorded_at']) > now for r in (plan, *captures.values())):
        raise ValueError('review predates its frozen input')
    results, issues = scored._results(state.resolve(), population, now)
    rows = []
    for mid, fixture in population.items():
        r, result = captures.get(mid), results.get(mid)
        arms = r['payload']['arms'] if r else {}
        rows.append({'match_id': mid, **scored._matchday(fixture),
                     'freeze_sha256': r['record_sha256'] if r else None, 'result': result,
                     'status': 'SETTLED' if result else 'PENDING', 'source_issues': issues.get(mid, []),
                     'outcomes': {k: scored._outcome(a, result) for k, a in arms.items()} if result else {}})
    summary = {}
    for arm in ARMS:
        predictions = [r['payload']['arms'][arm] for r in captures.values() if arm in r['payload']['arms']]
        observed = [r['outcomes'][arm] for r in rows if arm in r['outcomes']]
        metrics = {}
        for name in scored.METRICS:
            values = [r[name] for r in observed if r[name] is not None]
            metrics[name] = {'n': len(values), 'hits': sum(values), 'accuracy': sum(values) / len(values) if values else None}
        categories = Counter(r['total_settlement'] for r in observed)
        summary[arm] = {'forecasts': len(predictions), 'coverage': len(predictions) / len(population),
                        'real_line_selections': sum(r['total'] is not None for r in predictions),
                        'metrics': metrics, 'total_settlements': {k: categories[k] for k in scored.SETTLEMENTS},
                        'mean_score_log_loss': float(np.mean([r['score_log_loss'] for r in observed])) if observed else None,
                        'mean_wdl_brier': float(np.mean([r['wdl_brier'] for r in observed])) if observed else None}
    comparisons = {(b + '_minus_' + a): {k: scored._paired(rows, a, b, k) for k in scored.METRICS}
                   for a, b in (('market_baseline', 'lightgbm'), ('market_baseline', 'lstm'), ('lightgbm', 'lstm'))}
    return {'schema': 'WCPP-SEQUENCE-REVIEW-1', 'plan_sha256': plan['record_sha256'], 'population': len(population),
            'issued': len(captures), 'settled': len(results), 'pending': len(population) - len(results),
            'automatic_promotion': False, 'arms': summary, 'paired': comparisons, 'rows': rows}


def publish_review(batch, state):
    with exclusive_lock(batch / '.sequence-review.lock'):
        report = review(batch, state)
        output = batch / 'review.json'
        if output.exists() and read(output).get('plan_sha256') != report['plan_sha256']:
            raise ValueError('existing review belongs to a different plan')
        plan = verify(read(batch / 'plan.json'))
        stamps = [plan['recorded_at']]
        stamps.extend(r['result']['source']['captured_at'] for r in report['rows'] if r['result'])
        stamps.extend(verify(read(p))['recorded_at'] for p in (batch / 'freezes').glob('*.json'))
        fixture = {'match_id': 'sequence-review:' + report['plan_sha256'], 'kickoff_at': plan['fixture']['kickoff_at'],
                   'cutoff_at': max(stamps, key=aware)}
        receipt = save(report, fixture, stage='review', track='sequence-research-review', root=batch / 'reviews',
                       idempotency_key=digest(report))
        receipt['path'] = str(Path(receipt['path']).resolve().relative_to(batch.resolve()))
        atomic_json(output, dict(report, freeze_receipt=receipt))
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    cap = sub.add_parser('capture')
    for arg in ('market-dir', 'identity-dir', 'history', 'registration', 'artifact', 'market-artifact', 'registry', 'out'):
        cap.add_argument('--' + arg, required=True, type=Path)
    replay = sub.add_parser('review')
    replay.add_argument('--batch', type=Path, required=True)
    replay.add_argument('--state-dir', type=Path, required=True)
    args = vars(parser.parse_args())
    mode = args.pop('mode')
    result = capture(**args) if mode == 'capture' else publish_review(args['batch'], args['state_dir'])
    print(json.dumps({k: result[k] for k in ('population', 'settled', 'pending', 'plan_sha256') if k in result}))


if __name__ == '__main__':
    main()
