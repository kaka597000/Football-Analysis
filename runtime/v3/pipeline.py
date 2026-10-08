"""Context-aware candidate execution and prospective evaluation, isolated from Champion."""
import sys, math, json, hashlib
from pathlib import Path
from datetime import datetime, timezone, timedelta
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'v2'))
from model import research_predict, aware, digest, matrix, probabilities, total_settlement
from evidence import fit_context, validate_context
from context import assess, VERSION

FEATURE_CONTRACT = 'source-backed-context-1'

def predict(model, fixture):
    review = assess(fixture)
    effective = dict(fixture)
    if fixture.get('context_features'):
        raise ValueError('manual context_features forbidden; derive from audited context')
    context = model.get('context_coefficients') or {}
    applied, reason = False, 'No independently validated context coefficients'
    if review['complete'] and context.get('validated') is True:
        if context.get('feature_contract') != FEATURE_CONTRACT:
            raise ValueError('context coefficients do not match feature contract')
        names = context['features']
        if set(names) != set(review['features']):
            raise ValueError('validated context feature set mismatch')
        effective['context_features'] = {k: review['features'][k] for k in names}
        effective['context_evidence'] = review['evidence']
        # Existing v2 numerical adjustment requires official, independently timed evidence.
        if any(e['source_tier'] != 'L1' for e in review['evidence']):
            reason = 'Context training requires L1 evidence; numerical adjustment disabled'
            effective.pop('context_features'); effective.pop('context_evidence')
        else:
            applied, reason = True, 'Validated context coefficients applied'
    elif not review['complete']:
        reason = 'Context incomplete; baseline matrix retained for research only'
    base = research_predict(model, effective, model['selected_family'], model['temperature'])
    failures = list(review['reasons'])
    if not applied: failures.append(reason)
    if base.get('unseen_teams'): failures.append('Cold-start teams unsupported for release')
    if fixture.get('market_conflict') is True: failures.append('Unresolved model/market disagreement')
    p = base['primary_probability']
    base.update(version=VERSION, original_input=fixture, context_assessment=review,
                context_adjustment_applied=applied, context_adjustment_reason=reason,
                recommendation={'status': 'OBSERVE' if failures else 'RESEARCH_ELIGIBLE',
                                'reasons': failures, 'score_reference': base['primary'],
                                'primary_probability': p, 'claimed_target_accuracy': None},
                declared_targets={'score_top1': .70, 'wdl': .80, 'total': .80,
                                  'achieved': False, 'formal_kpi': 'future_frozen_primary_score_top1'})
    return base

def prepare_samples(samples):
    prepared = []
    for sample in samples:
        fixture = sample['fixture']; review = assess(fixture)
        if not review['complete']:
            raise ValueError('Cannot train context on incomplete audit')
        if sample['cohort'] != fixture['cohort'] or sample['kickoff_at'] != fixture['kickoff_at'] or sample['cutoff_at'] != fixture['cutoff_at']:
            raise ValueError('sample identity/timestamps mismatch')
        if sample.get('score_basis') != 'regulation_90':
            raise ValueError('context target must be 90-minute result')
        for k in ('home_goals', 'away_goals'):
            if type(sample[k]) is not int or not 0 <= sample[k] <= 30:
                raise ValueError('invalid result target')
        if aware(sample['result_observed_at']) > datetime.now(timezone.utc):
            raise ValueError('cannot train on future-observed results')
        if aware(sample['result_observed_at']) < aware(sample['kickoff_at']) + timedelta(minutes=90):
            raise ValueError('result observed before regulation could finish')
        s = dict(sample, features=review['features'], evidence=review['evidence'])
        prepared.append(s)
    if len({s['fixture']['match_id'] for s in samples}) != len(samples):
        raise ValueError('duplicate training fixture')
    return prepared

def train_context(samples, validate=False):
    rows = prepare_samples(samples)
    if not rows: raise ValueError('real context samples unavailable')
    names = sorted(rows[0]['features'])
    if any(sorted(r['features']) != names for r in rows):
        raise ValueError('feature schema varies')
    artifact = (validate_context if validate else fit_context)(rows, names)
    artifact['feature_contract'] = FEATURE_CONTRACT
    return artifact

def freeze(payload, model, directory):
    if payload.get('version') != VERSION or payload.get('freeze_status') != 'DRAFT_NOT_FROZEN':
        raise ValueError('requires unmodified v3 draft')
    current = predict(model, payload['original_input'])
    if current['recommendation']['status'] != 'RESEARCH_ELIGIBLE':
        raise ValueError('cannot freeze: ' + '; '.join(current['recommendation']['reasons']))
    for key in ('model_hash', 'input_hash', 'matrix_hash', 'primary', 'primary_probability', 'matrix', 'rates', 'wdl', 'over', 'under', 'total_line', 'family', 'temperature', 'declared_targets', 'context_assessment', 'recommendation'):
        if payload[key] != current[key]: raise ValueError('draft changed: ' + key)
    saved = dict(payload, freeze_status='CHALLENGER_RESEARCH_FREEZE',
                 frozen_at=datetime.now(timezone.utc).isoformat(), formal_kpi_eligible=False)
    saved['record_hash'] = digest(saved)
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    path = directory / (hashlib.sha256(payload['original_input']['match_id'].encode()).hexdigest() + '.json')
    with path.open('x') as f: json.dump(saved, f, ensure_ascii=False, indent=2)
    return str(path)

def interval(hits, n):
    if not n: return None
    z = 1.959963984540054; p = hits/n; den = 1+z*z/n
    mid = (p+z*z/(2*n))/den; half = z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return [max(0., mid-half), min(1., mid+half)]

def evaluate(freezes, results, eligible_count=None):
    result_map = {}; seen = set(); score_hits = wdl_hits = 0; rows = []; pending = []
    settlements = {k: 0 for k in ('win', 'half_win', 'push', 'half_loss', 'loss')}
    for r in results:
        if r['match_id'] in result_map: raise ValueError('duplicate result')
        result_map[r['match_id']] = r
    for f in freezes:
        mid = f['original_input']['match_id']
        if mid in seen: raise ValueError('duplicate freeze')
        seen.add(mid)
        if f.get('freeze_status') != 'CHALLENGER_RESEARCH_FREEZE' or f.get('version') != VERSION:
            raise ValueError('not a v3 research freeze')
        content = {k:v for k,v in f.items() if k != 'record_hash'}
        if digest(content) != f.get('record_hash'): raise ValueError('freeze tampered')
        if aware(f['frozen_at']) >= aware(f['original_input']['kickoff_at']): raise ValueError('retrospective freeze')
        r = result_map.get(mid)
        if r is None: pending.append(mid); continue
        if r.get('score_basis') != 'regulation_90': raise ValueError('exclude extra-time/shootout score')
        if aware(r['observed_at']) > datetime.now(timezone.utc):
            raise ValueError('future-observed result')
        if aware(r['observed_at']) < aware(f['original_input']['kickoff_at']) + timedelta(minutes=90):
            raise ValueError('premature result')
        if not r.get('source_url', '').startswith('https://'): raise ValueError('result source required')
        h,a = r['home_goals'],r['away_goals']
        if any(type(g) is not int or not 0 <= g <= 30 for g in (h,a)): raise ValueError('invalid goals')
        score_ok = f['primary'] == f'{h}-{a}'; score_hits += score_ok
        d = max(f['wdl'], key=f['wdl'].get); actual = '主胜' if h>a else '平局' if h==a else '客胜'
        wdl_ok = d == actual; wdl_hits += wdl_ok
        side = max(('over','under'), key=lambda k: f[k]['win']+.5*f[k]['half_win']-.5*f[k]['half_loss']-f[k]['loss'])
        import numpy as np
        point = np.zeros((51,51)); point[h,a] = 1
        outcome = max(total_settlement(point,f['total_line'],side), key=total_settlement(point,f['total_line'],side).get)
        settlements[outcome] += 1
        rows.append({'match_id':mid,'score_hit':bool(score_ok),'wdl_hit':bool(wdl_ok),'total_side':side,'total_settlement':outcome})
    n=len(rows)
    if eligible_count is not None and (type(eligible_count) is not int or eligible_count < len(freezes)):
        raise ValueError('eligible count smaller than frozen population')
    decisive = settlements['win']+settlements['loss']
    return {'formal_kpi_added':0,'research_only':True,'frozen':len(freezes),'settled':n,'pending':pending,
            'coverage':len(freezes)/eligible_count if eligible_count else None,
            'score_top1':{'hits':int(score_hits),'n':n,'accuracy':score_hits/n if n else None,'wilson95':interval(score_hits,n)},
            'wdl':{'hits':int(wdl_hits),'n':n,'accuracy':wdl_hits/n if n else None,'wilson95':interval(wdl_hits,n)},
            'total':{'settlements':settlements,'full_win_accuracy_excluding_push_and_half':settlements['win']/decisive if decisive else None,
                     'n_full_win_or_loss':decisive,'wilson95':interval(settlements['win'],decisive)},
            'rows':rows,'target_attainment_not_proven':True}
