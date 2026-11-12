"""One-pass 2526 temporal check of retained, unchanged PR32 coefficients.

2526 was excluded from PR32 fitting. It is not certified project-wide unseen,
as-of data, or an active Current/Test comparison. No refit or selection occurs.
"""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
from research_program import elo_kelly_ablation as experiment


def goals_summary(predictions):
    groups = defaultdict(list)
    for row in predictions:
        groups['all'].append(row); groups[row['league']].append(row)
    result = {}
    for group, rows in groups.items():
        arms = {arm: {'n': len(rows), 'hits': sum(r['models'][arm]['metrics']['top1'] for r in rows),
                     'score_logloss': float(np.mean([r['models'][arm]['metrics']['score_logloss'] for r in rows])),
                     'wdl_brier': float(np.mean([r['models'][arm]['metrics']['wdl_brier'] for r in rows]))}
                for arm in ('goals', 'goals_elo')}
        dates = defaultdict(lambda: [0, 0])
        for row in rows:
            dates[row['date']][0] += row['models']['goals_elo']['metrics']['top1'] - row['models']['goals']['metrics']['top1']
            dates[row['date']][1] += 1
        values = np.asarray(list(dates.values())); rng = np.random.default_rng(20261112)
        selected = values[rng.integers(len(values), size=(2000, len(values)))].sum(axis=1)
        result[group] = {'arms': arms, 'net_hits': arms['goals_elo']['hits']-arms['goals']['hits'],
                         'net_rate_ci95_date_blocks': np.quantile(selected[:, 0]/selected[:, 1], [.025, .975]).tolist()}
    return result


def run(manifest, models_path, out):
    out = Path(out); out.mkdir(parents=True, exist_ok=False)
    models = json.loads(Path(models_path).read_text())
    protocol = {'created_at': datetime.now(timezone.utc).isoformat(), 'target_season': '2526',
                'model_sha256': hashlib.sha256(Path(models_path).read_bytes()).hexdigest(),
                'manifest_sha256': hashlib.sha256(Path(manifest).read_bytes()).hexdigest(),
                'source_sha256': hashlib.sha256(Path(experiment.__file__).read_bytes()).hexdigest(),
                'fixed_original_protocol': experiment.PROTOCOL,
                'training': 'original retained final models; no refit, calibration or selection',
                'features': 'same prior-match/Elo updates; each date block predicts before labels update',
                'kelly_control': 'goals_market with identical book panel, without Kelly transform',
                'independent_goals_population': 'all feature-eligible targets; no market required for goals versus goals_elo',
                'project_wide_unseen_certified': False, 'historical_asof_certified': False,
                'active_comparator': False, 'promotion': False}
    experiment.write(out / 'protocol.json', protocol)
    rows, exclusions = experiment.load(manifest, seasons=experiment.PROTOCOL['seasons'] + ['2526'])
    features = experiment.feature_rows(rows)
    targets = [r for r in features if r['season'] == '2526']
    population = [r for r in rows if r['season'] == '2526']; predictions = []; goal_predictions = []
    featured_ids = {r['id'] for r in targets}
    exclusions += [{'id': r['id'], 'reason': 'insufficient_prior_history'}
                   for r in population if r['id'] not in featured_ids]
    for r in targets:
        goals = {}
        for arm in ('goals', 'goals_elo'):
            model = models[r['league']][arm]
            if model['train_end'] >= r['date']:
                raise ValueError('retained model reaches evaluation date')
            mu = experiment.predict(model, r)
            goals[arm] = {'lambda': mu.tolist(), 'top1': [max(0, math.ceil(v)-1) for v in mu],
                          'metrics': experiment.metrics(r, mu)}
        goal_predictions.append({k: r[k] for k in ('id', 'league', 'date', 'hg', 'ag', 'source_sha256', 'source_line')} | {'models': goals})
        if r['market'] is None:
            exclusions.append({'id': r['id'], 'reason': 'missing_same_market_panel'}); continue
        result = {}
        for arm in experiment.ARMS:
            model = models[r['league']][arm]
            if model['train_end'] >= r['date']:
                raise ValueError('retained model reaches evaluation date')
            mu = experiment.predict(model, r)
            result[arm] = {'lambda': mu.tolist(), 'top1': [max(0, math.ceil(v)-1) for v in mu],
                           'metrics': experiment.metrics(r, mu)}
        predictions.append({k: r[k] for k in ('id', 'league', 'date', 'hg', 'ag', 'source_sha256', 'source_line')} | {'models': result})
    report = {'status': 'FIXED_COEFFICIENT_TEMPORAL_RESEARCH_ONLY', 'population': len(population),
              'predicted': len(predictions), 'coverage': len(predictions)/len(population) if population else None,
              'summary': experiment.summary(predictions) if predictions else {},
              'refits': 0, 'new_hyperparameters': 0, 'selection': None, 'active_modified': False,
              'future_validated': False, 'project_wide_unseen_certified': False,
              'limitations': ['Closing odds lack source quote and first-known timestamps.',
                              'Original dataset has been retained for other research; global unseen status is not established.',
                              'No contemporaneous active Current/Test predictions cover this historical interval.']}
    experiment.write(out / 'predictions.json', predictions)
    experiment.write(out / 'goals-only-predictions.json', goal_predictions)
    experiment.write(out / 'goals-only-report.json', {
        'population': len(population), 'predicted': len(goal_predictions),
        'coverage': len(goal_predictions)/len(population) if population else None,
        'summary': goals_summary(goal_predictions), 'asof_certified': False,
        'active_comparator': False, 'project_wide_unseen_certified': False,
        'refits': 0, 'selected_model': None, 'promotion': False})
    experiment.write(out / 'exclusions.json', exclusions)
    experiment.write(out / 'report.json', report)
    return {'population': len(population), 'predicted': len(predictions),
            'independent_goals_predicted': len(goal_predictions), 'refits': 0}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest', required=True); p.add_argument('--models', required=True)
    p.add_argument('--out', required=True); a = p.parse_args()
    print(json.dumps(run(a.manifest, a.models, a.out)))
