"""Restore stored logistic inference and transparent IPF, strictly Shadow-only."""
import argparse
import json
import math
import sqlite3
from pathlib import Path

from wcpp import pmf, sha, write

MODEL = 'WCPP_REGIME_CONDITIONAL_MARGIN_TOTAL_0.1_SHADOW'


def logistic(lh, la, competition, model):
    features = [lh, la, lh+la, lh-la, abs(lh-la), math.log(lh/la),
                (lh+la)**2, abs(lh-la)**2, lh*la]
    if len(model['numeric_scaler_mean']) != len(features):
        raise ValueError('特征定义不符')
    x = [(v-m)/s for v, m, s in zip(features, model['numeric_scaler_mean'], model['numeric_scaler_scale'])]
    categories = model['categorical_categories'][0]
    if competition not in categories:
        raise ValueError('现有Shadow仅支持训练类别，禁止跨赛事族套用')
    x += [float(competition == value) for value in categories]
    logits = [sum(a*b for a, b in zip(row, x)) + bias for row, bias in zip(model['coef'], model['intercept'])]
    weights = [math.exp(v-max(logits)) for v in logits]
    return {cls: w/sum(weights) for cls, w in zip(model['classes'], weights)}


def regime(i, j):
    return 'LOW' if i+j <= 1 else 'CENTER' if i+j <= 3 else 'HIGH'


def margin(i, j):
    d = i-j
    return 'D0' if d == 0 else ('H' if d > 0 else 'A') + (str(abs(d)) if abs(d) < 3 else '3P')


def project(lh, la, totals, margins, iterations=500, tolerance=1e-10):
    h, a = pmf(lh), pmf(la)
    q = [h[i]*a[j] for i in range(8) for j in range(8)]
    mass = sum(q); q = [v/mass for v in q]
    rt = [regime(i, j) for i in range(8) for j in range(8)]
    rm = [margin(i, j) for i in range(8) for j in range(8)]
    residual = float('inf')
    for iteration in range(iterations):
        for labels, target in [(rt, totals), (rm, margins)]:
            current = {label: math.fsum(v for v, lab in zip(q, labels) if lab == label) for label in target}
            q = [v*target[label]/current[label] for v, label in zip(q, labels)]
        residual = max(abs(math.fsum(v for v, lab in zip(q, labels) if lab == label)-target[label])
                       for labels, target in [(rt, totals), (rm, margins)] for label in target)
        if residual < tolerance:
            break
    ranked = sorted(range(64), key=lambda k: (-q[k], k))
    return {'role': 'SHADOW_ONLY', 'promotion': 'DO_NOT_PROMOTE',
            'conditional_matrix_0_to_7': [q[i*8:(i+1)*8] for i in range(8)],
            'matrix_hash_sha256': sha(q), 'shadow_primary': [ranked[0]//8, ranked[0]%8],
            'converged': residual < tolerance, 'iterations': iteration+1,
            'marginal_max_error': residual, 'raw_64_mass': mass,
            'limitation': '仅64格条件分布，未重校准8+尾部；不可替代正式Primary'}


def replay(database):
    c = sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro', uri=True)
    c.execute('PRAGMA query_only=ON')
    parameters = json.loads(c.execute('SELECT parameters_json FROM model_versions WHERE version_id=?', (MODEL,)).fetchone()[0])['development_prototype']
    results = {'rows': 0, 'logistic_max_difference': 0., 'converged': 0,
               'shadow_primary_matches_stored': 0, 'new_shadow_hits': 0, 'stored_shadow_hits': 0}
    for raw, in c.execute('SELECT payload_json FROM regime_shadow_replay_v1'):
        p = json.loads(raw); lh, la = p['lambda']
        t = logistic(lh, la, p['competition'], parameters['regime_logistic'])
        m = logistic(lh, la, p['competition'], parameters['margin_logistic'])
        results['logistic_max_difference'] = max(results['logistic_max_difference'],
             max(abs(t[k]-p['pred_regime'][k]) for k in t), max(abs(m[k]-p['pred_margin'][k]) for k in m))
        q = project(lh, la, t, m)
        original = p['branches']['D_TOTAL_MARGIN']['primary']
        results['rows'] += 1
        results['converged'] += q['converged']
        results['shadow_primary_matches_stored'] += q['shadow_primary'] == original
        results['new_shadow_hits'] += q['shadow_primary'] == p['actual']
        results['stored_shadow_hits'] += original == p['actual']
    c.close()
    return {'model': MODEL, 'status': 'SHADOW_ONLY_DO_NOT_PROMOTE',
            'scope': 'historical development replay; no fitting or promotion',
            'results': results, 'projection_spec': {'iterations': 500, 'tolerance': 1e-10,
            'grid': '8x8 conditional distribution', 'tail': 'not calibrated'},
            'note': '参数推理和Primary对照可实测；投影实现仍为本云端实现，不宣称逐格复原旧算法'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Regime-MT-KL Shadow restoration check')
    parser.add_argument('database'); parser.add_argument('--out', required=True)
    args = parser.parse_args()
    write(args.out, replay(args.database))
    print('HELLO BOSS — Shadow核查完成，不晋级')
