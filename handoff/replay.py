"""Offline historical replay; never records a new forecast or changes artifacts."""
import argparse
import contextlib
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def clock(timestamp):
    point = dt.datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    class HistoricalClock(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return point.astimezone(tz) if tz else point.replace(tzinfo=None)
    return HistoricalClock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    root, out = args.bundle.resolve(), args.out.resolve()
    if out.exists():
        raise FileExistsError('Replay output must be a new directory')
    manifest = json.loads((root/'manifest.json').read_text())
    checked = 0
    for entry in manifest['files']:
        target = (root/entry['bundle_path']).resolve()
        if not target.is_relative_to(root) or target.stat().st_size != entry['bytes'] or sha(target) != entry['sha256']:
            raise ValueError('Bundle identity mismatch: '+entry['bundle_path'])
        checked += 1
    print('FILE_HASHES_VERIFIED', checked, flush=True)
    case = manifest['case']
    expected = json.loads((root/case['pair']).read_text())
    fixture = json.loads((root/case['fixture']).read_text())
    assert fixture == expected['current_model']['input'] == expected['test_model']['input']
    original_registry = json.loads((root/case['registry']).read_text())
    route = json.loads(json.dumps(original_registry['models'][fixture['cohort']]))
    mappings = json.loads((root/'path-map.json').read_text())['original_to_bundle']
    for key in ('artifact_path', 'test_artifact_path', 'report_path', 'test_report_path'):
        if key in route:
            if route[key] not in mappings:
                raise ValueError('Missing selected-route dependency: '+key)
            route[key] = str(root/mappings[route[key]])
    adapted = dict(original_registry, models={fixture['cohort']:route})
    out.mkdir(parents=True)
    write(out/'registry.mapped.json', adapted)
    original_main = root/'source/runtime/predict_active.py'
    sys.path.insert(0, str(original_main.parent))
    sys.dont_write_bytecode = True
    # Audit hook makes Python socket connections and SQLite opening fail closed.
    blocked = []
    def offline(event, values):
        if event in ('socket.connect', 'sqlite3.connect', 'subprocess.Popen', 'os.system'):
            blocked.append(event)
            raise RuntimeError('OFFLINE_REPLAY_PROHIBITS_'+event)
    sys.addaudithook(offline)
    spec = importlib.util.spec_from_file_location('handoff_predict_active', original_main)
    active = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(active)
    sys.path.insert(0, str(original_main.parent/'continental'))
    import ucl_ml
    import classifier
    from upset_v2 import predict as candidate
    from research.analysis_freeze import verify
    # Only execution time and filesystem paths are adapted. Original files remain
    # byte-identical. Production time checks are not removed or changed.
    old_attach, old_candidate = classifier.attach, candidate.run
    reg1 = root/case['upset_registry']
    reg2 = root/case['upset_candidate_registry']
    def attach(f):
        return old_attach(f, registry_path=reg1)
    def attach_candidate(f):
        return old_candidate(f, registry_path=reg2)
    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(active.legacy, 'datetime', clock(expected['current_model']['generated_at'])))
        stack.enter_context(patch.object(ucl_ml, 'datetime', clock(expected['test_model']['generated_at'])))
        stack.enter_context(patch.object(classifier, 'attach', attach))
        stack.enter_context(patch.object(candidate, 'run', attach_candidate))
        replay = active.run(fixture, out/'registry.mapped.json')
    import numpy as np
    numerical = ('version', 'primary', 'primary_probability', 'matrix', 'matrix_hash',
        'wdl', 'wdl_reference', 'total', 'rates', 'top3', 'recipe', 'ml',
        'model_hash', 'code_hash', 'training_data_cutoff', 'team_goal_analysis',
        'score_distribution_audit')
    models = {}
    passed = not blocked
    for key in ('current_model', 'test_model'):
        old, new = expected[key], replay[key]
        a, b = np.asarray(old['matrix']), np.asarray(new['matrix'])
        differences = [field for field in sorted(set(old)|set(new)) if old.get(field) != new.get(field)]
        unequal_numerical = [field for field in numerical if old.get(field) != new.get(field)]
        valid = (a.shape == b.shape == (51,51) and np.isfinite(b).all()
            and float(b.min()) >= 0 and abs(float(b.sum())-1) < 1e-12
            and digest(new['matrix']) == new['matrix_hash'])
        primary = '-'.join(map(str, np.unravel_index(np.argmax(b), b.shape)))
        exact = bool(np.array_equal(a,b))
        models[key] = {'version':new['version'], 'original_primary':old['primary'],
            'replayed_primary':new['primary'], 'matrix_shape':list(b.shape),
            'matrix_sha256':new['matrix_hash'], 'matrix_exact_equal':exact,
            'matrix_max_absolute_error':float(np.max(np.abs(a-b))),
            'matrix_mass':float(b.sum()), 'valid_probability_matrix':bool(valid),
            'primary_is_argmax':primary == new['primary'],
            'different_fields':differences, 'different_numerical_fields':unequal_numerical,
            'training_cutoff':new['training_data_cutoff']}
        passed = passed and exact and valid and not unequal_numerical and primary == new['primary']
    freezes=[]
    for name in case['freeze_records']:
        record = verify(json.loads((root/name).read_text()))
        freezes.append({'freeze_id':record['freeze_id'], 'record_sha256':record['record_sha256'],
            'file_sha256':sha(root/name), 'original_recorded_at':record['recorded_at'],
            'stage':record['stage'], 'track':record['track']})
        assert record['match_id'] == fixture['match_id']
        if record['track'] == 'current-test-pair':
            assert record['payload'] == {k:v for k,v in expected.items() if k!='freeze_receipt'}
            assert record['record_sha256'] == expected['freeze_receipt']['sha256']
    for key in ('upset_classifier','upset_candidate'):
        if replay[key] != expected[key]: passed = False
    result = {'status':'PASS' if passed else 'FAIL',
        'scope':'OFFLINE_HISTORICAL_REPLAY_NOT_NEW_FORECAST',
        'actual_executed_at_beijing':dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(),
        'file_hashes_verified':checked,'entry':'source/runtime/predict_active.py:run',
        'source_commit':manifest['source_commit'], 'match_id':fixture['match_id'],
        'display':'波尔图—埃因霍温', 'models':models, 'original_freezes':freezes,
        'adaptations':['Separate mapped registry limited to exported cohort',
            'Per-module historical clocks set to original generated_at for offline replay',
            'Auxiliary registry arguments mapped to preserved copies'],
        'blocked_network_or_database_attempts':blocked,
        'database_required':False,'new_freeze_created':False,
        'prediction_accuracy_improvement_proven':False,
        'known_non_numerical_difference':'data_freshness_notice was publication metadata outside run(); preserved in original pair, not synthesized'}
    write(out/'replayed-pair.json', replay)
    write(out/'verification.json', result)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    if not passed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
