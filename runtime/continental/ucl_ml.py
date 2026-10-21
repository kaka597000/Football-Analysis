"""LightGBM Poisson residuals on strictly prior match features, research only."""
import copy
import hashlib
import json
import math
import sys
from pathlib import Path
from datetime import datetime, timezone

import lightgbm as lgb
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'v5'))
from weighted_model import legacy, chronological_split

VERSION = 'WCPP-ML-UCL-0.1'
SIDE_FEATURES = ['recent_scored', 'recent_conceded', 'recent_ppg', 'scored90d',
                 'conceded90d', 'venue_scored', 'venue_conceded', 'same_cohort_gap',
                 'recent_btts', 'recent_over25', 'recent_xg', 'recent_xga', 'xg_coverage',
                 'recent_shots_on_target', 'recent_shots_off_target', 'recent_possession', 'process_coverage']
FEATURES = ['dc_log_rate', 'opponent_dc_log_rate', 'is_home', 'neutral'] + [
    prefix + k for prefix in ('own_', 'opponent_') for k in SIDE_FEATURES]
CONFIG = {'warmup_matches': 60, 'num_boost_round': 70, 'residual_cap': .4,
          'learning_rate': .03, 'num_leaves': 7, 'min_data_in_leaf': 25,
          'lambda_l2': 5., 'blend_candidates': [.25, .5, .75, 1.],
          'objective': 'poisson', 'seed': 20261012}


def code_hash():
    return legacy.digest({'ml': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                          'legacy': legacy.code_hash(), 'lightgbm': lgb.__version__})


def xg_values(row, home):
    evidence = row.get('xg_evidence')
    if not evidence: return None
    if not legacy.url(evidence.get('source_url')) or not evidence.get('query_time'):
        raise ValueError('xG provenance missing')
    if legacy.aware(evidence['query_time']) > legacy.aware(row['observed_at']):
        raise ValueError('xG observed after row audit')
    values = [row.get(k) for k in ('home_xg', 'away_xg')]
    if any(type(x) not in (int, float) or not math.isfinite(x) or not 0 <= x <= 30 for x in values):
        raise ValueError('invalid historical xG')
    return values if home else values[::-1]


def side_features(past, team, date, venue):
    games = [r for r in past if team in (r['home'], r['away'])]
    recent = games[-6:]
    season = [r for r in games if (datetime.fromisoformat(date)-datetime.fromisoformat(r['date'])).days <= 90]
    vg = [r for r in games if (r['home'] == team) == venue][-6:]
    def pair(r):
        return (r['home_goals'], r['away_goals']) if r['home'] == team else (r['away_goals'], r['home_goals'])
    def avg(block, side): return (sum(pair(r)[side] for r in block) + 4 * 1.3)/(len(block)+4)
    pts = sum(3 if pair(r)[0] > pair(r)[1] else 1 if pair(r)[0] == pair(r)[1] else 0 for r in recent)
    gap = min(21, (datetime.fromisoformat(date)-datetime.fromisoformat(games[-1]['date'])).days) if games else 21
    xgs = [xg_values(r, r['home'] == team) for r in recent]
    xgs = [v for v in xgs if v is not None]
    process = []
    for r in recent:
        if not r.get('process_evidence'): continue
        evidence = r['process_evidence']
        if not legacy.url(evidence.get('source_url')) or legacy.aware(evidence['query_time']) > legacy.aware(r['observed_at']):
            raise ValueError('invalid historical process provenance')
        values = r.get('process',{}).get('home' if r['home']==team else 'away',{})
        v = [values.get(k) for k in ('shots_on_target','shots_off_target','possession')]
        if any(x is not None and (type(x) not in (int,float) or not math.isfinite(x) or x < 0 or x > 100) for x in v):
            raise ValueError('invalid historical process values')
        if any(x is not None for x in v):process.append([float(x) if x is not None else float('nan') for x in v])
    return [avg(recent, 0), avg(recent, 1), (pts+4*1.3)/(len(recent)+4),
            avg(season, 0), avg(season, 1), avg(vg, 0), avg(vg, 1), gap,
            sum(all(v > 0 for v in pair(r)) for r in recent)/max(1,len(recent)),
            sum(sum(pair(r)) > 2.5 for r in recent)/max(1,len(recent)),
            float(np.mean([v[0] for v in xgs])) if xgs else float('nan'),
            float(np.mean([v[1] for v in xgs])) if xgs else float('nan'),
            len(xgs)/max(1,len(recent))] + [
            float(np.mean([v[k] for v in process if math.isfinite(v[k])])) if any(math.isfinite(v[k]) for v in process) else float('nan') for k in range(3)] + [
            sum(math.isfinite(x) for v in process for x in v)/(3*max(1,len(recent)))]


def feature_matrix(rows, fixture, rates):
    date = fixture.get('date') or legacy.aware(fixture['kickoff_at']).date().isoformat()
    past = [r for r in rows if r['date'] < date]
    if len(past) < CONFIG['warmup_matches']: raise ValueError('insufficient strictly prior feature history')
    h = side_features(past, fixture['home'], date, True)
    a = side_features(past, fixture['away'], date, False)
    neutral = int(fixture['neutral'])
    return np.asarray([[math.log(rates[0]), math.log(rates[1]), 1-neutral, neutral] + h + a,
                       [math.log(rates[1]), math.log(rates[0]), 0, neutral] + a + h])


def rolling_items(rows, targets):
    items, folds = [], []
    for month in sorted({r['date'][:7] for r in targets}):
        block = sorted([r for r in targets if r['date'][:7] == month], key=lambda r:(r['date'],r['match_id']))
        first = block[0]['date']; prior = [r for r in rows if r['date'] < first]
        if len(prior) < CONFIG['warmup_matches']: continue
        core = legacy.fit_core(prior)
        folds.append({'fit_rows': len(prior), 'fit_end': prior[-1]['date'], 'first_target': first})
        for r in block:
            rates = legacy.rates(core['long'], r)[:2]
            m, meta = legacy.distribution(core, None, prior, r, 'dc365')
            items.append({'row': r, 'features': feature_matrix(rows, r, rates),
                          'rates': rates, 'matrix': m, 'meta': meta,
                          'rho': core['long']['nuisance']['dixon_coles']})
    return items, folds


def fit_booster(items):
    if len(items) < 30: raise ValueError('insufficient real feature targets')
    features = np.concatenate([it['features'] for it in items])
    goals = np.array([it['row'][k] for it in items for k in ('home_goals','away_goals')])
    offsets = np.log(np.array([v for it in items for v in it['rates']]))
    params = {k: CONFIG[k] for k in ('objective', 'seed', 'num_leaves', 'min_data_in_leaf', 'learning_rate', 'lambda_l2')}
    params.update(verbosity=-1, num_threads=1, deterministic=True, force_col_wise=True)
    ds = lgb.Dataset(features, label=goals, init_score=offsets, feature_name=FEATURES)
    booster = lgb.train(params, ds, num_boost_round=CONFIG['num_boost_round'])
    return booster


def ml_matrix(booster, features, rates, rho):
    delta = np.clip(booster.predict(features, raw_score=True, num_threads=1),
                    -CONFIG['residual_cap'], CONFIG['residual_cap'])
    adjusted = np.clip(np.array(rates)*np.exp(delta), .05, 12.)
    return legacy.matrix(float(adjusted[0]), float(adjusted[1]), 'dixon_coles', rho), delta


def evaluate(items, booster, alpha):
    predictions = []
    for it in items:
        ml, _ = ml_matrix(booster, it['features'], it['rates'], it['rho'])
        m = (1-alpha)*it['matrix'] + alpha*ml
        predictions.append((it['row'],m,it['meta']))
    return legacy.score_matrices(predictions)


def train(rows):
    rows = legacy.history(rows)
    initial, calibration, test = chronological_split(rows)
    first_items, first_folds = rolling_items(rows, initial)
    calibration_items, cf = rolling_items(rows, calibration)
    test_items, tf = rolling_items(rows, test)
    if len(calibration_items) != len(calibration) or len(test_items) != len(test):
        raise ValueError('evaluation must retain complete denominators')
    booster = fit_booster(first_items)
    scores = {str(alpha): evaluate(calibration_items, booster, alpha) for alpha in CONFIG['blend_candidates']}
    alpha = max(CONFIG['blend_candidates'], key=lambda v:(scores[str(v)]['top1'],-scores[str(v)]['score_log_loss']))
    # The final test does not select alpha, hyperparameters, or fusion weights.
    test_booster = fit_booster(first_items + calibration_items)
    candidate = evaluate(test_items, test_booster, alpha)
    dc = legacy.score_matrices([(it['row'],it['matrix'],it['meta']) for it in test_items])
    baseline_items = []
    for month in sorted({r['date'][:7] for r in test}):
        block = [r for r in test if r['date'][:7]==month]
        prior = [r for r in rows if r['date'] < min(x['date'] for x in block)]
        core = legacy.fit_core(prior)
        for r in block:
            m, meta = legacy.distribution(core,None,prior,r,'baseline'); baseline_items.append((r,m,meta))
    baseline = legacy.score_matrices(baseline_items)
    report = {'version':VERSION, 'mode':'INSPECTED_HISTORY_DEVELOPMENT_DIAGNOSTIC',
              'formal_validation':False, 'selected_alpha':alpha, 'selection':scores,
              'test':{'ml':candidate,'dc':dc,'current_baseline':baseline},
              'acceptance':legacy.comparison(candidate,baseline),
              'feature_names':FEATURES, 'train_feature_rows':len(first_items)*2,
              'feature_xg_match_count':sum(bool(r.get('xg_evidence')) for r in rows),
              'feature_process_match_count':sum(bool(r.get('process_evidence')) for r in rows),
              'folds':{'fit':first_folds,'selection':cf,'test':tf}, 'config':CONFIG,
              'limitations':['Not independent prospective validation',
                             'xG coefficients not trained when source coverage is absent',
                             'Same-league gap is not all-competition rest or travel',
                             'No fitted lineup, injury, incentive, integrity or market coefficients',
                             'UCL process statistics independently missing; SOT/possession are not off-target shots',
                             'Fixed-2.5 diagnostics are not actual-market OU accuracy']}
    final_booster = fit_booster(first_items + calibration_items + test_items)
    built = datetime.now(timezone.utc).isoformat()
    artifact = {'version':VERSION,'cohort':rows[0]['cohort'], 'history':rows,
                'competition_route':legacy.validate_training(rows),
                'core':legacy.fit_core(rows), 'booster_model':final_booster.model_to_string(),
                'alpha':alpha, 'config':CONFIG, 'feature_names':FEATURES,
                'feature_importance_gain':dict(zip(FEATURES,final_booster.feature_importance('gain').tolist())),
                'code_hash':code_hash(),'report_hash':legacy.digest(report), 'built_at':built,
                'observed_at':max(r['observed_at'] for r in rows), 'train_end':rows[-1]['date'],
                'acceptance':report['acceptance'], 'formal_validation':False,
                'formal_kpi_eligible':False,'feature_xg_match_count':report['feature_xg_match_count']}
    artifact['feature_process_match_count'] = report['feature_process_match_count']
    return artifact, report


def predict(artifact, current, fixture):
    if artifact.get('version') != VERSION or artifact.get('code_hash') != code_hash() or artifact['config'] != CONFIG:
        raise ValueError('ML artifact/code/config mismatch')
    route = legacy.validate_training(legacy.history(artifact['history']))
    if route != legacy.classify(fixture):
        # classify adds per-fixture reasons; route_key is the stable identity.
        if route['route_key'] != legacy.classify(fixture)['route_key']:
            raise ValueError('ML competition route mismatch')
    cutoff = legacy.aware(fixture['cutoff_at'])
    now = datetime.now(timezone.utc)
    kickoff = legacy.aware(fixture['kickoff_at'])
    if current['input'] != fixture or cutoff > now or cutoff >= kickoff or kickoff <= now:
        raise ValueError('paired genuine future forecast required')
    if max(legacy.aware(artifact[k]) for k in ('built_at','observed_at')) > cutoff:
        raise ValueError('ML artifact later than forecast cutoff')
    if artifact['train_end'] >= legacy.aware(fixture['kickoff_at']).date().isoformat():
        raise ValueError('ML training reaches forecast date')
    # current is produced by legacy.predict in dispatcher, so identity, actual
    # line, source times, venue, and genuinely future kickoff are already checked.
    rates = legacy.rates(artifact['core']['long'], fixture)[:2]
    features = feature_matrix(artifact['history'],fixture,rates)
    booster = lgb.Booster(model_str=artifact['booster_model'])
    ml, delta = ml_matrix(booster,features,rates,artifact['core']['long']['nuisance']['dixon_coles'])
    dc, meta = legacy.distribution(artifact['core'],None,artifact['history'],fixture,'dc365')
    m = (1-artifact['alpha'])*dc + artifact['alpha']*ml
    output = copy.deepcopy(current)
    output.update(version=VERSION,model_label='test模型',role='ML_INDEPENDENT_RESEARCH',
                  generated_at=datetime.now(timezone.utc).isoformat(), model_hash=legacy.digest(artifact),
                  code_hash=code_hash(),model_built_at=artifact['built_at'],model_observed_at=artifact['observed_at'],
                  recipe='dc_lightgbm_poisson_residual',primary_basis='TEMPORAL_MACHINE_LEARNING',
                  primary_label='test模型机器学习研究比分；未通过项另列',
                  matrix=m.tolist(),matrix_hash=legacy.digest(m.tolist()),
                  primary='-'.join(map(str,np.unravel_index(np.argmax(m),m.shape))),
                  primary_probability=float(np.max(m)),
                  wdl=dict(zip(('主胜','平局','客胜'),map(float,legacy.probabilities(m)))),
                  rates={'means':[float(np.arange(51)@m.sum(axis=1)),float(np.arange(51)@m.sum(axis=0))]},
                  ml={'algorithm':'LightGBM Poisson residual + DC score matrix',
                      'selected_alpha':artifact['alpha'],'log_rate_residual':delta.tolist(),
                      'feature_count':len(FEATURES),'xg_training_match_count':artifact['feature_xg_match_count'],
                      'process_training_match_count':artifact['feature_process_match_count'],
                      'acceptance':artifact['acceptance'],
                      'feature_values':[[None if math.isnan(v) else float(v) for v in row] for row in features]},
                  context_numeric_applied=False, market_numeric_applied=False,
                  comprehensive_numeric_model=False, formal_kpi_eligible=False)
    ranked = np.argsort(-m.ravel(),kind='stable')[:3]
    output['top3'] = [{'score':f'{i//51}-{i%51}','probability':float(m.ravel()[i])} for i in ranked]
    output['acceptance'] = artifact['acceptance']
    if not artifact['acceptance']['acceptance_gate_passed']:
        output['reasons'].append('ML research candidate has not passed acceptance gate')
    output['wdl_reference'] = max(output['wdl'],key=output['wdl'].get)
    if output['total']:
        total = output['total']; line = total['quote']['line']
        total['over'] = legacy.total_settlement(m,line,'over')
        total['under'] = legacy.total_settlement(m,line,'under')
        value = lambda p:p['win']+.5*p['half_win']-.5*p['half_loss']-p['loss']
        total['reference_direction'] = 'over' if value(total['over']) > value(total['under']) else 'under'
    return output
