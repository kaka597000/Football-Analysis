"""Per-scope artifact and synthetic load audit, explicitly separate from live delivery."""
import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
from prediction_engine import predict_models
from research.analysis_freeze import verify


def audit(registry, freezes):
    policy = json.loads((ROOT / 'configs/analysis-scope.json').read_text())
    reg = json.loads(Path(registry).read_text()); retained = defaultdict(list)
    for path in Path(freezes).glob('*/000001.json'):
        record = json.loads(path.read_text())
        if record.get('track') == 'current-test-pair' and record.get('stage') == 'prematch':
            verify(record); retained[record['fixture'].get('cohort')].append(record)
    result = []; now = datetime.now(timezone.utc)
    for scope in policy['competitions']:
        routes = []
        for cohort, route in reg['models'].items():
            if cohort.split(':')[0] != scope['code']: continue
            models = []
            for role, prefix in [('current', ''), ('test', 'test_')]:
                path = Path(route[prefix + 'artifact_path']); a = json.loads(path.read_text())
                checksum = hashlib.sha256(path.read_bytes()).hexdigest()
                if checksum != route[prefix + 'artifact_sha256']:
                    raise ValueError('active artifact changed')
                core = a.get('core', {})
                models.append({'role': role, 'path': str(path), 'sha256': checksum,
                    'version': a.get('version'), 'train_end': a.get('train_end', core.get('fit_end')),
                    'training_n': a.get('training_n', core.get('fit_n', len(a.get('history', [])) or None)),
                    'acceptance': a.get('acceptance'),
                    'stage_scope': a.get('forecast_scope') or a.get('competition_route') or route.get('scope') or 'regular-only retained route'})
            entries = retained.get(cohort, [])
            usable = [r for r in entries if r['payload'].get('current_model', {}).get('matrix') is not None
                      and type(r['fixture'].get('neutral')) is bool]
            load = {'status': 'NOT_EXECUTED_NO_PRIOR_SUCCESSFUL_FIXTURE', 'synthetic': True}
            if usable:
                reference = max(usable, key=lambda r: r['recorded_at'])
                fixture = dict(reference['fixture'], match_id='SYNTHETIC-SCOPE-LOAD-' + cohort,
                               cutoff_at=now.isoformat(), kickoff_at=(now + timedelta(days=1)).isoformat(),
                               sources=[{'source_url': 'https://example.org/SYNTHETIC-LOAD-ONLY', 'query_time': now.isoformat()}])
                if 'date' in fixture:
                    fixture['date'] = (now + timedelta(days=1)).date().isoformat()
                fixture.pop('total_quote', None)
                try:
                    pair = predict_models(fixture, registry, retain_current_on_test_failure=True)
                    load.update(status='PAIRED' if pair['experimental'].get('matrix') is not None else 'PARTIAL_CURRENT_ONLY',
                                current_status=pair['current'].get('status'), test_status=pair['experimental'].get('status'))
                except (ValueError, OSError, ImportError, AssertionError) as exc:
                    load.update(status='FAILED', error_type=type(exc).__name__, reason=str(exc))
            routes.append({'cohort': cohort, 'models': models, 'load_audit': load,
                           'retained_first_pair_count': len(entries),
                           'actual_live_acceptance': 'SEE_EXPLICIT_J1_RECEIPTS' if cohort == 'jpn.1' else 'NOT_REPEATED_THIS_AUDIT',
                           'future_effect_validation': 'PENDING; frozen forecasts are not completed validation'})
        result.append({'competition': scope['code'], 'name': scope['name'], 'requires_phase_identity': scope['requires_phase_identity'],
                       'active_routes': routes, 'active_route_status': 'REGISTERED_RESEARCH' if routes else 'NO_ACTIVE_ROUTE',
                       'unlisted_stages': 'NOT_CERTIFIED; do not infer knockout/playoff or another era coverage'})
    return {'created_at': now.isoformat(), 'scope_sha256': hashlib.sha256((ROOT/'configs/analysis-scope.json').read_bytes()).hexdigest(),
            'registry_sha256': hashlib.sha256(Path(registry).read_bytes()).hexdigest(), 'competitions': result,
            'synthetic_forecasts_published': 0, 'active_modified': False}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--registry', required=True); p.add_argument('--freeze-root', required=True)
    p.add_argument('--out', required=True); a = p.parse_args()
    os.environ.setdefault('WCPP_J1_COMPATIBILITY_MANIFEST', '/workspace/WCPP/runtime/retained-j1/manifest.json')
    result = audit(a.registry, a.freeze_root)
    with Path(a.out).open('x') as f: json.dump(result, f, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({'competitions': len(result['competitions']), 'routes': sum(len(v['active_routes']) for v in result['competitions'])}))
