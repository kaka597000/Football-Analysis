"""Read-only score concentration and calibration diagnostics; never alter picks.

Normalized rows are an inspection interface, not proof of an as-of freeze.
Use frozen_score_evaluation for prospective provenance and denominator checks.
"""
import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path

import numpy as np

try:
    from .team_goal_analysis import summarize_prediction
    from .score_distribution_audit import parse_score
except ImportError:
    from team_goal_analysis import summarize_prediction
    from score_distribution_audit import parse_score

VERSION = 'WCPP-SCORE-CONCENTRATION-1'
EVENTS = ('score_1_1', 'draw', 'home_zero', 'home_one', 'away_zero', 'away_one',
          'total_ge_3', 'total_ge_4')


def selected_primary(prediction):
    status = str(prediction.get('status', '')).upper()
    if status.startswith('ABSTAIN') or status in ('NOT_EXECUTED', 'FAILED'):
        return None
    return parse_score(prediction['primary']) if prediction.get('primary') is not None else None


def mean(values):
    return float(np.mean(values)) if len(values) else None


def wilson(hits, n):
    """Descriptive 95% binomial interval, not a sequential decision rule."""
    if not n:
        return None
    z = 1.959963984540054
    p = hits / n
    center = (p + z*z/(2*n)) / (1+z*z/n)
    width = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / (1+z*z/n)
    return [max(0., center-width), min(1., center+width)]


def binary_summary(pairs):
    if not pairs:
        return {'n': 0, 'mean_probability': None, 'observed_rate': None}
    n = len(pairs)
    p, y = zip(*pairs)
    bins = []
    for k in range(10):
        values = [(a, b) for a, b in pairs if min(int(a * 10), 9) == k]
        bins.append({'lower': k/10, 'upper': (k+1)/10, 'n': len(values),
                     'mean_probability': mean([v[0] for v in values]),
                     'observed_rate': mean([v[1] for v in values])})
    return {'n': n, 'expected_events': sum(p), 'observed_events': sum(y),
            'mean_probability': mean(p), 'observed_rate': mean(y),
            'observed_rate_wilson95': wilson(sum(y), n),
            'observed_minus_expected_rate': mean(y)-mean(p),
            'brier': mean([(a-b)**2 for a, b in pairs]),
            'ece_10_fixed_bins': sum(b['n']/n * abs(b['mean_probability']-b['observed_rate'])
                                     for b in bins if b['n']), 'bins': bins}


def probability_view(prediction):
    summary = summarize_prediction(prediction)
    if summary['status'] != 'AVAILABLE':
        return {'status': 'UNAVAILABLE', 'reason': summary['reason']}
    m = np.asarray(prediction['matrix'], float)
    h, a = np.indices(m.shape)
    cells = [(float(m[i,j]), i, j) for i in range(m.shape[0]) for j in range(m.shape[1])]
    cells.sort(key=lambda v: (-v[0], v[1], v[2]))
    best = cells[0]
    second = cells[1] if len(cells)>1 else None
    def candidate(c):
        if c is None:
            return None
        return {'score': f'{c[1]}-{c[2]}', 'probability': c[0], 'gap_from_best': best[0]-c[0]}
    non11 = next((c for c in cells if c[1:] != (1, 1)), None)
    zero = next((c for c in cells if 0 in c[1:]), None)
    high = next((c for c in cells if c[1]+c[2]>=3), None)
    p11 = float(m[1,1]) if min(m.shape)>1 else 0.
    primary_score = selected_primary(prediction)
    actual_p = (float(m[primary_score]) if primary_score is not None
                and primary_score[0]<m.shape[0] and primary_score[1]<m.shape[1] else None)
    return {'status': 'AVAILABLE', 'matrix_sha256': summary['source_matrix_sha256'],
            'shape': list(m.shape), 'primary_probability': actual_p,
            'joint_mode': candidate(best), 'runner_up': candidate(second),
            'top_two_gap': best[0]-second[0] if second else None,
            'mode_ties': sum(abs(c[0]-best[0])<=1e-12 for c in cells),
            'best_non_1_1': candidate(non11), 'best_zero_score': candidate(zero),
            'best_three_plus_score': candidate(high),
            'p_1_1_minus_best_alternative': p11-non11[0] if non11 else None,
            'top_five': [candidate(c) for c in cells[:5]],
            'home_mean': summary['home']['mean'], 'away_mean': summary['away']['mean'],
            'total_mean': summary['home']['mean']+summary['away']['mean'],
            'primary_equals_mode': primary_score == best[1:] if primary_score else None,
            'events': dict(zip(EVENTS, [p11, float(m[h==a].sum()), float(m[h==0].sum()),
                float(m[h==1].sum()), float(m[a==0].sum()), float(m[a==1].sum()),
                float(m[h+a>=3].sum()), float(m[h+a>=4].sum())])),
            'home_interval80': summary['home']['central_interval'],
            'away_interval80': summary['away']['central_interval']}


def _result(row):
    result = row.get('result')
    if result is None:
        return None
    if any(type(result.get(k)) is not int or result[k]<0 for k in ('home_goals','away_goals')):
        raise ValueError('nonnegative integer result goals required')
    return result['home_goals'], result['away_goals']


def _group(rows):
    views, outcomes, selected, paired, proper = [], [], [], [], []
    calibration = defaultdict(list)
    matrix_failures = []
    pending = []
    for row in rows:
        pred = row['prediction']
        primary = selected_primary(pred)
        result = _result(row)
        try:
            view = probability_view(pred)
        except (ValueError, TypeError) as error:
            view = {'status': 'INVALID', 'reason': str(error)}
            matrix_failures.append(row['match_id'])
        entry = {'match_id': row['match_id'], 'primary': list(primary) if primary else None,
                 'actual': list(result) if result else None, 'distribution': view,
                 'source': row.get('source')}
        views.append(entry)
        if result is None:
            pending.append(row['match_id'])
            continue
        outcomes.append(result)
        if primary is not None:
            selected.append((primary, result))
        if view['status'] != 'AVAILABLE':
            continue
        h,a = result
        paired.append((result, view))
        event_y = [h==a==1, h==a, h==0, h==1, a==0, a==1, h+a>=3, h+a>=4]
        for name,y in zip(EVENTS,event_y):
            calibration[name].append((view['events'][name],int(y)))
        if primary is not None and view['primary_probability'] is not None:
            calibration['selected_primary'].append((view['primary_probability'],int(primary==result)))
        m = np.asarray(pred['matrix'],float)
        p = float(m[h,a]) if h<m.shape[0] and a<m.shape[1] else 0.
        hg,ag=np.indices(m.shape)
        wdl = np.array([m[hg>ag].sum(),np.trace(m),m[hg<ag].sum()])
        y = np.array([h>a,h==a,h<a],float)
        proper.append({'score_log_loss_clipped_1e_15': -math.log(max(p,1e-15)),
                       'score_brier': float((m*m).sum()-2*p+1),
                       'zero_probability_actual': p==0,
                       'actual_outside_matrix': h>=m.shape[0] or a>=m.shape[1],
                       'wdl_brier': float(np.square(wdl-y).sum()),
                       'wdl_rps': float(np.square(np.cumsum(wdl)[:-1]-np.cumsum(y)[:-1]).sum()/2)})
    n, settled, scored = len(rows), len(outcomes), len(selected)
    hits = sum(p==r for p,r in selected)
    available_views = [e['distribution'] for e in views if e['distribution']['status']=='AVAILABLE']
    p11views = [e['distribution'] for e in views if e['primary']==[1,1] and e['distribution']['status']=='AVAILABLE']
    return {'population': n, 'settled': settled, 'pending': pending,
            'primary': {'issued': sum(e['primary'] is not None for e in views),
                'settled_issued': scored, 'hits': hits,
                'coverage': sum(e['primary'] is not None for e in views)/n if n else None,
                'settled_coverage': scored/settled if settled else None,
                'accuracy_among_issued': hits/scored if scored else None,
                'hits_per_all_settled': hits/settled if settled else None,
                'issued_accuracy_wilson95': wilson(hits,scored),
                'prediction_counts': dict(Counter('-'.join(map(str,e['primary'])) for e in views if e['primary'] is not None)),
                'actual_counts': dict(Counter(f'{h}-{a}' for h,a in outcomes))},
            'matrix_population': len(available_views), 'matrix_settled': len(paired),
            'invalid_matrix_match_ids': matrix_failures,
            'mean_top_two_gap': mean([v['top_two_gap'] for v in available_views if v['top_two_gap'] is not None]),
            'one_one_selection': {'n': len(p11views),
                'mean_probability': mean([v['events']['score_1_1'] for v in p11views]),
                'mean_gap_to_best_alternative': mean([v['p_1_1_minus_best_alternative'] for v in p11views]),
                'fraction_gap_under_1_percentage_point': mean([v['p_1_1_minus_best_alternative']<.01 for v in p11views])},
            'paired_goal_means': {side: {'predicted': mean([v[side+'_mean'] for r,v in paired]),
                'actual': mean([r[0] if side=='home' else r[1] if side=='away' else sum(r) for r,v in paired]),
                'mean_error_predicted_minus_actual': mean([v[side+'_mean']-(r[0] if side=='home' else r[1] if side=='away' else sum(r)) for r,v in paired])}
                for side in ('home','away','total')},
            'interval80': {side: {'n':len(paired), 'empirical_coverage': mean([v[side+'_interval80']['lower']<=r[i]<=v[side+'_interval80']['upper'] for r,v in paired])} for i,side in enumerate(('home','away'))},
            'calibration': {name: binary_summary(calibration[name]) for name in (*EVENTS,'selected_primary')},
            'proper_scores': {'n': len(proper), **{key: mean([p[key] for p in proper]) for key in ('score_log_loss_clipped_1e_15','score_brier','wdl_brier','wdl_rps')},
                'zero_probability_actual_count': sum(p['zero_probability_actual'] for p in proper),
                'actual_outside_matrix_count': sum(p['actual_outside_matrix'] for p in proper)},
            'rows': views}


def diagnose(rows):
    """Group track/version/cohort separately; never silently pool model versions."""
    grouped = defaultdict(list)
    seen = set()
    for row in rows:
        key = (str(row['match_id']), row['track'])
        if key in seen:
            raise ValueError('duplicate match/track; run revisions as a separate diagnostic')
        seen.add(key)
        group = (row['track'],row['prediction'].get('version') or 'UNAVAILABLE',row.get('cohort') or 'UNSPECIFIED')
        grouped[group].append(row)
    return {'schema': VERSION, 'scope': 'DESCRIPTIVE_ONLY_NOT_RETROSPECTIVE_VALIDATION',
            'prediction_modified': False, 'automatic_promotion': False,
            'groups': [{'track':k[0], 'version':k[1], 'cohort':k[2], **_group(v)} for k,v in sorted(grouped.items())],
            'limitations': ['Input provenance must be verified separately.',
                'A frequent 1-1 mode is not the same as assigning most probability to 1-1.',
                'Calibration bins and intervals are descriptive on this sample; no independence or sequential testing claim.',
                'No forced score diversification, extra goals, parameter fitting or promotion.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('rows'); parser.add_argument('--out',required=True)
    args = parser.parse_args()
    report = diagnose(json.loads(Path(args.rows).read_text()))
    with Path(args.out).open('x',encoding='utf-8') as handle:
        json.dump(report,handle,ensure_ascii=False,indent=2,allow_nan=False)


if __name__ == '__main__':
    main()
