"""Reconstruct DC/ML from genuine first freezes; lock future candidates separately.

Historical reconstruction is not a new first forecast. The future subset alone
can receive a new candidate freeze, before kickoff, with the old input timestamp
kept explicit. No labels are read and no alpha is selected by this program.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
from v6 import ml_model as ml
from research.analysis_freeze import verify, save

ALPHAS = [0, .25, .5, .75, 1]


def write(path, value):
    with Path(path).open('x') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)


def metrics(matrix):
    score = list(map(int, np.unravel_index(np.argmax(matrix), matrix.shape)))
    return {'statistical_top1': score, 'top1_probability': float(matrix.max()),
            'wdl_probabilities': list(map(float, ml.legacy.probabilities(matrix))),
            'matrix_sha256': ml.legacy.digest(matrix.tolist())}


def run(registry_path, freeze_root, out):
    out = Path(out); out.mkdir(parents=True, exist_ok=False)
    registry = json.loads(Path(registry_path).read_text()); artifacts = {}
    for route in registry['models'].values():
        path = Path(route['test_artifact_path']); body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() != route['test_artifact_sha256']:
            raise ValueError('registered artifact identity changed')
        a = json.loads(body)
        if a.get('version') == ml.VERSION:
            artifacts[ml.legacy.digest(a)] = (a, path, hashlib.sha256(body).hexdigest())
    now = datetime.now(timezone.utc); excluded = Counter(); candidates = []
    for path in sorted(Path(freeze_root).glob('*/000001.json')):
        record = json.loads(path.read_text())
        if record.get('stage') != 'prematch' or record.get('track') != 'current-test-pair':
            continue
        verify(record)
        fixture = record['fixture']; test = record['payload'].get('test_model', {})
        if test.get('model_hash') not in artifacts or not test.get('ml', {}).get('feature_values'):
            excluded['no_supported_retained_ml_artifact'] += 1; continue
        if record.get('evaluation_slot') != 'FIRST_PREMATCH':
            excluded['not_first_prematch'] += 1; continue
        cutoff = ml.legacy.aware(fixture['cutoff_at']); kickoff = ml.legacy.aware(fixture['kickoff_at'])
        a, artifact_path, artifact_sha = artifacts[test['model_hash']]
        if (not cutoff <= ml.legacy.aware(record['recorded_at']) < kickoff
                or max(ml.legacy.aware(a[k]) for k in ('built_at', 'observed_at')) > cutoff
                or a['train_end'] >= kickoff.date().isoformat()):
            excluded['not_time_out_of_training'] += 1; continue
        candidates.append((path, record, a, artifact_path, artifact_sha))
    protocol = {'schema': 'WCPP-FROZEN-ALPHA-GRID-1', 'created_at': now.isoformat(),
        'alphas': ALPHAS, 'alpha_zero': 'pure DC from test artifact, not necessarily Current',
        'selection': 'NONE; existing outcomes not used to select weights',
        'historical_status': 'retained first forecasts reconstructed; not new unseen validation',
        'future_rule': 'all supported retained FIRST_PREMATCH pairs with kickoff after protocol creation',
        'prediction_inputs': 'exact original freeze input, never refreshed retrospectively',
        'candidate_node': 'actual candidate freeze time; original input cutoff retained',
        'promotion': False, 'registry_sha256': hashlib.sha256(Path(registry_path).read_bytes()).hexdigest(),
        'population': [{'match_id': r['match_id'], 'freeze_file': str(p), 'record_sha256': r['record_sha256'],
                        'kickoff_at': r['fixture']['kickoff_at'], 'future': ml.legacy.aware(r['fixture']['kickoff_at']) > now}
                       for p, r, a, ap, ash in candidates], 'excluded': dict(excluded)}
    write(out / 'protocol.json', protocol)
    rows = []; booster_cache = {}
    for path, record, artifact, artifact_path, artifact_sha in candidates:
        fixture = record['fixture']; test = record['payload']['test_model']
        rates = ml.legacy.rates(artifact['core']['long'], fixture)[:2]
        features = np.array([[np.nan if v is None else v for v in row] for row in test['ml']['feature_values']])
        regenerated = ml.feature_matrix(artifact['history'], fixture, rates)
        if not np.allclose(features, regenerated, rtol=0, atol=1e-12, equal_nan=True):
            excluded['feature_reconstruction_mismatch'] += 1; continue
        if artifact_sha not in booster_cache:
            booster_cache[artifact_sha] = ml.lgb.Booster(model_str=artifact['booster_model'])
        learned, delta = ml.ml_matrix(booster_cache[artifact_sha], features, rates,
                                      artifact['core']['long']['nuisance']['dixon_coles'])
        dc, _ = ml.legacy.distribution(artifact['core'], None, artifact['history'], fixture, 'dc365')
        original = (1-artifact['alpha'])*dc + artifact['alpha']*learned
        error = float(np.max(np.abs(original - np.array(test['matrix']))))
        if error > 1e-12:
            excluded['frozen_matrix_reconstruction_mismatch'] += 1; continue
        row = {'match_id': record['match_id'], 'cohort': fixture['cohort'],
               'kickoff_at': fixture['kickoff_at'], 'input_cutoff_at': fixture['cutoff_at'],
               'first_freeze_id': record['freeze_id'], 'first_freeze_sha256': record['record_sha256'],
               'artifact_path': str(artifact_path), 'artifact_sha256': artifact_sha,
               'frozen_matrix_max_error': error, 'original_alpha': artifact['alpha'],
               'current': metrics(np.array(record['payload']['current_model']['matrix'])),
               'test': metrics(original),
               'alphas': {str(a): metrics((1-a)*dc + a*learned) for a in ALPHAS},
               'components': {'dc': dc.tolist(), 'ml': learned.tolist()},
               'future': ml.legacy.aware(fixture['kickoff_at']) > now}
        if row['future']:
            current_time = datetime.now(timezone.utc)
            if current_time >= ml.legacy.aware(fixture['kickoff_at']):
                excluded['kickoff_passed_during_execution'] += 1; continue
            target = dict(fixture, cutoff_at=current_time.isoformat())
            row['candidate_freeze'] = save(row, target, stage='prematch', track='alpha-grid-candidate',
                                          root=freeze_root, idempotency_key='post32-alpha-' + record['freeze_id'])
        rows.append(row)
    with (out / 'predictions.jsonl').open('x') as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
    summary = {'eligible_population': len(candidates), 'reconstructed': len(rows),
               'future_candidates_frozen': sum(r['future'] for r in rows),
               'historical_reconstructed': sum(not r['future'] for r in rows),
               'cohorts': dict(Counter(r['cohort'] for r in rows)), 'excluded': dict(excluded),
               'labels_read': 0, 'alpha_selected': None, 'active_modified': False,
               'unsettled_future': sum(r['future'] for r in rows)}
    write(out / 'summary.json', summary); return summary


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--registry', required=True); p.add_argument('--freeze-root', required=True)
    p.add_argument('--out', required=True); a = p.parse_args()
    print(json.dumps(run(a.registry, a.freeze_root, a.out), ensure_ascii=False))
