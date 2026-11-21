"""Prospective fixed-population shadow evaluation of first issued forecasts.

Hash consistency is not external trusted timestamping or proof of exhaustive
source collection. No fitting, best-revision selection or production promotion.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import numpy as np

try:
    from .analysis_freeze import aware, digest, save, verify
    from .score_concentration import diagnose, parse_score, probability_view, selected_primary
except ImportError:
    from analysis_freeze import aware, digest, save, verify
    from score_concentration import diagnose, parse_score, probability_view, selected_primary

SCHEMA = 'WCPP-FROZEN-SCORE-EVALUATION-1'
PROTOCOL = {
    'schema': SCHEMA, 'primary': 'UNIQUE_PRIMARY_TOP1',
    'population': 'ALL_PREREGISTERED_MATCHES_INCLUDING_FAILURES_AND_ABSTENTIONS',
    'version': 'FIRST_PREMATCH_ONLY_NO_FALLBACK',
    'secondary': ['WDL', 'ACTUAL_LINE_FIVE_WAY_TOTALS', 'PROBABILITY_CALIBRATION'],
    'comparison': 'PAIRED_SAME_MATCH_NO_BEST_OF_MODELS',
    'uncertainty': 'PAIRED_KICKOFF_DATE_CLUSTER_BOOTSTRAP_95',
    'automatic_promotion': False,
}


def now():
    return datetime.now(ZoneInfo('Asia/Shanghai'))


def _sha(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _url(value):
    u = urlparse(value)
    return u.scheme == 'https' and bool(u.hostname) and not u.username and not u.password


def implementation_sha256():
    root = Path(__file__).resolve().parent
    names = ('frozen_score_evaluation.py', 'score_concentration.py', 'analysis_freeze.py',
             'team_goal_analysis.py', 'score_distribution_audit.py')
    return digest({name: hashlib.sha256((root/name).read_bytes()).hexdigest() for name in names})


def validate_spec(spec):
    if not isinstance(spec.get('experiment_id'), str) or not spec['experiment_id'].strip():
        raise ValueError('experiment ID required')
    if spec.get('implementation_sha256') != implementation_sha256():
        raise ValueError('evaluation implementation digest mismatch')
    if spec.get('protocol_sha256') != digest(PROTOCOL):
        raise ValueError('protocol digest mismatch')
    matches, tracks = spec['matches'], spec['tracks']
    if not matches or not tracks or len({m['match_id'] for m in matches}) != len(matches):
        raise ValueError('nonempty unique match population and tracks required')
    if {m['match_id'] for m in matches}.intersection(spec.get('development_match_ids', [])):
        raise ValueError('development examples cannot enter the holdout')
    end = aware(spec['evaluation_not_before'])
    for m in matches:
        if not isinstance(m['match_id'], str) or not m['match_id'] or not m.get('cohort'):
            raise ValueError('explicit match and cohort identities required')
        if end < aware(m['kickoff_at']) + timedelta(hours=3):
            raise ValueError('evaluation time must follow all planned matches')
    if len({t['name'] for t in tracks}) != len(tracks):
        raise ValueError('unique track names required')
    for t in tracks:
        if not t.get('name') or not t.get('freeze_track') or t.get('payload_key') not in (None, 'current_model', 'test_model'):
            raise ValueError('explicit forecast selector required')
        for cohort in {m['cohort'] for m in matches}:
            identity = t['identity_by_cohort'][cohort]
            if not identity.get('version') or not _sha(identity.get('artifact_sha256')):
                raise ValueError('lock version and model/selector artifact SHA256 per cohort')
            if not _sha(identity.get('training_snapshot_sha256')) or not identity.get('training_cutoff'):
                raise ValueError('lock training snapshot and cutoff per cohort')
            for m in matches:
                if m['cohort'] == cohort and aware(identity['training_cutoff']) >= aware(m['kickoff_at']):
                    raise ValueError('training cutoff must precede held-out kickoff')
    if spec['baseline_track'] not in {t['name'] for t in tracks}:
        raise ValueError('baseline must be a preregistered track')
    return spec


def register(spec, root):
    """Real clock and atomic append-only writer. Changed plans need a new ID."""
    validate_spec(spec)
    current = now()
    first = min(aware(m['kickoff_at']) for m in spec['matches'])
    if current >= first:
        raise ValueError('prospective population must be registered before first kickoff')
    for track in spec['tracks']:
        if any(aware(i['training_cutoff']) > current for i in track['identity_by_cohort'].values()):
            raise ValueError('training cutoff cannot be in the future at registration')
    fixture = {'match_id': 'score-evaluation:' + spec['experiment_id'],
               'kickoff_at': first.isoformat(), 'cutoff_at': current.isoformat()}
    return save(spec, fixture, stage='prematch', track='shadow-score-evaluation-plan', root=root,
                idempotency_key='plan:' + spec['experiment_id'])


def validate_plan(plan):
    verify(plan)
    if (plan.get('schema') != 'WCPP-ISSUED-FREEZE-1' or plan.get('stage') != 'prematch'
            or plan.get('track') != 'shadow-score-evaluation-plan' or plan.get('revision') != 1
            or plan.get('previous_sha256') is not None or plan.get('evaluation_slot') != 'FIRST_PREMATCH'):
        raise ValueError('first immutable prospective plan required')
    spec = validate_spec(plan['payload'])
    first = min(aware(m['kickoff_at']) for m in spec['matches'])
    if (not aware(plan['fixture']['cutoff_at']) <= aware(plan['recorded_at']) < first
            or aware(plan['recorded_at']) > now()):
        raise ValueError('late prospective plan')
    for track in spec['tracks']:
        if any(aware(i['training_cutoff']) > aware(plan['recorded_at']) for i in track['identity_by_cohort'].values()):
            raise ValueError('training cutoff after registration')
    return spec


def _outcomes(results, matches):
    actual = {}
    for r in results:
        mid = r['match_id']
        if mid in actual:
            raise ValueError('duplicate result')
        if mid not in matches:
            raise ValueError('result outside registered population')
        if r.get('status') not in ('FT', 'completed') or r.get('score_basis') != 'regulation_90':
            raise ValueError('explicit finished regulation_90 result required')
        if any(type(r.get(k)) is not int or not 0 <= r[k] <= 40 for k in ('home_goals', 'away_goals')):
            raise ValueError('valid integer result goals required')
        if not _url(r.get('source_url', '')):
            raise ValueError('verified HTTPS result source required')
        if not aware(matches[mid]['kickoff_at']) + timedelta(minutes=90) <= aware(r['observed_at']) <= now():
            raise ValueError('result observation time invalid')
        actual[mid] = r
    return actual


def _first_records(records, matches):
    first, seen, revisions, outside = {}, {}, [], []
    for record in records:
        verify(record)
        if record.get('schema') != 'WCPP-ISSUED-FREEZE-1':
            raise ValueError('issued-freeze schema required')
        key = (record['match_id'], record['track'], record['stage'])
        identity = (*key, record['revision'])
        if identity in seen:
            if seen[identity] != record['record_sha256']:
                raise ValueError('conflicting records at same revision')
            continue
        seen[identity] = record['record_sha256']
        if record['match_id'] not in matches:
            outside.append(record['freeze_id'])
            continue
        fixture, match = record['fixture'], matches[record['match_id']]
        if (fixture['match_id'] != record['match_id'] or aware(fixture['kickoff_at']) != aware(match['kickoff_at'])
                or fixture.get('cohort') != match['cohort']):
            raise ValueError('frozen fixture identity differs from population')
        if record['stage'] != 'prematch':
            revisions.append(record['freeze_id'])
            continue
        if (not aware(fixture['cutoff_at']) <= aware(record['recorded_at']) <= now()
                or aware(record['recorded_at']) >= aware(fixture['kickoff_at'])):
            raise ValueError('invalid prematch freeze chronology')
        if record['revision'] != 1:
            revisions.append(record['freeze_id'])
            continue
        if record.get('previous_sha256') is not None or record.get('evaluation_slot') != 'FIRST_PREMATCH':
            raise ValueError('first revision contract mismatch')
        first[key] = record
    for r in records:
        identity = (r['match_id'], r['track'], r['stage'], r['revision']-1)
        if identity in seen and r['previous_sha256'] != seen[identity]:
            raise ValueError('revision chain mismatch')
    return first, revisions, outside


def _prediction(record, track, match, plan_time):
    if record is None:
        return {'status': 'MISSING_FIRST_FREEZE', 'primary': None}
    if aware(record['recorded_at']) < plan_time:
        return {'status': 'FORECAST_PRECEDES_PLAN', 'primary': None}
    pred = record['payload'].get(track['payload_key']) if track.get('payload_key') else record['payload']
    if not isinstance(pred, dict):
        return {'status': 'MISSING_TRACK_PAYLOAD', 'primary': None}
    identity = track['identity_by_cohort'][match['cohort']]
    if any(pred.get(k) != identity[k] for k in ('version', 'artifact_sha256', 'training_snapshot_sha256', 'training_cutoff')):
        return {'status': 'MODEL_IDENTITY_MISMATCH', 'primary': None}
    if not _sha(pred.get('input_snapshot_sha256')) or not pred.get('generated_at') or not pred.get('data_cutoff_at'):
        return {'status': 'MISSING_ASOF_PROVENANCE', 'primary': None}
    if pred.get('input') is not None and pred['input'] != record['fixture']:
        raise ValueError('prediction input differs from frozen fixture')
    if not (aware(identity['training_cutoff']) <= aware(pred['data_cutoff_at']) <= aware(pred['generated_at'])
            and plan_time <= aware(pred['generated_at']) <= aware(record['recorded_at'])
            and aware(pred['data_cutoff_at']) <= aware(record['fixture']['cutoff_at'])):
        raise ValueError('prediction as-of chronology invalid')
    if pred.get('primary') is not None:
        parse_score(pred['primary'])
    if pred.get('matrix') is not None:
        probability_view(pred)
    return dict(pred, primary=selected_primary(pred))


def total_settlement(total_goals, line, direction):
    if type(line) not in (float, int) or not math.isfinite(line) or line < 0 or abs(line*4-round(line*4)) > 1e-9:
        raise ValueError('actual quarter-goal line required')
    if direction not in ('大', '小'):
        raise ValueError('explicit actual-line direction required')
    lines = [line] if int(round(line*4)) % 2 == 0 else [line-.25, line+.25]
    value = sum((1 if total_goals > x else -1 if total_goals < x else 0)
                * (1 if direction == '大' else -1) for x in lines)/len(lines)
    return {-1: '全输', -.5: '半输', 0: '走盘', .5: '半赢', 1: '全赢'}[value]


def _directions(pred, result, record):
    if not result:
        return {'wdl_hit': None, 'total_settlement': None}
    h, a = result['home_goals'], result['away_goals']
    direction = pred.get('wdl_reference', pred.get('wdl'))
    actual = '主胜' if h > a else '客胜' if h < a else '平局'
    out = {'wdl_hit': direction == actual if direction in ('主胜', '平局', '客胜') else None,
           'total_settlement': None}
    total = pred.get('total')
    if total:
        quote = total.get('quote') or {}
        captured = quote.get('captured_at', quote.get('query_time'))
        quoted = quote.get('quoted_at')
        if (not quote.get('bookmaker') or not _url(quote.get('source_url', '')) or not captured or not quoted
                or quote.get('verified') is not True or not _sha(quote.get('source_file_sha256'))
                or quote.get('market') not in ('standard_total', 'asian_total')
                or quote.get('score_basis') != 'regulation_90'
                or quote.get('match_id') != record['match_id']
                or not aware(quoted) <= aware(captured) <= aware(pred['data_cutoff_at']) <= aware(pred['generated_at'])
                or aware(captured) > aware(record['fixture']['cutoff_at'])):
            out['total_unavailable_reason'] = 'MISSING_OR_LATE_ACTUAL_LINE_PROVENANCE'
        else:
            out['total_settlement'] = total_settlement(h+a, quote['line'], total['reference_direction'])
    return out


def paired_uncertainty(pairs):
    """Date-block resampling is descriptive, not sequential significance."""
    if not pairs:
        return {'n': 0, 'date_clusters': 0, 'net_hits': 0, 'delta_rate': None, 'bootstrap95': None}
    groups = defaultdict(list)
    for row in pairs:
        date = aware(row['kickoff_at']).astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
        groups[date].append(row['delta'])
    values, n = list(groups.values()), len(pairs)
    interval = None
    if len(values) >= 2:
        rng = np.random.default_rng(20261110)
        samples = []
        for _ in range(5000):
            draws = [values[i] for i in rng.integers(0, len(values), len(values))]
            samples.append(sum(map(sum, draws))/sum(map(len, draws)))
        interval = np.quantile(samples, [.025, .975]).tolist()
    return {'n': n, 'date_clusters': len(values), 'net_hits': sum(r['delta'] for r in pairs),
            'delta_rate': sum(r['delta'] for r in pairs)/n, 'bootstrap95': interval,
            'uncertainty_status': 'FEW_DATE_CLUSTERS_CAUTION' if len(values) < 20 else 'DESCRIPTIVE_FIXED_HORIZON',
            'changed_right': sum(r['delta'] == 1 for r in pairs),
            'changed_wrong': sum(r['delta'] == -1 for r in pairs)}


def evaluate(plan, records, results):
    spec = validate_plan(plan)
    matches = {m['match_id']: m for m in spec['matches']}
    actual = _outcomes(results, matches)
    first, revisions, outside = _first_records(records, matches)
    rows, directions, lookup = [], [], {}
    for track in spec['tracks']:
        for mid, m in matches.items():
            rec = first.get((mid, track['freeze_track'], 'prematch'))
            pred = _prediction(rec, track, m, aware(plan['recorded_at']))
            identity = track['identity_by_cohort'][m['cohort']]
            normalized = dict(pred, version=identity['version'])
            row = {'match_id': mid, 'track': track['name'], 'cohort': m['cohort'],
                   'prediction': normalized, 'result': actual.get(mid),
                   'source': {'freeze_sha256': rec['record_sha256'] if rec else None,
                              'validation_status': pred.get('status', 'AVAILABLE')}}
            rows.append(row)
            lookup[(mid, track['name'])] = row
            directions.append({'match_id': mid, 'track': track['name'], 'cohort': m['cohort'],
                               **_directions(pred, actual.get(mid), rec)})
    comparisons = []
    for track in spec['tracks']:
        if track['name'] == spec['baseline_track']:
            continue
        for cohort in sorted({m['cohort'] for m in matches.values()}):
            full, complete = [], []
            for mid, m in matches.items():
                if mid not in actual or m['cohort'] != cohort:
                    continue
                a = lookup[(mid, spec['baseline_track'])]['prediction'].get('primary')
                b = lookup[(mid, track['name'])]['prediction'].get('primary')
                score = (actual[mid]['home_goals'], actual[mid]['away_goals'])
                delta = int(b is not None and parse_score(b) == score)-int(a is not None and parse_score(a) == score)
                item = {'delta': delta, 'kickoff_at': m['kickoff_at']}
                full.append(item)
                if a is not None and b is not None:
                    complete.append(item)
            comparisons.append({'baseline': spec['baseline_track'], 'candidate': track['name'], 'cohort': cohort,
                                'full_settled_population': paired_uncertainty(full),
                                'both_primary_available': paired_uncertainty(complete)})
    secondary = []
    for track in spec['tracks']:
        for cohort in sorted({m['cohort'] for m in matches.values()}):
            subset = [d for d in directions if d['track'] == track['name'] and d['cohort'] == cohort]
            secondary.append({'track': track['name'], 'cohort': cohort, 'population': len(subset),
                'wdl_n': sum(d['wdl_hit'] is not None for d in subset),
                'wdl_hits': sum(d['wdl_hit'] is True for d in subset),
                'total_n': sum(d['total_settlement'] is not None for d in subset),
                'total_settlements': dict(Counter(d['total_settlement'] for d in subset if d['total_settlement'] is not None))})
    return {'schema': SCHEMA, 'evaluated_at': now().isoformat(), 'plan_sha256': plan['record_sha256'],
        'protocol_sha256': digest(PROTOCOL), 'implementation_sha256': implementation_sha256(),
        'population': len(matches), 'settled': len(actual), 'pending_match_ids': sorted(set(matches)-set(actual)),
        'horizon_closed': now() >= aware(spec['evaluation_not_before']), 'population_complete': len(actual) == len(matches),
        'automatic_promotion': False, 'production_modified': False, 'formal_kpi_added': 0,
        'excluded_revisions_or_stages': revisions, 'outside_population_records': outside,
        'diagnosis': diagnose(rows), 'paired_primary_comparisons': comparisons,
        'independent_direction_denominators': secondary, 'direction_rows': directions,
        'limitations': ['Hash checks do not establish external trusted timestamps or prove all sources were captured.',
            'Asset and input hashes must be retained with actual bytes and first-known availability evidence.',
            'Training and prediction metadata are checked, not independently rederived from raw data.',
            'Interim inspection is not a fixed-horizon promotion test; tuned variants need a new untouched holdout.']}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('register'); r.add_argument('spec'); r.add_argument('--root', required=True)
    e = sub.add_parser('evaluate'); e.add_argument('plan'); e.add_argument('records'); e.add_argument('results'); e.add_argument('--out', required=True)
    sub.add_parser('protocol')
    args = p.parse_args()
    if args.cmd == 'protocol':
        print(json.dumps({'protocol': PROTOCOL, 'protocol_sha256': digest(PROTOCOL),
                          'implementation_sha256': implementation_sha256()}, ensure_ascii=False, indent=2))
        return
    if args.cmd == 'register':
        print(json.dumps(register(json.loads(Path(args.spec).read_text()), args.root), ensure_ascii=False))
        return
    report = evaluate(*[json.loads(Path(getattr(args, k)).read_text()) for k in ('plan', 'records', 'results')])
    with Path(args.out).open('x', encoding='utf-8') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)


if __name__ == '__main__':
    main()
