"""Independent, paired accounting for 当前模型 and test模型."""
from datetime import datetime, timezone
from urllib.parse import urlparse
from weighted_model import legacy


def review(pairs, results):
    result_map = {}
    for r in results:
        if r['match_id'] in result_map:
            raise ValueError('duplicate result')
        if r.get('score_basis') != 'regulation_90' or urlparse(r.get('source_url', '')).scheme != 'https':
            raise ValueError('verified 90-minute result required')
        if any(type(r.get(k)) is not int or not 0 <= r[k] <= 30 for k in ('home_goals', 'away_goals')):
            raise ValueError('invalid result scores')
        result_map[r['match_id']] = r
    seen, rows, pending = set(), [], []
    for pair in pairs:
        mid = pair['match_id']
        if mid in seen: raise ValueError('duplicate model pair')
        seen.add(mid)
        r = result_map.get(mid)
        if r is None:
            pending.append(mid); continue
        forecasts = [pair['current_model'], pair['test_model']]
        if forecasts[0]['input'] != forecasts[1]['input']:
            raise ValueError('unpaired source/cutoff inputs')
        if any(f['input']['match_id'] != mid for f in forecasts):
            raise ValueError('forecast/result identity mismatch')
        kickoff = legacy.aware(forecasts[0]['input']['kickoff_at'])
        for f in forecasts:
            if not legacy.aware(f['input']['cutoff_at']) <= legacy.aware(f['generated_at']) < kickoff:
                raise ValueError('not a chronological research forecast')
            if legacy.digest(f['matrix']) != f['matrix_hash']:
                raise ValueError('tampered forecast probability matrix')
        observed = legacy.aware(r['observed_at'])
        from datetime import timedelta
        if not kickoff + timedelta(minutes=90) <= observed <= datetime.now(timezone.utc):
            raise ValueError('result not observed after match')
        h, a = r['home_goals'], r['away_goals']
        actual = '主胜' if h > a else '客胜' if h < a else '平局'
        entry = {'match_id': mid, 'result': f'{h}-{a}', 'models': {},
                 'formal_kpi_eligible': False, 'kickoff_conflict': pair.get('kickoff_conflict')}
        for name, f in zip(('当前模型', 'test模型'), forecasts):
            settlement = None
            if f['total']:
                import numpy as np
                point = np.zeros((51, 51)); point[h, a] = 1.
                probs = legacy.total_settlement(point, f['total']['quote']['line'], f['total']['reference_direction'])
                settlement = next(k for k, v in probs.items() if v == 1.)
            entry['models'][name] = {'version':f.get('version','UNSPECIFIED'),
                                     'primary_hit': f['primary'] == f'{h}-{a}',
                                     'wdl_hit': f['wdl_reference'] == actual,
                                     'total_settlement': settlement}
        rows.append(entry)
    summary = {}
    for name in ('当前模型', 'test模型'):
        values = [r['models'][name] for r in rows]
        from collections import Counter
        def counts(items):
            return {'settled':len(items),'primary_hits':sum(v['primary_hit'] for v in items),
                    'wdl_hits':sum(v['wdl_hit'] for v in items),
                    'total_settlements':dict(Counter(v['total_settlement'] for v in items if v['total_settlement']))}
        versions = {v['version'] for v in values}
        grouped = {ver:counts([v for v in values if v['version']==ver]) for ver in sorted(versions)}
        summary[name] = (dict(counts(values),version=next(iter(versions),'NONE'),by_version=grouped)
                         if len(versions)<=1 else {'settled':len(values),
                         'aggregation_status':'MIXED_VERSIONS_NO_POOLED_ACCURACY','by_version':grouped})
    return {'models': summary, 'paired_rows': rows, 'pending': pending,
            'selection_policy': 'NO_BEST_OF_TWO_NO_RETROSPECTIVE_SWITCH',
            'formal_kpi_added': 0, 'target_attainment_proven': False}
