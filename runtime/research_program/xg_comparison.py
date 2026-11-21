"""Inactive common-cohort xG comparison. Strict availability is the default.

Reconstructed from the retained 2026-11-10 source after the temporary workspace
was lost. Fixed numerical protocol retained; no active model or old freeze is
written. Retrospective mode is never an as-of certified backtest.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import warnings

import numpy as np
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import PoissonRegressor

from .feature_ablation import BASE_NAMES, XG_NAMES, group_key, paired_summary, score_prediction

PROTOCOL = {
    'version': 'WCPP-XG-COMMON-COHORT-20261110-1',
    'competitions': ['eng.1', 'esp.1', 'fra.1', 'ger.1', 'ita.1'],
    'window_matches': 10, 'min_history_matches': 3,
    'max_xg_age_days': 90, 'half_life_days': 30,
    'min_training_fixtures': 200, 'ridge_alpha': .1,
    'max_iter': 500, 'tol': 1e-9,
    'fit_schedule': 'UTC month start, expanding, labels known strictly before fit',
    'grouping': 'exact (cohort, competition, stage), no identity or phase alias collapse',
    'selection': 'fixed four arms; no hyperparameter search or winner selection',
    'deployment': 'INACTIVE_SHADOW_ONLY',
    'original': 'same prior-goal and prior-Understat-xG recipe as feature_ablation',
    'fresh': '30-day exponential decay; 90-day cap; training-only mean imputation plus missingness',
    'residual': 'fresh arm plus xG minus opponent pre-match fresh mean, computed at that historical cutoff',
    'common_cohort': 'all four arms use identical fixtures, labels, and monthly training IDs',
    'primary': 'statistical joint Poisson Top1, NOT comprehensive or active Current/Test Primary',
}
VARIANTS = ('goals_only', 'original_xg', 'fresh_missingness', 'opponent_residual')
FRESH_SIDE_NAMES = ['xg', 'xga', 'missing', 'observed_count', 'effective_n',
                    'latest_age_days', 'missing_fraction_prior_goal_window']
RESIDUAL_SIDE_NAMES = ['xg_residual', 'xga_residual', 'residual_missing',
                       'residual_observed_count', 'residual_effective_n']
FEATURE_NAMES = {
    'goals_only': BASE_NAMES,
    'original_xg': BASE_NAMES + XG_NAMES,
    'fresh_missingness': BASE_NAMES + [s + '_' + k for s in ('own', 'opponent') for k in FRESH_SIDE_NAMES],
    'opponent_residual': BASE_NAMES + [s + '_' + k for s in ('own', 'opponent') for k in FRESH_SIDE_NAMES]
                         + [s + '_' + k for s in ('own', 'opponent') for k in RESIDUAL_SIDE_NAMES],
}


def aware(value):
    if not isinstance(value, str):
        raise ValueError('explicit timezone-aware timestamp required')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('timezone required')
    return result.astimezone(timezone.utc)


def valid_score(r):
    return r.get('score_basis') == 'regulation_90' and all(
        type(r.get(k)) is int and r[k] >= 0 for k in ('home_goals_90m', 'away_goals_90m'))


def valid_xg(r):
    x = (r.get('features') or {}).get('xg') or {}
    return (x.get('provider') == 'understat' and x.get('date') == r['date']
            and bool(x.get('provider_match_id'))
            and bool(re.fullmatch(r'[0-9a-f]{64}', str(x.get('source_payload_sha256', ''))))
            and all(type(x.get(k)) in (int, float) and math.isfinite(x[k]) and x[k] >= 0
                    for k in ('home_xg', 'away_xg', 'home_xga', 'away_xga'))
            and x['home_xga'] == x['away_xg'] and x['away_xga'] == x['home_xg'])


def target_cutoff(r, mode):
    if mode == 'retrospective':
        return aware(r['date'] + 'T00:00:00Z')
    cutoff, kickoff = aware(r.get('feature_available_by')), aware(r.get('kickoff_at'))
    if cutoff >= kickoff:
        raise ValueError('feature cutoff must be strictly before kickoff')
    return cutoff


def known_by(event, cutoff, mode, strict=False):
    if mode == 'retrospective':
        return True
    try:
        known, kickoff = aware(event.get('first_known_at')), aware(event.get('kickoff_at'))
    except (TypeError, ValueError):
        return False
    return known > kickoff and (known < cutoff if strict else known <= cutoff)


def weighted(events, current_date, fields, protocol):
    """Missing remains None, not a fabricated zero observation."""
    if not events:
        return [None for _ in fields], 0.
    age = np.asarray([(date.fromisoformat(current_date) - date.fromisoformat(e['date'])).days for e in events])
    weights = np.exp2(-age / protocol['half_life_days'])
    values = np.asarray([[e[k] for k in fields] for e in events], dtype=float)
    return np.average(values, axis=0, weights=weights).tolist(), float(weights.sum()**2 / np.square(weights).sum())


def fresh(events, current_date, protocol):
    return [e for e in events if 0 < (date.fromisoformat(current_date) - date.fromisoformat(e['date'])).days
            <= protocol['max_xg_age_days']][-protocol['window_matches']:]


def availability_audit(records):
    return {'input_fixtures': len(records),
            'missing_label_first_known_at': sum(not r.get('label_first_known_at') for r in records),
            'missing_feature_available_by': sum(not r.get('feature_available_by') for r in records),
            'missing_kickoff_at': sum(not r.get('kickoff_at') for r in records),
            'xg_observations': sum(valid_xg(r) for r in records),
            'xg_missing_first_known_at': sum(valid_xg(r) and not r['features']['xg'].get('first_known_at') for r in records),
            'collection_time_used_as_first_known': False}


def build_features(records, mode='asof', protocol=None):
    p = dict(PROTOCOL if protocol is None else protocol)
    if mode not in ('asof', 'retrospective'):
        raise ValueError('unknown availability mode')
    if (type(p['window_matches']) is not int or type(p['min_history_matches']) is not int
            or not 1 <= p['min_history_matches'] <= p['window_matches']
            or type(p['max_xg_age_days']) is not int or p['max_xg_age_days'] < 1
            or not math.isfinite(p['half_life_days']) or p['half_life_days'] <= 0):
        raise ValueError('invalid fixed window/freshness protocol')
    histories = defaultdict(lambda: {'goals': [], 'xg': [], 'residual': []})
    prepared, excluded, ids, identities = [], [], set(), set()
    for r in sorted(records, key=lambda r: (r['date'], r['match_id'])):
        date.fromisoformat(r['date'])
        key = group_key(r)
        if not all(isinstance(r.get(k), str) and r[k] for k in ('match_id', 'home_team_id', 'away_team_id')):
            raise ValueError('canonical fixture and team IDs required')
        if r['home_team_id'] == r['away_team_id']:
            raise ValueError('identical opposing team IDs')
        identity = (key, r['date'], r['home_team_id'], r['away_team_id'])
        if r['match_id'] in ids or identity in identities:
            raise ValueError('duplicate fixture identity')
        ids.add(r['match_id']); identities.add(identity)
        score_ok, xg_ok = valid_score(r), valid_xg(r)
        try:
            cutoff = target_cutoff(r, mode)
        except (TypeError, ValueError):
            cutoff = None
        team_histories = [histories[(key, r[s + '_team_id'])] for s in ('home', 'away')]
        selected = [{kind: [e for e in h[kind] if e['date'] < r['date']
                           and cutoff is not None and known_by(e, cutoff, mode)][-p['window_matches']:]
                     for kind in ('goals', 'xg', 'residual')} for h in team_histories]
        recent = [fresh(h['xg'], r['date'], p) for h in selected]
        recent_residual = [fresh(h['residual'], r['date'], p) for h in selected]
        reason = ('MISSING_OR_INVALID_PREMATCH_CUTOFF' if cutoff is None
                  else 'MISSING_90_MINUTE_LABEL' if not score_ok
                  else 'INSUFFICIENT_COMMON_PRIOR_GOAL_OR_XG_HISTORY'
                  if min(len(h[k]) for h in selected for k in ('goals', 'xg')) < p['min_history_matches'] else None)
        if reason:
            excluded.append({'match_id': r['match_id'], 'reason': reason})
        else:
            goal_means = [np.mean([[e['for'], e['against']] for e in h['goals']], axis=0).tolist() for h in selected]
            xg_means = [np.mean([[e['for'], e['against']] for e in h['xg']], axis=0).tolist() for h in selected]
            fresh_parts, residual_parts = [], []
            for i, h in enumerate(selected):
                means, effective = weighted(recent[i], r['date'], ('for', 'against'), p)
                latest_age = ((date.fromisoformat(r['date']) - date.fromisoformat(recent[i][-1]['date'])).days
                              if recent[i] else None)
                observed = {e['match_id'] for e in recent[i]}
                missing_fraction = sum(e['match_id'] not in observed for e in h['goals']) / len(h['goals'])
                fresh_parts.append([*means, int(not recent[i]), len(recent[i]), effective, latest_age, missing_fraction])
                residual_means, effective_r = weighted(recent_residual[i], r['date'], ('for', 'against'), p)
                residual_parts.append([*residual_means, int(not recent_residual[i]), len(recent_residual[i]), effective_r])
            features = {v: [] for v in VARIANTS}
            for own, other in ((0, 1), (1, 0)):
                base = [float(own == 0), *goal_means[own], *goal_means[other]]
                features['goals_only'].append(base)
                features['original_xg'].append(base + xg_means[own] + xg_means[other])
                features['fresh_missingness'].append(base + fresh_parts[own] + fresh_parts[other])
                features['opponent_residual'].append(base + fresh_parts[own] + fresh_parts[other]
                                                       + residual_parts[own] + residual_parts[other])
            prepared.append({k: r.get(k) for k in ('match_id', 'date', 'cohort', 'competition', 'stage', 'season',
                                                  'home_team_id', 'away_team_id', 'home', 'away', 'kickoff_at')}
                            | {'month': cutoff.strftime('%Y-%m'), 'evaluation_cutoff': cutoff.isoformat(),
                               'feature_available_by': r.get('feature_available_by'), 'availability_mode': mode,
                               'cutoff_is_date_only_assumption': mode == 'retrospective',
                               'label_first_known_at': r.get('label_first_known_at'),
                               'actual_score': [r['home_goals_90m'], r['away_goals_90m']],
                               'features': features, 'dependencies': dict(zip(('home', 'away'), selected)),
                               'fresh_dependencies': dict(zip(('home', 'away'), recent)),
                               'residual_dependencies': dict(zip(('home', 'away'), recent_residual))})
        # Append only after constructing target features; explicit date filtering
        # also excludes other same-day fixtures already processed.
        if not score_ok:
            continue
        x = (r.get('features') or {}).get('xg') or {}
        for i, (side, other) in enumerate((('home', 'away'), ('away', 'home'))):
            h = team_histories[i]
            common = {'match_id': r['match_id'], 'date': r['date'], 'kickoff_at': r.get('kickoff_at')}
            h['goals'].append(common | {'for': r[side + '_goals_90m'], 'against': r[other + '_goals_90m'],
                                       'first_known_at': r.get('label_first_known_at')})
            if not xg_ok:
                continue
            event = common | {'for': x[side + '_xg'], 'against': x[other + '_xg'],
                              'first_known_at': x.get('first_known_at'), 'provider': 'understat',
                              'provider_match_id': x['provider_match_id'],
                              'source_payload_sha256': x['source_payload_sha256']}
            h['xg'].append(event)
            opponent = recent[1-i]
            if cutoff is not None and len(opponent) >= p['min_history_matches']:
                expectation, _ = weighted(opponent, r['date'], ('for', 'against'), p)
                h['residual'].append(event | {'for': event['for'] - expectation[1],
                                             'against': event['against'] - expectation[0],
                                             'opponent_expectation': expectation,
                                             'opponent_baseline_cutoff': cutoff.isoformat(),
                                             'opponent_dependencies': opponent})
    return prepared, excluded


def fit_model(rows, variant, protocol):
    x = np.asarray([v for r in rows for v in r['features'][variant]], dtype=float)
    y = np.asarray([v for r in rows for v in r['actual_score']], dtype=float)
    n = np.isfinite(x).sum(axis=0)
    means = np.divide(np.nansum(x, axis=0), n, out=np.zeros(x.shape[1]), where=n > 0)
    filled = np.where(np.isfinite(x), x, means)
    scale = filled.std(axis=0); scale[scale == 0] = 1.
    model = PoissonRegressor(alpha=protocol['ridge_alpha'], max_iter=protocol['max_iter'], tol=protocol['tol'])
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always', ConvergenceWarning)
        model.fit((filled - means) / scale, y)
    if any(issubclass(w.category, ConvergenceWarning) for w in caught):
        raise RuntimeError('Poisson GLM did not converge')
    return {'feature_names': FEATURE_NAMES[variant], 'mean': means.tolist(), 'scale': scale.tolist(),
            'coef': model.coef_.tolist(), 'intercept': float(model.intercept_), 'n_iter': int(model.n_iter_),
            'training_observed_counts': n.tolist(), 'unlearned_columns': np.flatnonzero(n == 0).tolist(),
            'imputation': 'training means only; all-missing training columns zero standardized and coefficient zero'}


def predict(model, x):
    values = np.asarray(x, dtype=float)
    values = np.where(np.isfinite(values), values, model['mean'])
    values[:, model['unlearned_columns']] = np.asarray(model['mean'])[model['unlearned_columns']]
    result = np.exp(((values - model['mean']) / model['scale']) @ np.asarray(model['coef']) + model['intercept'])
    if not np.all(np.isfinite(result)) or not np.all(result > 0):
        raise ValueError('invalid Poisson rates')
    return result.tolist()


def summaries(predictions):
    comparisons = [('goals_only', 'original_xg'), ('original_xg', 'fresh_missingness'),
                   ('original_xg', 'opponent_residual'), ('fresh_missingness', 'opponent_residual')]
    return {candidate + '_vs_' + baseline: paired_summary([
        {**r, 'baseline': r['models'][baseline], 'plus_xg': r['models'][candidate]} for r in predictions])
            for baseline, candidate in comparisons}


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def write_jsonl(path, rows):
    with Path(path).open('x') as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')


def run(records, out_dir, input_sha256, mode='asof', source_metadata=None, protocol=None):
    p = dict(PROTOCOL if protocol is None else protocol)
    if mode not in ('asof', 'retrospective'):
        raise ValueError('unknown availability mode')
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=False)
    selected = [r for r in records if r['competition'] in p['competitions']]
    audit = availability_audit(selected)
    write_json(out / 'protocol.json', p | {'mode': mode, 'input_sha256': input_sha256,
               'source_metadata': source_metadata or {}, 'availability_audit': audit,
               'created_at_beijing': datetime.now(timezone(timedelta(hours=8))).isoformat(),
               'module_sha256': sha256(Path(__file__).read_bytes()).hexdigest()})
    rows, excluded = build_features(selected, mode, p)
    write_jsonl(out / 'features.jsonl', rows)
    groups = defaultdict(list)
    for r in rows:
        groups[group_key(r)].append(r)
    predictions, fits, skipped = [], [], []
    for group, group_rows in sorted(groups.items(), key=lambda item: str(item[0])):
        for month in sorted({r['month'] for r in group_rows}):
            boundary = aware(month + '-01T00:00:00Z')
            targets = [r for r in group_rows if r['month'] == month]
            train = [r for r in group_rows if r['date'] < month + '-01'
                     and aware(r['evaluation_cutoff']) < boundary
                     and known_by({'first_known_at': r['label_first_known_at'], 'kickoff_at': r['kickoff_at']}, boundary, mode, strict=True)]
            if len(train) < p['min_training_fixtures']:
                skipped.extend({'match_id': r['match_id'], 'reason': 'INSUFFICIENT_MONTH_START_KNOWN_TRAINING',
                                'training_fixtures': len(train)} for r in targets)
                continue
            artifacts = {variant: fit_model(train, variant, p) for variant in VARIANTS}
            fit_id = ':'.join(str(v) for v in group) + ':' + month
            train_ids = [r['match_id'] for r in train]
            fits.append({'model_id': fit_id, 'group': group, 'fit_boundary': boundary.isoformat(),
                         'training_match_ids': train_ids, 'training_fixtures': len(train),
                         'training_match_ids_sha256': sha256('\n'.join(train_ids).encode()).hexdigest(),
                         'models': artifacts})
            for r in targets:
                prediction = {k: r[k] for k in ('match_id', 'date', 'month', 'cohort', 'competition', 'stage',
                                                'actual_score', 'feature_available_by', 'evaluation_cutoff',
                                                'availability_mode', 'cutoff_is_date_only_assumption')}
                prediction.update(model_id=fit_id, models={variant: score_prediction(
                    predict(artifacts[variant], r['features'][variant]), r['actual_score']) for variant in VARIANTS})
                predictions.append(prediction)
        print(json.dumps({'group': group, 'common_feature_fixtures': len(group_rows),
                          'predicted_total': len(predictions)}), flush=True)
    predictions.sort(key=lambda r: (r['date'], r['match_id']))
    for name, values in [('predictions', predictions), ('fits', fits), ('excluded', excluded + skipped)]:
        write_jsonl(out / (name + '.jsonl'), values)
    summary = {'protocol': p['version'], 'mode': mode,
               'status': ('BLOCKED_NO_PAIRED_PREDICTIONS' if not predictions else
                          'COMPLETED_TIMESTAMP_GATED_SHADOW' if mode == 'asof' else 'COMPLETED_RETROSPECTIVE_NOT_ASOF'),
               'active_modified': False, 'promotion': False, 'future_validation': False,
               'coverage': {'all_input': len(records), 'selected': len(selected), 'common_feature_eligible': len(rows),
                            'paired_predictions': len(predictions), 'four_arm_monthly_fits': len(fits),
                            'fitted_models': len(fits) * len(VARIANTS),
                            'exclusion_reasons': dict(Counter(r['reason'] for r in excluded + skipped)),
                            'fraction_of_selected': len(predictions) / len(selected) if selected else 0},
               'availability_audit': audit, 'comparisons': summaries(predictions),
               'score_selection_diagnostics': {
                   variant: {'predicted_1_1': sum(r['models'][variant]['statistical_top1'] == [1, 1] for r in predictions),
                             'actual_1_1': sum(r['actual_score'] == [1, 1] for r in predictions),
                             'top1_counts': dict(Counter('-'.join(map(str, r['models'][variant]['statistical_top1'])) for r in predictions))}
                   for variant in VARIANTS},
               'by_group': {':'.join(str(v) for v in g): summaries([r for r in predictions if group_key(r) == g]) for g in groups},
               'by_year': {year: summaries([r for r in predictions if r['date'][:4] == year]) for year in sorted({r['date'][:4] for r in predictions})},
               'limitations': ['Historical development data; no proof of future accuracy or promotion.',
                               'Statistical Top1 is not active Current/Test or comprehensive Primary.',
                               'Strict gates validate supplied timestamp consistency, not original-source truth.',
                               'Retrospective mode is never as-of certified; collection time is not first-known.',
                               'No real-line market model or settlement; 4+ tail metrics are not betting accuracy.',
                               'Canonical home role only; neutral venue not verified.',
                               'Freshness and opponent adjustment are fixed recipes, not validated improvements.',
                               'Common cohort inherits original observed-xG eligibility; missing original xG stays excluded.',
                               'Raw source hash identity is retained; source payloads are not fetched or reverified here.']}
    write_json(out / 'summary.json', summary)
    write_json(out / 'manifest.json', {f.name: sha256(f.read_bytes()).hexdigest() for f in sorted(out.iterdir()) if f.is_file()})
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--records', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--mode', choices=['asof', 'retrospective'], default='asof')
    args = parser.parse_args()
    raw = Path(args.records).read_bytes(); dataset = json.loads(raw)
    result = run(dataset['records'], args.out, sha256(raw).hexdigest(), args.mode,
                 {k: dataset.get(k) for k in ('schema_version', 'source_database_sha256', 'counts')})
    print(json.dumps({k: result[k] for k in ('status', 'coverage', 'comparisons')}, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
