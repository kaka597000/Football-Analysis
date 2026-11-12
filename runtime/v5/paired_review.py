"""Independent, paired accounting for 当前模型 and test模型."""
from datetime import datetime, timezone
from urllib.parse import urlparse
from weighted_model import legacy


def _total_audit(forecast):
    from research_program.future_validation import quote_audit
    fixture=forecast.get('input') or {}
    audit=quote_audit(fixture);reasons=list(audit['reasons'])
    total=forecast.get('total') or {}
    if total.get('reference_direction') not in ('over','under'):
        reasons.append('NO_FROZEN_TOTAL_DIRECTION')
    quote=total.get('quote') or {};source=fixture.get('total_quote') or {}
    if any(quote.get(k)!=source.get(k) for k in ('match_id','bookmaker','market','line','quoted_at','query_time','over_odds','under_odds','source_url')):
        reasons.append('FROZEN_TOTAL_QUOTE_MISMATCH')
    return {'eligible':not reasons,'reasons':reasons}


def _unavailable(forecast):
    if forecast.get('matrix') is not None:return False
    status=forecast.get('status','')
    if (not isinstance(status,str) or not (status=='ABSTAIN' or status.startswith(('ABSTAIN_','UNAVAILABLE')))
            or any(forecast.get(k) is not None for k in ('primary','wdl','wdl_reference','total'))):
        raise ValueError('missing matrix requires explicit unavailable track without recommendations')
    return True


def review(pairs, results):
    result_map = {}; seen_results = set()
    for r in results:
        if r['match_id'] in seen_results:
            raise ValueError('duplicate result')
        seen_results.add(r['match_id'])
        statuses=[r[k] for k in ('status','event_status') if r.get(k) is not None]
        if any(s in ('completed','FT') for s in statuses) and any(s not in ('completed','FT') for s in statuses):
            raise ValueError('conflicting result status')
        if r.get('status') not in ('completed', 'FT') and r.get('event_status') != 'FT':
            if r.get('status') is None and r.get('event_status') is None:
                raise ValueError('explicit result status required')
            continue
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
        unavailable = [_unavailable(f) for f in forecasts]
        inputs = [f['input'] for f in forecasts if f.get('input') is not None]
        if pair.get('fixture') is not None:inputs.append(pair['fixture'])
        if not inputs:raise ValueError('frozen fixture context required for unavailable pair')
        if any(fixture != inputs[0] for fixture in inputs):
            raise ValueError('unpaired source/cutoff inputs')
        if inputs[0]['match_id'] != mid:
            raise ValueError('forecast/result identity mismatch')
        kickoff = legacy.aware(inputs[0]['kickoff_at'])
        for f,missing in zip(forecasts,unavailable):
            if missing and f.get('generated_at') is None:continue
            if f.get('input') is None:raise ValueError('forecast input required')
            if not legacy.aware(f['input']['cutoff_at']) <= legacy.aware(f['generated_at']) < kickoff:
                raise ValueError('not a chronological research forecast')
            if missing:continue
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
        for name, f, missing in zip(('当前模型', 'test模型'), forecasts,unavailable):
            settlement = None; total_audit = _total_audit(f)
            if total_audit['eligible']:
                import numpy as np
                point = np.zeros((51, 51)); point[h, a] = 1.
                probs = legacy.total_settlement(point, f['total']['quote']['line'], f['total']['reference_direction'])
                settlement = next(k for k, v in probs.items() if v == 1.)
            entry['models'][name] = {'version':f.get('version') or 'UNSPECIFIED',
                                     'forecast_available':not missing,
                                     'primary_hit': (f.get('primary') == f'{h}-{a}'
                                                     if f.get('primary') is not None else None),
                                     'wdl_hit': (f.get('wdl_reference') == actual
                                                 if f.get('wdl_reference') in ('主胜', '平局', '客胜') else None),
                                     'total_settlement': settlement,
                                     'total_ineligible_reasons': total_audit['reasons']}
        rows.append(entry)
    summary = {}
    for name, field in (('当前模型', 'current_model'), ('test模型', 'test_model')):
        values = [r['models'][name] for r in rows]
        forecasts = [pair[field] for pair in pairs]
        from collections import Counter
        def counts(items, population):
            primary = [v for v in items if v['primary_hit'] is not None]
            wdl = [v for v in items if v['wdl_hit'] is not None]
            totals = [v for v in items if v['total_settlement'] is not None]
            settlements = Counter(v['total_settlement'] for v in totals)
            non_push = len(totals)-settlements['push']
            selected = {'primary':sum(v.get('primary') is not None for v in population),
                        'wdl':sum(v.get('wdl_reference') in ('主胜','平局','客胜') for v in population),
                        'total':sum(_total_audit(v)['eligible'] for v in population)}
            return {'population':len(population), 'pending':len(population)-len(items), 'settled':len(items),
                    'unavailable_settled':sum(not v['forecast_available'] for v in items),
                    'selected_population':selected,
                    'coverage':{k:v/len(population) if population else 0. for k,v in selected.items()},
                    'primary_eligible':len(primary),
                    'primary_abstentions':len(items)-len(primary),
                    'primary_hits':sum(v['primary_hit'] for v in primary),
                    'primary_accuracy':sum(v['primary_hit'] for v in primary)/len(primary) if primary else None,
                    'wdl_eligible':len(wdl),
                    'wdl_abstentions':len(items)-len(wdl),
                    'wdl_hits':sum(v['wdl_hit'] for v in wdl),
                    'wdl_accuracy':sum(v['wdl_hit'] for v in wdl)/len(wdl) if wdl else None,
                    'total_eligible':len(totals),
                    'total_abstentions':len(items)-len(totals),
                    'total_ineligible_reasons':dict(Counter(reason for v in items for reason in v['total_ineligible_reasons'])),
                    'total_non_push_eligible':non_push,
                    'total_strict_full_win_rate':settlements['win']/non_push if non_push else None,
                    'total_settlements':{k:settlements[k] for k in ('win','half_win','push','half_loss','loss')}}
        versions = {v.get('version') or 'UNSPECIFIED' for v in forecasts}
        grouped = {ver:counts([v for v in values if v['version']==ver],
                             [v for v in forecasts if (v.get('version') or 'UNSPECIFIED')==ver]) for ver in sorted(versions)}
        summary[name] = (dict(counts(values, forecasts),version=next(iter(versions),'NONE'),by_version=grouped)
                         if len(versions)<=1 else {'population':len(forecasts),'pending':len(forecasts)-len(values),'settled':len(values),
                         'aggregation_status':'MIXED_VERSIONS_NO_POOLED_ACCURACY','by_version':grouped})
    return {'models': summary, 'paired_rows': rows, 'pending': pending,
            'scope': len(seen), 'finished': len(rows), 'pending_count': len(pending),
            'selection_policy': 'NO_BEST_OF_TWO_NO_RETROSPECTIVE_SWITCH',
            'formal_kpi_added': 0, 'target_attainment_proven': False}
