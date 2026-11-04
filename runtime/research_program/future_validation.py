"""Lock, capture and review real first-prematch Current/candidate pairs.

No backfill, scheduler, best-of-two selection, or model activation is performed.
"""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
import numpy as np

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from competition_scope import require
from research.analysis_freeze import save, verify, aware, digest, DEFAULT_ROOT
from publication import deliver, atomic_json
from storage_lock import exclusive_lock
from research_program import challengers
from research_program.promotion_review import assess


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    deliver(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode())


def check_matrix(prediction):
    m = np.asarray(prediction['matrix'], float)
    if (m.ndim != 2 or not np.isfinite(m).all() or (m < 0).any()
            or not np.isclose(m.sum(), 1., atol=1e-8) or digest(prediction['matrix']) != prediction['matrix_hash']):
        raise ValueError('frozen matrix integrity')
    primary = prediction.get('primary')
    if primary is not None:
        import re
        if not isinstance(primary, str) or not re.fullmatch(r'(0|[1-9]\d?)-(0|[1-9]\d?)', primary):
            raise ValueError('canonical unique Primary required')
        h, a = map(int, primary.split('-'))
        if h >= m.shape[0] or a >= m.shape[1]:
            raise ValueError('Primary outside support')
    return m


def quote_audit(fixture, max_age_seconds=3600):
    q = fixture.get('total_quote')
    reasons = []
    if not q:
        return {'eligible': False, 'reasons': ['NO_REAL_TOTAL_QUOTE']}
    try:
        line = q['line']
        if (q.get('verified') is not True or q.get('match_id') != fixture['match_id']
                or not q.get('bookmaker') or q.get('market') not in ('asian_total', 'standard_total')
                or type(line) not in (int, float) or not np.isfinite(line) or not 0 <= line <= 20
                or not float(line*4).is_integer()
                or any(type(q[k]) not in (int, float) or not np.isfinite(q[k]) or q[k] <= 1 for k in ('over_odds', 'under_odds'))):
            reasons.append('UNVERIFIED_IDENTITY_BOOK_LINE_OR_TWO_SIDED_PRICE')
        from v2_test.data_contract import public_url
        public_url(q['source_url'])
        if not aware(q['quoted_at']) <= aware(q['query_time']) <= aware(fixture['cutoff_at']):
            reasons.append('QUOTE_CAPTURE_CUTOFF_CONFLICT')
        if not 0 <= (aware(fixture['cutoff_at'])-aware(q['quoted_at'])).total_seconds() <= max_age_seconds:
            reasons.append('QUOTE_TOO_OLD_FOR_LOCKED_3600_SECOND_WINDOW')
    except (KeyError, TypeError, ValueError, AttributeError):
        reasons.append('MISSING_OR_INVALID_REAL_QUOTE_TIME')
    return {'eligible': not reasons, 'reasons': reasons, 'quote': q}


def lock_protocol(registry_path, candidates, out):
    """Freeze all nominated candidates before target identification or inference."""
    registry_path = Path(registry_path).resolve()
    registry = read(registry_path)
    entries = {}
    for candidate_path in candidates:
        path = Path(candidate_path).resolve()
        value = read(path)
        cohort = value['cohort']
        require({'cohort': cohort})
        if cohort in entries:
            raise ValueError('one predeclared candidate per cohort')
        if (value['schema'] != 'WCPP-DYNAMIC-FORWARD-CANDIDATE-1'
                or value['distribution'] != {'family': 'poisson', 'parameter': None}
                or value['model']['config'] != challengers.CONFIG):
            raise ValueError('this protocol locks existing dynamic Poisson only')
        route = registry['models'][cohort]
        if sha(route['artifact_path']) != route['artifact_sha256']:
            raise ValueError('Current artifact checksum')
        current = read(route['artifact_path'])
        if route.get('current_engine', 'legacy_weighted_anchor') != 'legacy_weighted_anchor':
            raise ValueError('Current version extraction requires supported anchor')
        entries[cohort] = {'candidate_path': str(path), 'candidate_artifact_sha256': sha(path),
                           'candidate_version': value['version'], 'stage': value['model']['stage'],
                           'current_artifact_sha256': route['artifact_sha256'],
                           'current_version': current['anchor']['version']}
    if not entries:
        raise ValueError('candidate population required')
    protocol = {'schema': 'WCPP-FUTURE-PAIR-PROTOCOL-1', 'locked_at': datetime.now(timezone.utc).isoformat(),
                'registry_path': str(registry_path), 'registry_sha256': sha(registry_path), 'cohorts': entries,
                'source_files_sha256': {str(p.resolve()): sha(p) for p in (Path(__file__), Path(challengers.__file__))},
                'minimum_sample': 30, 'alpha': .05, 'minimum_primary_coverage': 1.,
                'quality_tolerances': {k: 0. for k in ('score_log_loss','score_brier','wdl_brier','wdl_rps')},
                'tail_brier_tolerance': 0., 'ece_tolerance': 0.,
                'primary_rule': 'STATISTICAL_MATRIX_MODE_INDEPENDENT_RESEARCH_NOT_COMPREHENSIVE',
                'wdl_rule': 'ARGMAX_WDL_MARGINAL_INDEPENDENT_OF_PRIMARY',
                'actual_line_rule': 'TWO_SIDED_REAL_BOOK_LINE_AND_QUOTE_TIME; FIVE_WAY_SETTLEMENT',
                'maximum_quote_age_seconds': 3600,
                'population_rule': 'ALL_PREREGISTERED_IDS; FIRST_PREMATCH; PENDING_AND_ABSTAIN_RETAINED',
                'automatic_promotion': False, 'status': 'LOCKED_AWAITING_UNSEEN_FIXTURE_UNIVERSE'}
    protocol['protocol_sha256'] = digest(protocol)
    write_new(out, protocol)
    return protocol


def check_protocol(protocol):
    if digest({k: v for k, v in protocol.items() if k != 'protocol_sha256'}) != protocol['protocol_sha256']:
        raise ValueError('protocol checksum mismatch')
    if sha(protocol['registry_path']) != protocol['registry_sha256']:
        raise ValueError('locked registry changed; create a separate protocol')
    for path, expected in protocol['source_files_sha256'].items():
        if sha(path) != expected:
            raise ValueError('locked executable source changed')
    for entry in protocol['cohorts'].values():
        if sha(entry['candidate_path']) != entry['candidate_artifact_sha256']:
            raise ValueError('locked candidate artifact changed')


def register(protocol, fixtures, out, freeze_root=DEFAULT_ROOT):
    """The entire explicitly supplied future population is frozen before capture."""
    check_protocol(protocol)
    now = datetime.now(timezone.utc)
    groups = {}
    seen, keys = set(), set()
    for fixture in fixtures:
        require(fixture)
        key = (fixture['cohort'], fixture['stage'], aware(fixture['kickoff_at']), fixture['home'], fixture['away'])
        if fixture['match_id'] in seen or key in keys:
            raise ValueError('duplicate fixture population')
        seen.add(fixture['match_id']); keys.add(key)
        if aware(fixture['kickoff_at']) <= now or any(k in fixture for k in ('home_goals', 'away_goals', 'actual', 'result')):
            raise ValueError('only genuinely unstarted unlabeled targets may register')
        entry = protocol['cohorts'][fixture['cohort']]
        if fixture['stage'] != entry['stage']:
            raise ValueError('locked phase required')
        groups.setdefault(fixture['cohort'], []).append(fixture)
    if not groups:
        raise ValueError('explicit future population required; do not invent targets')
    receipts = []
    for cohort, group in groups.items():
        entry = protocol['cohorts'][cohort]
        payload = {key: protocol[key] for key in ('minimum_sample', 'alpha', 'minimum_primary_coverage', 'quality_tolerances', 'tail_brier_tolerance', 'ece_tolerance')}
        payload.update({key: entry[key] for key in ('candidate_artifact_sha256', 'candidate_version', 'current_artifact_sha256', 'current_version', 'stage')})
        payload.update(cohort=cohort, protocol_sha256=protocol['protocol_sha256'],
                       eligible_match_ids=[f['match_id'] for f in group],
                       fixtures_identity=[{k: f[k] for k in ('match_id','cohort','stage','kickoff_at','home','away')} for f in group])
        shell = {'match_id': 'future-plan:'+digest(payload), 'cutoff_at': now.isoformat(),
                 'kickoff_at': min((aware(f['kickoff_at']) for f in group)).isoformat(), 'cohort': cohort}
        receipt = save(payload, shell, stage='review', track='research-preregistration', root=freeze_root,
                       idempotency_key='future-plan:'+digest(payload))
        receipts.append(receipt)
    write_new(out, {'protocol_sha256': protocol['protocol_sha256'], 'plans': receipts,
                    'notice': 'Capture uses a new actual cutoff after registration; never backdate input availability.'})
    return receipts


def capture(protocol, plan_record, fixture, out, freeze_root=DEFAULT_ROOT, resume=False):
    """Use the actual Current pipeline, preserving its independent Test output."""
    check_protocol(protocol)
    plan_record = verify(plan_record)
    plan = plan_record['payload']
    if plan_record['track'] != 'research-preregistration' or plan['protocol_sha256'] != protocol['protocol_sha256']:
        raise ValueError('registered locked protocol required')
    out = Path(out).resolve(); out.parent.mkdir(parents=True, exist_ok=True)
    journal = out.with_name(out.name+'.capture.json')
    with exclusive_lock(str(journal)+'.lock'):
        if resume:
            state = read(journal)
            if state['plan_sha256'] != plan_record['record_sha256'] or state['out'] != str(out):
                raise ValueError('capture resume binding conflict')
            fixture = state['fixture']
        else:
            if out.exists() or journal.exists():
                raise FileExistsError(out)
            now = datetime.now(timezone.utc)
            require(fixture)
            identity = {k: fixture[k] for k in ('match_id','cohort','stage','kickoff_at','home','away')}
            if identity not in plan['fixtures_identity']:
                raise ValueError('target outside entire preregistered population')
            if (not aware(plan_record['recorded_at']) < aware(fixture['cutoff_at']) <= now < aware(fixture['kickoff_at'])
                    or any(k in fixture for k in ('home_goals','away_goals','actual','result'))):
                raise ValueError('actual registered future cutoff required; no historical rerun')
            if fixture.get('identity_verified') is not True or fixture.get('age') != 'senior' or fixture.get('gender') not in ('men','male'):
                raise ValueError('verified senior male identities required')
            for path in Path(freeze_root).glob('*/000001.json'):
                prior = verify(read(path))
                if prior['match_id'] == fixture['match_id'] and prior['stage'] == 'prematch' and prior['track'] in ('current-test-pair','challenger-independent'):
                    raise ValueError('existing first forecast cannot be recreated or cherry-picked')
            value = read(protocol['cohorts'][fixture['cohort']]['candidate_path'])
            if aware(value['generated_at']) > aware(fixture['cutoff_at']):
                raise ValueError('candidate unavailable at cutoff')
            model = value['model']
            if model['config'] != challengers.CONFIG or value['distribution'] != {'family': 'poisson', 'parameter': None}:
                raise ValueError('locked inference configuration')
            if any(fixture[side] not in model['teams'] for side in ('home','away')):
                raise ValueError('unseen candidate teams; preserve refusal rather than invent strength')
            target = dict(fixture, date=aware(fixture['kickoff_at']).astimezone(timezone.utc).date().isoformat())
            if model['training_end'] >= target['date'] or model['cohort'] != fixture['cohort'] or model['stage'] != fixture['stage']:
                raise ValueError('candidate training/target boundary conflict')
            matrix = challengers.distribution(challengers.rates(model, target), 'poisson', None)[0]
            primary = tuple(map(int, np.unravel_index(matrix.argmax(), matrix.shape)))
            x, y = np.indices(matrix.shape)
            directions = ['主胜','平局','客胜']
            wdl = [float(matrix[x>y].sum()),float(matrix[x==y].sum()),float(matrix[x<y].sum())]
            from prediction_engine import legacy
            total = None
            qa = quote_audit(fixture)
            if qa['eligible']:
                q = fixture['total_quote']; over, under = [legacy.total_settlement(matrix, q['line'], side) for side in ('over','under')]
                balance = lambda p: p['win']+.5*p['half_win']-.5*p['half_loss']-p['loss']
                total = {'quote': q, 'over': over, 'under': under, 'reference_direction': 'over' if balance(over)>balance(under) else 'under'}
            prediction = {'version': plan['candidate_version'], 'input': fixture, 'input_sha256': digest(fixture),
                          'artifact_sha256': plan['candidate_artifact_sha256'], 'generated_at': datetime.now(timezone.utc).isoformat(),
                          'matrix': matrix.tolist(), 'matrix_hash': digest(matrix.tolist()), 'primary': f'{primary[0]}-{primary[1]}',
                          'primary_probability': float(matrix[primary]), 'wdl': dict(zip(directions,wdl)),
                          'wdl_reference': directions[int(np.argmax(wdl))], 'total': total, 'actual_line_audit': qa,
                          'status': 'INDEPENDENT_FORWARD_RESEARCH_NOT_PROMOTED', 'protocol_sha256': protocol['protocol_sha256']}
            from prediction_service import run
            pair = run(fixture, protocol['registry_path'])
            if (pair['current_model']['version'] != plan['current_version']
                    or pair['execution_audit']['current']['artifact_sha256'] != plan['current_artifact_sha256']):
                raise ValueError('actual Current differs from preregistration')
            check_matrix(pair['current_model']); check_matrix(prediction)
            state = {'out': str(out), 'plan_sha256': plan_record['record_sha256'], 'fixture': fixture,
                     'pair': pair, 'candidate': prediction, 'freeze_root': str(Path(freeze_root).resolve())}
            atomic_json(journal, state)
        # Idempotency keys recover a partially written pair without new inference.
        root = state['freeze_root']
        current_receipt = save(state['pair'], fixture, stage='prematch', track='current-test-pair', root=root,
                               idempotency_key=state['plan_sha256']+':current:'+fixture['match_id'])
        candidate_receipt = save(state['candidate'], fixture, stage='prematch', track='challenger-independent', root=root,
                                 idempotency_key=state['plan_sha256']+':candidate:'+fixture['match_id'])
        package = {'match_id': fixture['match_id'], 'plan_sha256': state['plan_sha256'],
                   'current_receipt': current_receipt, 'candidate_receipt': candidate_receipt}
        if any(r['evaluation_slot'] != 'FIRST_PREMATCH' for r in (current_receipt, candidate_receipt)):
            raise ValueError('concurrent earlier freeze prevents first-pair eligibility; retained records remain')
        write_new(out, package)
        return package


def audit_existing(root):
    """Read-only integrity/availability audit; no old candidate predictions."""
    rows, errors, tracks = [], [], Counter()
    for folder in sorted(Path(root).iterdir()):
        if not folder.is_dir():
            continue
        previous = None
        for path in sorted(folder.glob('[0-9]*.json')):
            try:
                record = verify(read(path))
                if record['previous_sha256'] != previous:
                    raise ValueError('freeze chain mismatch')
                previous = record['record_sha256']
                tracks[record['track']] += 1
                if record['track'] != 'current-test-pair' or record['evaluation_slot'] != 'FIRST_PREMATCH':
                    continue
                f = record['fixture']; payload = record['payload']
                row = {'match_id': f['match_id'], 'cohort': f['cohort'], 'path': str(path),
                       'record_sha256': record['record_sha256'], 'models': {}, 'issues': [],
                       'actual_line': quote_audit(f), 'candidate_pair_status': 'NOT_CAPTURED_NO_HISTORICAL_REPLAY_ALLOWED'}
                if not aware(f['cutoff_at']) <= aware(record['recorded_at']) < aware(f['kickoff_at']):
                    row['issues'].append('FREEZE_CHRONOLOGY_CONFLICT')
                for name in ('current_model','test_model'):
                    p = payload[name]
                    if p.get('input') is not None and p['input'] != f:
                        row['issues'].append(name+':INPUT_CONFLICT')
                    if p.get('generated_at') and not aware(f['cutoff_at']) <= aware(p['generated_at']) <= aware(record['recorded_at']) < aware(f['kickoff_at']):
                        row['issues'].append(name+':CHRONOLOGY_CONFLICT')
                    if p.get('matrix') is not None:
                        check_matrix(p)
                    row['models'][name] = {'version': p.get('version'), 'matrix_available': p.get('matrix') is not None,
                                          'primary_selected': p.get('primary') is not None, 'wdl_selected': p.get('wdl_reference') in ('主胜','平局','客胜'),
                                          'actual_line_selected': bool(p.get('total')), 'status': p.get('status',p.get('recommendation_status')),
                                          'input_metadata_available': p.get('input') is not None,
                                          'generation_time_available': p.get('generated_at') is not None}
                row['execution_artifact_hash_available'] = bool(payload.get('execution_audit',{}).get('current',{}).get('artifact_sha256'))
                rows.append(row)
            except (ValueError,KeyError,TypeError) as error:
                errors.append({'path': str(path), 'error': str(error)})
    return {'schema': 'WCPP-EXISTING-FIRST-FREEZE-AUDIT-1', 'first_pair_population': len(rows),
            'tracks': dict(tracks), 'chain_or_record_errors': errors, 'chronology_or_input_conflicts': sum(bool(r['issues']) for r in rows),
            'verified_timed_actual_line_population': sum(r['actual_line']['eligible'] for r in rows),
            'execution_artifact_hash_population': sum(r['execution_artifact_hash_available'] for r in rows),
            'candidate_paired_population': 0, 'rows': rows, 'historical_predictions_created': 0,
            'notice': 'First freezes are retained as issued. Missing prospective candidates cannot be reconstructed after results. Quote capture time alone does not establish real quote time.'}


def review(plan_record, packages, results, evidence_root=None, audit_receipts=None):
    """Pending is not a loss or win. Retained FT source evidence is required."""
    plan_record = verify(plan_record); plan = plan_record['payload']
    universe = plan['eligible_match_ids']
    if len(set(universe)) != len(universe) or not universe:
        raise ValueError('unique preregistered population required')
    by_id = {}
    for package in packages:
        mid = package['match_id']
        if mid in by_id or mid not in universe or package['plan_sha256'] != plan_record['record_sha256']:
            raise ValueError('duplicate/outside/unbound paired package')
        records = {}
        for role in ('current','candidate'):
            receipt = package[role+'_receipt']; rec = verify(read(receipt['path']))
            if rec['record_sha256'] != receipt['sha256']:
                raise ValueError('paired receipt binding')
            records[role+'_record'] = rec
        a, b = records['current_record'], records['candidate_record']
        if a['fixture'] != b['fixture'] or a['fixture']['match_id'] != mid:
            raise ValueError('same fixture and cutoff required')
        f = a['fixture']
        require(f)
        if (f['cohort'] != plan['cohort'] or f['stage'] != plan['stage']
                or f.get('identity_verified') is not True or f.get('age') != 'senior'
                or f.get('gender') not in ('men','male')):
            raise ValueError('registered verified scope and phase required')
        for role, rec in (('current',a),('candidate',b)):
            p = rec['payload']['current_model'] if role == 'current' else rec['payload']
            expected_track = 'current-test-pair' if role == 'current' else 'challenger-independent'
            if rec['track'] != expected_track or rec['stage'] != 'prematch' or rec['evaluation_slot'] != 'FIRST_PREMATCH':
                raise ValueError('first independent prematch required')
            if p['input'] != rec['fixture'] or p['version'] != plan[role+'_version']:
                raise ValueError('locked actual input/version required')
            artifact = (rec['payload'].get('execution_audit',{}).get('current',{}).get('artifact_sha256') if role == 'current' else p.get('artifact_sha256'))
            if artifact != plan[role+'_artifact_sha256']:
                raise ValueError('locked artifact required')
            f = rec['fixture']
            if not aware(plan_record['recorded_at']) < aware(f['cutoff_at']) <= aware(p['generated_at']) <= aware(rec['recorded_at']) < aware(f['kickoff_at']):
                raise ValueError('registered actual prematch chronology required')
            check_matrix(p)
        by_id[mid] = records
    actual, seen_results = {}, set()
    for result in results:
        mid = result['match_id']
        if mid in seen_results or mid not in universe:
            raise ValueError('duplicate or outside result')
        seen_results.add(mid)
        # Non-final observations never become outcome labels.
        if result.get('event_status') != 'FT' or result.get('score_basis') != 'regulation_90':
            continue
        if mid not in by_id:
            continue
        f = by_id[mid]['current_record']['fixture']
        if not aware(f['kickoff_at'])+timedelta(minutes=90) <= aware(result['observed_at']) <= datetime.now(timezone.utc):
            raise ValueError('result capture chronology')
        if any(type(result.get(k)) is not int or not 0 <= result[k] <= 40 for k in ('home_goals','away_goals')):
            raise ValueError('regulation result score')
        evidence = result.get('evidence')
        if evidence_root is None or not evidence:
            raise ValueError('retained result evidence required')
        base = Path(evidence_root).resolve(); path = (base/evidence['file']).resolve()
        if (not path.is_relative_to(base) or sha(path) != evidence['sha256']
                or evidence.get('identity_verified') is not True or aware(evidence['query_time']) != aware(result['observed_at'])):
            raise ValueError('retained result evidence checksum/identity/time')
        source_result = dict(result, kickoff_at=f['kickoff_at'], home=f['home'], away=f['away'], cohort=f['cohort'])
        from research_program.replay import _finished_espn_label
        _finished_espn_label(source_result, evidence, path)
        actual[mid] = dict(result, status='completed', source_url=evidence['url'])
    completed, pending, missing, directions = [], [], [], []
    for mid in universe:
        if mid not in by_id:
            missing.append(mid)
        elif mid not in actual:
            pending.append(mid)
        else:
            records = by_id[mid]; result = actual[mid]
            completed.append(dict(records, match_id=mid, result=result))
            h,a = result['home_goals'], result['away_goals']; wd = '主胜' if h>a else '客胜' if h<a else '平局'
            f = records['current_record']['fixture']; qa = quote_audit(f)
            values = {}
            for role in ('current','candidate'):
                p = records[role+'_record']['payload']; p = p['current_model'] if role == 'current' else p
                direction = p.get('wdl_reference'); total = p.get('total'); settlement = None
                same_quote = total and all(total.get('quote',{}).get(k) == f.get('total_quote',{}).get(k)
                                           for k in ('match_id','bookmaker','market','line','quoted_at','query_time','over_odds','under_odds','source_url'))
                if qa['eligible'] and same_quote and total.get('reference_direction') in ('over','under'):
                    from prediction_engine import legacy
                    point = np.zeros((41,41)); point[h,a] = 1.
                    probabilities = legacy.total_settlement(point, f['total_quote']['line'], total['reference_direction'])
                    settlement = next(key for key, prob in probabilities.items() if np.isclose(prob,1.))
                values[role] = {'primary_selected': p.get('primary') is not None,
                                'primary_hit': None if p.get('primary') is None else p['primary'] == f'{h}-{a}',
                                'wdl_hit': direction == wd if direction in ('主胜','平局','客胜') else None,
                                'total_settlement': settlement}
            directions.append({'match_id': mid, 'models': values})
    independent = {}
    for role in ('current','candidate'):
        vals = [r['models'][role] for r in directions]
        independent[role] = {'settled_population': len(vals),
                             'primary_n': sum(v['primary_selected'] for v in vals),
                             'primary_hits': sum(v['primary_hit'] is True for v in vals),
                             'wdl_n': sum(v['wdl_hit'] is not None for v in vals),
                             'wdl_hits': sum(v['wdl_hit'] is True for v in vals),
                             'actual_line_n': sum(v['total_settlement'] is not None for v in vals),
                             'actual_line_five_way': dict(Counter(v['total_settlement'] for v in vals if v['total_settlement']))}
    return {'schema': 'WCPP-REAL-FUTURE-PAIRED-REVIEW-1', 'population': len(universe), 'completed': len(completed),
            'pending_results': pending, 'missing_first_pairs': missing, 'independent_denominators': independent,
            'rows': directions, 'promotion_review': assess(plan_record, completed, audit_receipts),
            'automatic_promotion': False, 'activation_modified': False,
            'notice': 'No missing or live result is scored; direction and real-line denominators are independent. Software checks do not substitute independent source or market audit.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__); sub = parser.add_subparsers(dest='command', required=True)
    lock = sub.add_parser('lock'); lock.add_argument('--registry', required=True); lock.add_argument('--candidates', nargs='+', required=True); lock.add_argument('--out', required=True)
    reg = sub.add_parser('register'); reg.add_argument('--protocol', required=True); reg.add_argument('--fixtures', required=True); reg.add_argument('--out', required=True); reg.add_argument('--freeze-root', default=DEFAULT_ROOT)
    cap = sub.add_parser('capture'); cap.add_argument('--protocol', required=True); cap.add_argument('--plan', required=True); cap.add_argument('--fixture', required=True); cap.add_argument('--out', required=True); cap.add_argument('--freeze-root', default=DEFAULT_ROOT); cap.add_argument('--resume', action='store_true')
    audit = sub.add_parser('audit-existing'); audit.add_argument('--root', default=DEFAULT_ROOT); audit.add_argument('--out', required=True)
    rev = sub.add_parser('review'); rev.add_argument('--plan', required=True); rev.add_argument('--packages', required=True); rev.add_argument('--results', required=True); rev.add_argument('--evidence-root'); rev.add_argument('--audit-receipts'); rev.add_argument('--out', required=True)
    args = parser.parse_args()
    if args.command == 'lock': result = lock_protocol(args.registry, args.candidates, args.out)
    elif args.command == 'register': result = register(read(args.protocol), read(args.fixtures), args.out, args.freeze_root)
    elif args.command == 'capture': result = capture(read(args.protocol), read(args.plan), read(args.fixture), args.out, args.freeze_root, args.resume)
    elif args.command == 'audit-existing': result = audit_existing(args.root); write_new(args.out, result)
    else: result = review(read(args.plan), read(args.packages), read(args.results), args.evidence_root, read(args.audit_receipts) if args.audit_receipts else None); write_new(args.out, result)
    print(json.dumps({'command': args.command, 'out': args.out, 'automatic_promotion': False}))


if __name__ == '__main__':
    main()
