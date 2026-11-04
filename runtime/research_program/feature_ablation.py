"""Fixed-protocol, retrospective prior-match xG ablation; never an active model.

Run as a module from the repository root. No target fixture's process value is
read while constructing its features. Exact date blocks and cohort/phase keys
are deliberately conservative when kickoff or identity evidence is incomplete.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone, timedelta
from hashlib import sha256
from itertools import groupby
import json
import math
from pathlib import Path
import warnings

import numpy as np
from scipy.stats import binomtest, poisson, skellam
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import PoissonRegressor

PROTOCOL = {
    'version': 'WCPP-XG-PRIOR-ABLATION-20261104-1',
    'competitions': ['eng.1', 'esp.1', 'fra.1', 'ger.1', 'ita.1'],
    'window_matches': 10, 'min_history_matches': 3,
    'min_training_fixtures': 200, 'ridge_alpha': 0.1,
    'max_iter': 500, 'tol': 1e-9,
    'fit_schedule': 'calendar month; expanding window strictly before month first day',
    'grouping': 'exact (cohort, competition, stage); no phase alias collapse',
    'availability': 'RETROSPECTIVE_RECONSTRUCTION_NOT_ASOF_CERTIFIED',
    'selection': 'one fixed protocol; no target-period hyperparameter selection',
    'statistical_top1': 'joint independent-Poisson mode; not comprehensive Primary',
    'deployment': 'INACTIVE_RESEARCH_ONLY',
}
BASE_NAMES = ['is_home', 'own_prior_goals_for', 'own_prior_goals_against',
              'opponent_prior_goals_for', 'opponent_prior_goals_against']
XG_NAMES = ['own_prior_understat_xg', 'own_prior_understat_xga',
            'opponent_prior_understat_xg', 'opponent_prior_understat_xga']


def group_key(r):
    return (r['cohort'], r['competition'], r.get('stage'))


def valid_pair(a, b):
    return all(isinstance(v, (float, int)) and not isinstance(v, bool)
               and math.isfinite(v) and v >= 0 for v in (a, b))


def build_features(records, window=10, min_history=3):
    """All history updates occur after the complete date block is evaluated.

    Missing xG is never an observation of zero. Histories retain their latest
    source match IDs and dates, so stale coverage and exclusion can be inspected.
    """
    if window < min_history or min_history < 1:
        raise ValueError('window must be >= min_history >= 1')
    histories = defaultdict(lambda: {'goals': deque(maxlen=window), 'xg': deque(maxlen=window)})
    prepared, excluded = [], []
    seen = set()
    ordered = sorted(records, key=lambda r: (r['date'], r['match_id']))
    for date, block in groupby(ordered, key=lambda r: r['date']):
        block = list(block)
        for r in block:
            if r['match_id'] in seen:
                raise ValueError('duplicate fixture: ' + r['match_id'])
            seen.add(r['match_id'])
            if r['score_basis'] != 'regulation_90':
                excluded.append({'match_id': r['match_id'], 'reason': 'NOT_90_MINUTE'})
                continue
            if not valid_pair(r['home_goals_90m'], r['away_goals_90m']):
                excluded.append({'match_id': r['match_id'], 'reason': 'MISSING_90_MINUTE_LABEL'})
                continue
            teams = [histories[(group_key(r), r[k])] for k in ('home_team_id', 'away_team_id')]
            counts = [[len(t[k]) for k in ('goals', 'xg')] for t in teams]
            if min(min(c) for c in counts) < min_history:
                excluded.append({'match_id': r['match_id'], 'reason': 'INSUFFICIENT_PRIOR_HISTORY',
                                 'prior_goal_xg_counts_home_away': counts})
                continue
            means = [{k: np.mean([[v['for'], v['against']] for v in t[k]], axis=0).tolist()
                      for k in ('goals', 'xg')} for t in teams]
            home_base = [1., *means[0]['goals'], *means[1]['goals']]
            away_base = [0., *means[1]['goals'], *means[0]['goals']]
            home_xg = [*means[0]['xg'], *means[1]['xg']]
            away_xg = [*means[1]['xg'], *means[0]['xg']]
            dependencies = {}
            for side, t in zip(('home', 'away'), teams):
                dependencies[side] = {k: list(t[k]) for k in ('goals', 'xg')}
                assert all(v['date'] < date for k in t for v in t[k])
            prepared.append({
                'match_id': r['match_id'], 'date': date, 'month': date[:7],
                'cohort': r['cohort'], 'competition': r['competition'], 'stage': r.get('stage'),
                'season': r['season'], 'home_team_id': r['home_team_id'],
                'away_team_id': r['away_team_id'], 'home': r['home'], 'away': r['away'],
                'actual_score': [r['home_goals_90m'], r['away_goals_90m']],
                'base_x': [home_base, away_base],
                'plus_xg_x': [home_base + home_xg, away_base + away_xg],
                'dependencies': dependencies,
                'label_first_known_at': r.get('label_first_known_at'),
            })
        # Outcome and process values become *prior-date* history only here.
        for r in block:
            if r['score_basis'] != 'regulation_90' or not valid_pair(r['home_goals_90m'], r['away_goals_90m']):
                continue
            xg = r.get('features', {}).get('xg') or {}
            xg_ok = (xg.get('provider') == 'understat' and xg.get('date') == date
                     and valid_pair(xg.get('home_xg'), xg.get('away_xg'))
                     and xg.get('home_xga') == xg.get('away_xg')
                     and xg.get('away_xga') == xg.get('home_xg'))
            for side, other in (('home', 'away'), ('away', 'home')):
                h = histories[(group_key(r), r[side + '_team_id'])]
                h['goals'].append({'match_id': r['match_id'], 'date': date,
                                  'for': r[side + '_goals_90m'], 'against': r[other + '_goals_90m'],
                                  'label_first_known_at': r.get('label_first_known_at')})
                if xg_ok:
                    h['xg'].append({'match_id': r['match_id'], 'date': date,
                                    'for': xg[side + '_xg'], 'against': xg[other + '_xg'],
                                    'provider': 'understat', 'provider_match_id': xg.get('provider_match_id'),
                                    'source_payload_sha256': xg.get('source_payload_sha256'),
                                    'source_path': xg.get('source_path'),
                                    'observed_at': xg.get('source_fetched_at_beijing'),
                                    'first_known_at': xg.get('first_known_at')})
    return prepared, excluded


def fit_model(rows, feature_key):
    x = np.asarray([v for r in rows for v in r[feature_key]], dtype=float)
    y = np.asarray([v for r in rows for v in r['actual_score']], dtype=float)
    mean, scale = x.mean(axis=0), x.std(axis=0)
    scale[scale == 0] = 1.
    model = PoissonRegressor(alpha=PROTOCOL['ridge_alpha'], max_iter=PROTOCOL['max_iter'],
                             tol=PROTOCOL['tol'])
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always', ConvergenceWarning)
        model.fit((x - mean) / scale, y)
    if any(issubclass(w.category, ConvergenceWarning) for w in caught):
        raise RuntimeError('Poisson GLM did not converge')
    artifact = {'feature_names': BASE_NAMES + (XG_NAMES if feature_key == 'plus_xg_x' else []),
                'mean': mean.tolist(), 'scale': scale.tolist(),
                'coef': model.coef_.tolist(), 'intercept': float(model.intercept_),
                'n_iter': int(model.n_iter_), 'alpha': PROTOCOL['ridge_alpha']}
    return artifact


def model_predict(artifact, x):
    x = (np.asarray(x) - artifact['mean']) / artifact['scale']
    return np.exp(x @ np.asarray(artifact['coef']) + artifact['intercept']).tolist()


def score_prediction(lambdas, actual):
    h, a = lambdas
    # Infinite-support Skellam/Poisson CDFs avoid arbitrary matrix-tail clipping.
    wdl = [float(skellam.sf(0, h, a)), float(skellam.pmf(0, h, a)), float(skellam.cdf(-1, h, a))]
    top1 = [max(0, math.ceil(v) - 1) for v in lambdas]  # lower mode on exact-integer ties
    wdl_y = 0 if actual[0] > actual[1] else 1 if actual[0] == actual[1] else 2
    y = np.eye(3)[wdl_y]
    tail_p = float(poisson.sf(3, h + a))
    return {'lambda_home': h, 'lambda_away': a, 'statistical_top1': top1,
            'top1_hit': top1 == actual, 'wdl_probs': wdl,
            'wdl_pick': int(np.argmax(wdl)), 'wdl_actual': wdl_y,
            'wdl_hit': int(np.argmax(wdl)) == wdl_y,
            'score_log_loss': float(-sum(poisson.logpmf(actual, lambdas))),
            'wdl_log_loss': -math.log(max(wdl[wdl_y], 1e-300)),
            'wdl_brier': float(np.sum((np.asarray(wdl) - y) ** 2)),
            'wdl_rps': float(np.mean((np.cumsum(wdl)[:-1] - np.cumsum(y)[:-1]) ** 2)),
            'total_ge4_probability': tail_p, 'total_ge4_actual': sum(actual) >= 4,
            'total_ge4_brier': (tail_p - int(sum(actual) >= 4)) ** 2,
            'home_zero_probability': math.exp(-h), 'away_zero_probability': math.exp(-a),
            'zero_brier_mean': ((math.exp(-h) - int(actual[0] == 0)) ** 2
                                + (math.exp(-a) - int(actual[1] == 0)) ** 2) / 2,
            'probability_mass_error': abs(sum(wdl) - 1.)}


def paired_summary(predictions):
    if not predictions:
        return {'n': 0}
    result = {'n': len(predictions)}
    for key in ('baseline', 'plus_xg'):
        ms = [r[key] for r in predictions]
        result[key] = {'top1_hits': sum(r['top1_hit'] for r in ms),
                       'top1_rate': float(np.mean([r['top1_hit'] for r in ms])),
                       'wdl_hits': sum(r['wdl_hit'] for r in ms),
                       'wdl_rate': float(np.mean([r['wdl_hit'] for r in ms]))}
        for metric in ('score_log_loss', 'wdl_log_loss', 'wdl_brier', 'wdl_rps',
                       'total_ge4_brier', 'zero_brier_mean', 'total_ge4_probability'):
            result[key][metric] = float(np.mean([r[metric] for r in ms]))
        result[key]['predicted_total_mean'] = float(np.mean([r['lambda_home'] + r['lambda_away'] for r in ms]))
        result[key]['max_probability_mass_error'] = max(r['probability_mass_error'] for r in ms)
    gains = sum(r['plus_xg']['top1_hit'] and not r['baseline']['top1_hit'] for r in predictions)
    losses = sum(r['baseline']['top1_hit'] and not r['plus_xg']['top1_hit'] for r in predictions)
    result.update(top1_gains=gains, top1_losses=losses, top1_net=gains-losses,
                  top1_both_hit=sum(r['baseline']['top1_hit'] and r['plus_xg']['top1_hit'] for r in predictions),
                  actual_total_mean=float(np.mean([sum(r['actual_score']) for r in predictions])),
                  actual_total_ge4_rate=float(np.mean([sum(r['actual_score']) >= 4 for r in predictions])),
                  mcnemar_exact_p=float(binomtest(gains, gains+losses).pvalue) if gains+losses else 1.)
    # Fixed seed, cohort-month cluster bootstrap: descriptive uncertainty only.
    blocks = defaultdict(list)
    for r in predictions:
        blocks[(r['cohort'], r['stage'], r['month'])].append(
            int(r['plus_xg']['top1_hit']) - int(r['baseline']['top1_hit']))
    sums = np.asarray([sum(v) for v in blocks.values()])
    ns = np.asarray([len(v) for v in blocks.values()])
    rng = np.random.default_rng(20261104)
    indices = rng.integers(0, len(ns), size=(2000, len(ns)))
    rates = sums[indices].sum(axis=1) / ns[indices].sum(axis=1)
    result['top1_delta_cluster_bootstrap_ci95'] = np.quantile(rates, [.025, .975]).tolist()
    result['top1_delta_rate'] = (gains-losses)/len(predictions)
    result['probability_constraints'] = {
        'plus_xg_minus_baseline': {metric: result['plus_xg'][metric] - result['baseline'][metric]
                                  for metric in ('score_log_loss', 'wdl_log_loss', 'wdl_brier', 'wdl_rps',
                                                 'total_ge4_brier', 'zero_brier_mean')},
        'meaning': 'lower is better; no post-hoc tolerances or promotion decision'}
    return result


def market_gate(records):
    """Capture/closing timestamps never substitute for an actual quote time.

    This run only receives certified joined market rows. Unknown precision and
    non-prematch quotes fail closed; it does not attempt fixture inference.
    """
    usable, reasons = [], Counter()
    for r in records:
        m = r.get('market_features')
        if not m:
            reasons['NO_CERTIFIED_MARKET_JOIN'] += 1
            continue
        if not m.get('quote_time'):
            reasons['MISSING_QUOTE_TIME_CAPTURE_NOT_SUBSTITUTE'] += 1
            continue
        required = ['bookmaker', 'market', 'line', 'over_odds', 'under_odds']
        if any(m.get(k) is None for k in required) or not r.get('kickoff_at'):
            reasons['MISSING_MARKET_IDENTITY_LINE_SIDES_OR_KICKOFF'] += 1
            continue
        try:
            qt = datetime.fromisoformat(m['quote_time'].replace('Z', '+00:00'))
            ko = datetime.fromisoformat(r['kickoff_at'].replace('Z', '+00:00'))
            if qt.tzinfo is None or ko.tzinfo is None or qt >= ko:
                raise ValueError('not certified pre-kickoff')
        except (ValueError, TypeError):
            reasons['INVALID_OR_NON_PREMATCH_QUOTE_TIME'] += 1
            continue
        usable.append(r['match_id'])
    return {'status': 'BLOCKED' if not usable else 'EVIDENCE_GATE_ONLY',
            'input_fixtures': len(records), 'certified_prematch_quote_fixtures': len(usable),
            'training_fixtures': 0, 'ablation_predictions': 0,
            'reasons': dict(reasons), 'usable_fixture_ids': usable,
            'reason': 'No historical market model trained; never use capture time or closing label as quote timestamp.'}


def run(records, out_dir, input_sha256, source_metadata):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    # Existing experiment outputs are immutable; a new invocation needs a new path.
    if (out / 'protocol.json').exists():
        raise FileExistsError('Experiment path already contains protocol.json')
    protocol = dict(PROTOCOL, input_sha256=input_sha256, source_metadata=source_metadata,
                    created_at_beijing=datetime.now(timezone(timedelta(hours=8))).isoformat())
    (out / 'protocol.json').write_text(json.dumps(protocol, indent=2, ensure_ascii=False)+'\n')
    selected = [r for r in records if r['competition'] in PROTOCOL['competitions']]
    prepared, excluded = build_features(selected, PROTOCOL['window_matches'], PROTOCOL['min_history_matches'])
    predictions, fits, skipped = [], [], []
    groups = defaultdict(list)
    for r in prepared:
        groups[group_key(r)].append(r)
    with (out / 'features.jsonl').open('w') as f:
        for r in prepared:
            f.write(json.dumps(r, ensure_ascii=False)+'\n')
    for key, rows in sorted(groups.items(), key=lambda kv: str(kv[0])):
        for month in sorted({r['month'] for r in rows}):
            cutoff = month + '-01'
            train = [r for r in rows if r['date'] < cutoff]
            targets = [r for r in rows if r['month'] == month]
            if len(train) < PROTOCOL['min_training_fixtures']:
                skipped.extend({'match_id': r['match_id'], 'reason': 'INSUFFICIENT_MONTH_START_TRAIN',
                                'training_fixtures': len(train)} for r in targets)
                continue
            model_id = ':'.join(str(v) for v in key) + ':' + month
            artifacts = {name: fit_model(train, fk) for name, fk in [('baseline', 'base_x'), ('plus_xg', 'plus_xg_x')]}
            fits.append({'model_id': model_id, 'group': key, 'month': month,
                         'training_cutoff_exclusive': cutoff, 'training_max_fixture_date': max(r['date'] for r in train),
                         'training_fixtures': len(train), 'training_match_ids_sha256': sha256('\n'.join(r['match_id'] for r in train).encode()).hexdigest(),
                         'training_match_ids': [r['match_id'] for r in train], 'models': artifacts})
            for r in targets:
                pred = {k: v for k, v in r.items() if k not in ('base_x', 'plus_xg_x', 'dependencies')}
                pred['model_id'] = model_id
                pred['feature_match_id'] = r['match_id']
                pred['max_dependency_date'] = max(v['date'] for side in r['dependencies'].values() for vs in side.values() for v in vs)
                for name, fk in [('baseline', 'base_x'), ('plus_xg', 'plus_xg_x')]:
                    pred[name] = score_prediction(model_predict(artifacts[name], r[fk]), r['actual_score'])
                predictions.append(pred)
        print(json.dumps({'completed_group': key, 'prepared_fixtures': len(rows), 'predictions_so_far': len(predictions)}), flush=True)
    predictions.sort(key=lambda r: (r['date'], r['match_id']))
    for name, values in [('predictions', predictions), ('fits', fits), ('excluded', excluded+skipped)]:
        with (out / (name + '.jsonl')).open('w') as f:
            for v in values:
                f.write(json.dumps(v, ensure_ascii=False)+'\n')
    gate = market_gate(records)
    (out / 'market-gate.json').write_text(json.dumps(gate, indent=2)+'\n')
    summary = {'protocol': PROTOCOL['version'], 'status': 'COMPLETED_INACTIVE_RETROSPECTIVE_RESEARCH',
               'coverage': {'all_input_fixtures': len(records), 'selected_competition_fixtures': len(selected),
                            'same_prior_feature_eligible_fixtures': len(prepared),
                            'predicted_paired_fixtures': len(predictions), 'monthly_pair_fits': len(fits),
                            'excluded_before_feature_eligibility': len(excluded), 'excluded_initial_training_window': len(skipped),
                            'historically_asof_certified_predictions': 0,
                            'predicted_fraction_of_selected': len(predictions)/len(selected) if selected else 0,
                            'excluded_reasons': dict(Counter(r['reason'] for r in excluded+skipped))},
               'overall': paired_summary(predictions),
               'by_group': {':'.join(str(v) for v in key): paired_summary([r for r in predictions if group_key(r) == key])
                            for key in sorted(groups, key=str)},
               'by_year': {y: paired_summary([r for r in predictions if r['date'].startswith(y)])
                           for y in sorted({r['date'][:4] for r in predictions})},
               'market': gate,
               'limits': ['No historical first-known timestamps; not an as-of backtest or future validation.',
                          'Statistical Top1 is not comprehensive human Primary and does not measure active Current/Test.',
                          'Single fixed protocol; no candidate selection, promotion, deployment, or active artifact modification.',
                          'No lineup/injury feature or synthetic xG/market imputation.',
                          'Missing xG observations remain missing; latest 10 observed prior matches may be stale; dependency dates retained.',
                          'Exact phase/identity boundaries intentionally reduce coverage.',
                          'Repeated historical development data cannot prove future 70%/80% goals.']}
    (out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
    (out / 'manifest.json').write_text(json.dumps({p.name: sha256(p.read_bytes()).hexdigest() for p in sorted(out.iterdir()) if p.is_file()}, indent=2)+'\n')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--records', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    raw = Path(args.records).read_bytes()
    dataset = json.loads(raw)
    summary = run(dataset['records'], args.out, sha256(raw).hexdigest(),
                  {k: dataset[k] for k in ('schema_version', 'source_database_sha256', 'counts')})
    print(json.dumps({'coverage': summary['coverage'], 'overall': summary['overall']}, indent=2))


if __name__ == '__main__':
    main()
