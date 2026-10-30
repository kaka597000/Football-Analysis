"""Bounded unattended candidate refits; active models are never changed.

Historical seed labels are explicitly retrospective. Diagnostics are chronological
but are not a reconstruction of historically available information.
"""
import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import parse_qsl, urlsplit
import uuid

os.environ['OPENBLAS_NUM_THREADS'] = '1'
os.environ['OMP_NUM_THREADS'] = '1'
ROOT = Path(__file__).resolve().parents[1]
VERSION = 'WCPP-UNATTENDED-CANDIDATE-1'
CONFIG = {'half_life': 365, 'ridge': 20, 'minimum_rows': 120,
          'minimum_fit_rows': 80, 'minimum_audit_rows': 20, 'split_fraction': .75}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def aware(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('timezone required')
    return parsed.astimezone(timezone.utc)


def _write(path, value, *, exclusive=True):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    if exclusive:
        with path.open('x') as f:
            json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
    else:
        temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
        with temporary.open('x') as f:
            json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        temporary.replace(path)


def _engine():
    """Load the existing engine without permanently polluting generic imports."""
    opt = ROOT/'optimization'
    saved = {name: sys.modules.get(name) for name in ('schema', 'features', 'models')}
    try:
        for name in ('schema', 'features', 'models'):
            spec = importlib.util.spec_from_file_location('_unattended_'+name, opt/(name+'.py'))
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
        return module
    finally:
        for name, old in saved.items():
            if old is None: sys.modules.pop(name, None)
            else: sys.modules[name] = old


def code_fingerprint():
    paths = [Path(__file__)] + [ROOT/'optimization'/n for n in ('models.py', 'features.py', 'schema.py')]
    return digest({p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})


def _load(state_dir, now):
    scopes_path = state_dir/'config/scopes.json'
    scopes = json.loads(scopes_path.read_text())
    if not isinstance(scopes, list) or len({x['cohort'] for x in scopes}) != len(scopes):
        raise ValueError('explicit unique scope list required')
    mapping = {x['cohort']: x for x in scopes}
    for s in scopes:
        if not s.get('competition') or not isinstance(s.get('stages'), list) or not s['stages']:
            raise ValueError('competition and explicit allowed stages required')
    incoming = []
    seed = state_dir/'seed/training.json.gz'
    source_artifacts = set()
    if seed.exists():
        manifest_path = state_dir/'seed/manifest.json'
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text())
            if hashlib.sha256(seed.read_bytes()).hexdigest() != manifest.get('seed_sha256'):
                raise ValueError('seed checksum mismatch')
            if aware(manifest['created_at']) > now:
                raise ValueError('seed manifest is not yet available')
            source_artifacts = {(x['cohort'],x['source_artifact_sha256']) for x in manifest['sources']
                                if re.fullmatch(r'[0-9a-f]{64}',x.get('source_artifact_sha256',''))}
        with gzip.open(seed, 'rt') as f:
            values = json.load(f)
        if not isinstance(values, list): raise ValueError('seed array required')
        incoming.extend((x, True) for x in values)
    incremental = state_dir/'normalized-results.jsonl'
    if incremental.exists():
        with incremental.open() as f:
            incoming.extend((json.loads(line), False) for line in f if line.strip())
    selected = {}; conflicts = set(); quarantine = []; excluded = []
    for raw, seed_row in incoming:
        mid = raw.get('match_id')
        try:
            scope = mapping.get(raw.get('cohort'))
            if scope is None or raw.get('competition') != scope['competition'] or raw.get('stage') not in scope['stages']:
                raise ValueError('UNSUPPORTED_SCOPE')
            if not isinstance(mid, str) or not mid: raise ValueError('INVALID_IDENTITY')
            if raw.get('score_basis') != 'regulation_90': raise ValueError('REGULATION_SCOPE_REQUIRED')
            day = datetime.strptime(raw['date'], '%Y-%m-%d').date()
            if day >= now.date():
                excluded.append({'match_id': mid, 'reason': 'TODAY_OR_FUTURE_DATE'}); continue
            known = raw.get('known_at') or raw.get('first_known_at')
            retrospective = seed_row and raw.get('dataset_kind') == 'SEED_RETROSPECTIVE'
            if not known and retrospective:
                known = raw.get('observed_at')
            if not known:
                raise ValueError('KNOWN_AT_REQUIRED_OR_EXPLICIT_RETROSPECTIVE_SEED')
            if aware(known) > now:
                excluded.append({'match_id': mid, 'reason': 'NOT_YET_KNOWN'}); continue
            if raw.get('completed') is False: raise ValueError('NOT_COMPLETED')
            goals = [raw.get(k) for k in ('home_goals', 'away_goals')]
            if any(type(v) is not int or not 0 <= v <= 40 for v in goals):
                raise ValueError('INVALID_90_MINUTE_LABEL')
            if not all(isinstance(raw.get(k), str) and raw[k] for k in ('home', 'away')) or raw['home'] == raw['away']:
                raise ValueError('INVALID_TEAM_IDENTITY')
            if raw.get('neutral') is not None and type(raw.get('neutral')) is not bool:
                raise ValueError('INVALID_NEUTRAL_STATE')
            url = raw.get('source_url', '')
            artifact_provenance = retrospective and (raw['cohort'],raw.get('source_artifact_sha256')) in source_artifacts
            if not url and not artifact_provenance:
                raise ValueError('HTTPS_OR_VERIFIED_RETROSPECTIVE_ARTIFACT_REQUIRED')
            if url:
                parsed = urlsplit(url)
                if parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password:
                    raise ValueError('HTTPS_PROVENANCE_REQUIRED')
                if any(k.lower() in ('apikey', 'api_key', 'token', 'access_token') for k, _ in parse_qsl(parsed.query)):
                    raise ValueError('CREDENTIAL_BEARING_SOURCE')
            row = {k: raw[k] for k in ('match_id', 'cohort', 'competition', 'stage', 'date', 'home', 'away', 'home_goals', 'away_goals')}
            row['neutral'] = raw.get('neutral')
            key = (row['cohort'], row['competition'], row['stage'], row['date'], row['home'], row['away'])
            label = (row['home_goals'], row['away_goals'])
            if key in selected:
                if label != (selected[key]['home_goals'], selected[key]['away_goals']):
                    conflicts.add(key)
                    quarantine.append({'match_id': mid, 'reason': 'CONFLICTING_90_MINUTE_LABELS'})
                continue
            selected[key] = row
        except (ValueError, KeyError, TypeError):
            quarantine.append({'match_id': mid if isinstance(mid, str) else None,
                               'reason': 'ROW_FAILED_IDENTITY_TIME_SCOPE_OR_LABEL_VALIDATION'})
    rows = sorted((v for k, v in selected.items() if k not in conflicts), key=lambda r: (r['date'], r['match_id']))
    return scopes, rows, quarantine, excluded


def _metrics(engine, core, rows, *, constant=False):
    import math
    import numpy as np
    from scipy.stats import poisson
    rates = np.tile(core['constant'], (len(rows), 1)) if constant else engine.rates(core, rows)
    records = []
    for row, mu in zip(rows, rates):
        mu = np.clip(mu, .08, 8)
        m = engine.matrix(mu); i, j = np.unravel_index(m.argmax(), m.shape)
        h, a = row['home_goals'], row['away_goals']
        probability = float(m[h,a]) if h < m.shape[0] and a < m.shape[1] else float(poisson.pmf(h,mu[0])*poisson.pmf(a,mu[1]))
        records.append({'match_id': row['match_id'], 'date': row['date'], 'primary': [int(i),int(j)],
            'actual': [h,a], 'top1_hit': int(i)==h and int(j)==a,
            'score_logloss': -math.log(max(probability,1e-300)),
            'total_bias': float(mu.sum())-h-a})
    return {'n': len(records), 'top1_hits': sum(r['top1_hit'] for r in records),
            'top1_rate': sum(r['top1_hit'] for r in records)/len(records),
            'score_logloss': sum(r['score_logloss'] for r in records)/len(records),
            'total_bias': sum(r['total_bias'] for r in records)/len(records), 'records': records}


def train(state_dir, max_cohorts=21, now=None):
    now = datetime.now(timezone.utc) if now is None else now
    if not isinstance(now, datetime) or now.tzinfo is None: raise ValueError('aware current time required')
    now = now.astimezone(timezone.utc)
    if type(max_cohorts) is not int or not 1 <= max_cohorts <= 100:
        raise ValueError('bounded cohort count required')
    state_dir = Path(state_dir)
    scopes, rows, quarantine, excluded = _load(state_dir, now)
    code_hash = code_fingerprint()
    index_path = state_dir/'candidate-index.json'
    prior = json.loads(index_path.read_text()) if index_path.exists() else {'models': {}}
    index = {'schema': VERSION, 'updated_at': now.isoformat(), 'models': dict(prior.get('models', {})),
             'automatic_promotion': False}
    run_id = now.strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
    folder = state_dir/'candidates'/run_id; folder.mkdir(parents=True, exist_ok=False)
    _write(folder/'quarantine.json', quarantine)
    summary = {'schema': VERSION, 'run_id': run_id, 'created_at': now.isoformat(),
               'code_sha256': code_hash, 'configuration': CONFIG,
               'source_files': {str(p.relative_to(state_dir)): hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in (state_dir/'config/scopes.json', state_dir/'seed/training.json.gz',state_dir/'seed/manifest.json',
                             state_dir/'normalized-results.jsonl') if p.is_file()},
               'scope_count': len(scopes), 'accepted_rows': len(rows),
               'quarantined': len(quarantine), 'excluded_by_time': excluded,
               'diagnostic_scope': 'RETROSPECTIVE_CHRONOLOGICAL_NOT_PROSPECTIVE_ASOF_RECONSTRUCTION',
               'active_models_modified': False, 'automatic_promotion': False, 'cohorts': []}
    engine = None; attempted = 0
    for scope in sorted(scopes, key=lambda x: x['cohort']):
        cohort = scope['cohort']; subset = [r for r in rows if r['cohort'] == cohort]
        fingerprint = digest({'rows': subset, 'scope': scope, 'config': CONFIG, 'code_sha256': code_hash})
        row = {'cohort': cohort, 'rows': len(subset), 'input_sha256': fingerprint}
        previous = index['models'].get(cohort, {})
        if previous.get('input_sha256') == fingerprint:
            row['status'] = 'SKIPPED_UNCHANGED'
        elif len(subset) < CONFIG['minimum_rows']:
            row['status'] = 'BLOCKED_INSUFFICIENT_ROWS'
        elif attempted >= max_cohorts:
            row['status'] = 'DEFERRED_RESOURCE_BUDGET'
        else:
            attempted += 1
            try:
                dates = sorted({r['date'] for r in subset})
                boundary = dates[int(len(dates)*CONFIG['split_fraction'])]
                fit_rows = [r for r in subset if r['date'] < boundary]
                audit_rows = [r for r in subset if r['date'] >= boundary]
                if len(fit_rows)<CONFIG['minimum_fit_rows'] or len(audit_rows)<CONFIG['minimum_audit_rows']:
                    raise ValueError('insufficient chronological split')
                engine = engine or _engine()
                core = engine.fit_core(fit_rows, half_life=365, ridge=20)
                constant = {'constant': [sum(r[k] for r in fit_rows)/len(fit_rows)
                                          for k in ('home_goals','away_goals')]}
                baseline = _metrics(engine, constant, audit_rows, constant=True)
                candidate = _metrics(engine, core, audit_rows)
                # Full refit is separate from the immutable retrospective audit.
                final_core = engine.fit_core(subset, half_life=365, ridge=20)
                name = re.sub(r'[^A-Za-z0-9._-]', '_', cohort)
                name += '-'+digest(cohort)[:8]
                target = folder/name; target.mkdir(exist_ok=False)
                manifest = {'cohort': cohort, 'input_sha256': fingerprint, 'code_sha256': code_hash,
                    'created_at': now.isoformat(), 'rows': len(subset), 'train_start': subset[0]['date'],
                    'train_end': subset[-1]['date'], 'scope': scope,
                    'first_known_at_policy': 'EXPLICIT_CAPTURE_FOR_NEW_ROWS_SEED_RETROSPECTIVE',
                    'source_records': [{'match_id': r['match_id'], 'date': r['date']} for r in subset]}
                _write(target/'manifest.json',manifest)
                artifact = {'schema': VERSION, 'version': VERSION, 'status': 'CANDIDATE_RESEARCH_ONLY',
                    'model_kind': 'team_poisson', 'cohort': cohort, 'core': final_core,
                    'created_at': now.isoformat(), 'data_cutoff': subset[-1]['date'], 'input_sha256': fingerprint,
                    'forecast_scope': scope, 'code_sha256': code_hash, 'automatic_promotion': False}
                _write(target/'artifact.json', artifact)
                _write(target/'diagnostics.json', {'audit_start': boundary, 'fit_n': len(fit_rows),
                    'baseline': baseline, 'candidate': candidate,
                    'paired_top1_hit_gain': candidate['top1_hits']-baseline['top1_hits'],
                    'promotion_proven': False, 'scope': summary['diagnostic_scope']})
                row.update(status='TRAINED_CANDIDATE', artifact=str((target/'artifact.json').relative_to(state_dir)),
                    artifact_sha256=hashlib.sha256((target/'artifact.json').read_bytes()).hexdigest(),
                    fit_end=subset[-1]['date'], audit_n=len(audit_rows),
                    baseline_top1_hits=baseline['top1_hits'], candidate_top1_hits=candidate['top1_hits'])
                index['models'][cohort] = row.copy()
            except (ValueError, TypeError, KeyError, OSError, RuntimeError) as exc:
                row.update(status='TRAINING_FAILED', error_type=type(exc).__name__)
        summary['cohorts'].append(row)
    _write(folder/'summary.json', summary)
    _write(index_path, index, exclusive=False)
    _write(state_dir/'latest-training.json',summary,exclusive=False)
    return summary


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir',required=True);parser.add_argument('--max-cohorts',type=int,default=21)
    args=parser.parse_args(); result=train(args.state_dir,args.max_cohorts)
    print(json.dumps({'run_id':result['run_id'],'accepted_rows':result['accepted_rows'],
                      'statuses':[{k:r[k] for k in ('cohort','status')} for r in result['cohorts']],
                      'automatic_promotion':False},ensure_ascii=False))
