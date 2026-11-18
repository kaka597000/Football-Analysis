"""Independent, paired accounting for 当前模型 and test模型."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import numpy as np

from weighted_model import legacy


ROLES = (('当前模型', 'current_model'), ('test模型', 'test_model'))
DIRECTIONS = ('主胜', '平局', '客胜')
SETTLEMENTS = ('win', 'half_win', 'push', 'half_loss', 'loss')


def _total_audit(forecast):
    from research_program.future_validation import quote_audit

    fixture = forecast.get('input') or {}
    reasons = list(quote_audit(fixture)['reasons'])
    total = forecast.get('total') or {}
    if total.get('reference_direction') not in ('over', 'under'):
        reasons.append('NO_FROZEN_TOTAL_DIRECTION')
    quote = total.get('quote') or {}
    source = fixture.get('total_quote') or {}
    fields = ('match_id', 'bookmaker', 'market', 'line', 'quoted_at', 'query_time',
              'over_odds', 'under_odds', 'source_url')
    # Early predictions copied the price and URL without the optional raw-source metadata.
    claimed_provenance = (key for key in ('provider', 'source_sha256') if quote.get(key) is not None)
    if (any(quote.get(key) != source.get(key) for key in fields)
            or any(quote[key] != source.get(key) for key in claimed_provenance)):
        reasons.append('FROZEN_TOTAL_QUOTE_MISMATCH')
    return {'eligible': not reasons, 'reasons': reasons}


def _unavailable(forecast):
    if forecast.get('matrix') is not None:
        return False
    status = forecast.get('status', '')
    if (not isinstance(status, str) or not (status == 'ABSTAIN' or status.startswith(('ABSTAIN_', 'UNAVAILABLE')))
            or any(forecast.get(k) is not None for k in ('primary', 'wdl', 'wdl_reference', 'total'))):
        raise ValueError('missing matrix requires explicit unavailable track without recommendations')
    return True


def _completed_results(results):
    finished = {}
    seen = set()
    for result in results:
        mid = result['match_id']
        if mid in seen:
            raise ValueError('duplicate result')
        seen.add(mid)
        statuses = [result[k] in ('completed', 'FT') for k in ('status', 'event_status') if result.get(k) is not None]
        if not statuses:
            raise ValueError('explicit result status required')
        if not any(statuses):
            continue
        if not all(statuses):
            raise ValueError('conflicting result status')
        source = urlparse(result.get('source_url') or '')
        if result.get('score_basis') != 'regulation_90' or source.scheme != 'https' or not source.hostname:
            raise ValueError('verified 90-minute result required')
        if any(type(result.get(k)) is not int or not 0 <= result[k] <= 30 for k in ('home_goals', 'away_goals')):
            raise ValueError('invalid result scores')
        finished[mid] = result
    return finished


def _fixture_kickoff(pair, forecasts, result):
    inputs = [f['input'] for f in forecasts if f.get('input') is not None]
    if pair.get('fixture') is not None:
        inputs.append(pair['fixture'])
    if not inputs:
        raise ValueError('frozen fixture context required for unavailable pair')
    fixture = inputs[0]
    if any(other != fixture for other in inputs[1:]):
        raise ValueError('unpaired source/cutoff inputs')
    if fixture['match_id'] != result['match_id']:
        raise ValueError('forecast/result identity mismatch')
    # Older review files have only match_id. Check every additional identity
    # supplied by both records, with the same ordered sides as the frozen score.
    for key in ('competition', 'cohort', 'stage', 'home', 'away'):
        if result.get(key) is not None and fixture.get(key) is not None and result[key] != fixture[key]:
            raise ValueError('forecast/result identity mismatch: ' + key)
    kickoff = legacy.aware(fixture['kickoff_at'])
    if result.get('kickoff_at') is not None and legacy.aware(result['kickoff_at']) != kickoff:
        raise ValueError('forecast/result identity mismatch: kickoff_at')
    return kickoff


def _counts(items, population):
    primary = [v['primary_hit'] for v in items if v['primary_hit'] is not None]
    wdl = [v['wdl_hit'] for v in items if v['wdl_hit'] is not None]
    settlements = Counter(v['total_settlement'] for v in items if v['total_settlement'] is not None)
    total_n = sum(settlements.values())
    non_push = total_n - settlements['push']
    selected = {k: sum(v[k] for v in population) for k in ('primary', 'wdl', 'total')}
    return {
        'population': len(population), 'pending': len(population) - len(items), 'settled': len(items),
        'unavailable_settled': sum(not v['forecast_available'] for v in items),
        'selected_population': selected,
        'coverage': {k: n / len(population) if population else 0. for k, n in selected.items()},
        'primary_eligible': len(primary), 'primary_abstentions': len(items) - len(primary),
        'primary_hits': sum(primary), 'primary_accuracy': sum(primary) / len(primary) if primary else None,
        'wdl_eligible': len(wdl), 'wdl_abstentions': len(items) - len(wdl),
        'wdl_hits': sum(wdl), 'wdl_accuracy': sum(wdl) / len(wdl) if wdl else None,
        'total_eligible': total_n, 'total_abstentions': len(items) - total_n,
        'total_ineligible_reasons': dict(Counter(reason for v in items for reason in v['total_ineligible_reasons'])),
        'total_non_push_eligible': non_push,
        'total_strict_full_win_rate': settlements['win'] / non_push if non_push else None,
        'total_settlements': {k: settlements[k] for k in SETTLEMENTS}}


def review(pairs, results):
    result_map = _completed_results(results)
    populations = {name: defaultdict(list) for name, _ in ROLES}
    seen, rows, pending = set(), [], []
    for pair in pairs:
        mid = pair['match_id']
        if mid in seen:
            raise ValueError('duplicate model pair')
        seen.add(mid)
        forecasts = [pair[field] for _, field in ROLES]
        audits = [_total_audit(f) for f in forecasts]
        for (role, _), f, audit in zip(ROLES, forecasts, audits):
            populations[role][f.get('version') or 'UNSPECIFIED'].append({
                'primary': f.get('primary') is not None, 'wdl': f.get('wdl_reference') in DIRECTIONS,
                'total': audit['eligible']})
        result = result_map.get(mid)
        if result is None:
            pending.append(mid)
            continue
        kickoff = _fixture_kickoff(pair, forecasts, result)
        observed = legacy.aware(result['observed_at'])
        if not kickoff + timedelta(minutes=90) <= observed <= datetime.now(timezone.utc):
            raise ValueError('result not observed after match')
        home, away = result['home_goals'], result['away_goals']
        actual_score = f'{home}-{away}'
        actual_wdl = '主胜' if home > away else '客胜' if home < away else '平局'
        entry = {'match_id': mid, 'result': actual_score, 'models': {},
                 'formal_kpi_eligible': False, 'kickoff_conflict': pair.get('kickoff_conflict')}
        for (role, _), f, audit in zip(ROLES, forecasts, audits):
            missing = _unavailable(f)
            if not missing or f.get('generated_at') is not None:
                if f.get('input') is None:
                    raise ValueError('forecast input required')
                if not legacy.aware(f['input']['cutoff_at']) <= legacy.aware(f['generated_at']) < kickoff:
                    raise ValueError('not a chronological research forecast')
            if not missing and legacy.digest(f['matrix']) != f['matrix_hash']:
                raise ValueError('tampered forecast probability matrix')
            settlement = None
            if audit['eligible']:
                point = np.zeros((51, 51))
                point[home, away] = 1.
                probs = legacy.total_settlement(point, f['total']['quote']['line'], f['total']['reference_direction'])
                settlement = next(k for k, value in probs.items() if value == 1.)
            entry['models'][role] = {
                'version': f.get('version') or 'UNSPECIFIED', 'forecast_available': not missing,
                'primary_hit': f['primary'] == actual_score if f.get('primary') is not None else None,
                'wdl_hit': f['wdl_reference'] == actual_wdl if f.get('wdl_reference') in DIRECTIONS else None,
                'total_settlement': settlement, 'total_ineligible_reasons': audit['reasons']}
        rows.append(entry)

    summary = {}
    for role, population in populations.items():
        settled = defaultdict(list)
        for row in rows:
            value = row['models'][role]
            settled[value['version']].append(value)
        grouped = {v: _counts(settled[v], population[v]) for v in sorted(population)}
        if len(grouped) <= 1:
            version = next(iter(grouped), 'NONE')
            counts = grouped[version] if grouped else _counts([], [])
            summary[role] = dict(counts, version=version, by_version=grouped)
        else:
            summary[role] = {'population': len(seen), 'pending': len(pending), 'settled': len(rows),
                             'aggregation_status': 'MIXED_VERSIONS_NO_POOLED_ACCURACY', 'by_version': grouped}
    return {'models': summary, 'paired_rows': rows, 'pending': pending,
            'scope': len(seen), 'finished': len(rows), 'pending_count': len(pending),
            'selection_policy': 'NO_BEST_OF_TWO_NO_RETROSPECTIVE_SWITCH',
            'formal_kpi_added': 0, 'target_attainment_proven': False}
