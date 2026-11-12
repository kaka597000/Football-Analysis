"""Score retained alpha reconstruction against verified local ESPN FT responses.

No new requests, no weight selection. Historical metrics are development
diagnostics; future candidate IDs remain pending until verified final results.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
from research_program.replay import _finished_espn_label
from research.analysis_freeze import verify, digest, aware


def protocol_records(records, protocol):
    """Resolve every planned first freeze, including failed reconstructions."""
    by_hash = {r['record_sha256']: r for _, r in records.values()
               if r.get('track') == 'current-test-pair'}
    originals = {}
    for planned in protocol['population']:
        mid = planned['match_id']
        if mid in originals:
            raise ValueError('duplicate protocol identity')
        original = by_hash.get(planned['record_sha256'])
        if original is None:
            raise ValueError('protocol first freeze missing or checksum mismatch')
        fixture = original['fixture']
        if (original['match_id'] != mid or fixture['match_id'] != mid
                or original['stage'] != 'prematch' or original['evaluation_slot'] != 'FIRST_PREMATCH'
                or planned.get('cohort', fixture['cohort']) != fixture['cohort']
                or planned.get('kickoff_at', fixture['kickoff_at']) != fixture['kickoff_at']
                or planned['future'] is not (aware(fixture['kickoff_at']) > aware(protocol['created_at']))
                or not aware(fixture['cutoff_at']) <= aware(original['recorded_at']) < aware(fixture['kickoff_at'])):
            raise ValueError('protocol fixture identity, segment or chronology mismatch')
        originals[mid] = original
    return originals


def validate_predictions(predictions, records, protocol):
    """Bind every scored row to the original first freeze and candidate node."""
    population = {r['match_id']: r for r in protocol['population']}
    if len(population) != len(protocol['population']):
        raise ValueError('duplicate protocol identity')
    protocol_records(records, protocol)
    seen = set()
    for p in predictions:
        mid = p['match_id']
        if mid in seen:
            raise ValueError('duplicate prediction identity')
        seen.add(mid)
        original = records[p['first_freeze_id']][1]
        fixture = original['fixture']; planned = population[mid]
        if (original['track'] != 'current-test-pair' or original['stage'] != 'prematch'
                or original['evaluation_slot'] != 'FIRST_PREMATCH'
                or original['match_id'] != mid or fixture['match_id'] != mid
                or p['first_freeze_sha256'] != original['record_sha256']
                or planned['record_sha256'] != original['record_sha256']
                or p['cohort'] != fixture['cohort'] or p['kickoff_at'] != fixture['kickoff_at']
                or p['input_cutoff_at'] != fixture['cutoff_at']
                or not aware(fixture['cutoff_at']) <= aware(original['recorded_at']) < aware(fixture['kickoff_at'])):
            raise ValueError('first freeze identity or time mismatch')
        future = aware(fixture['kickoff_at']) > aware(protocol['created_at'])
        if p['future'] is not future or planned['future'] is not future:
            raise ValueError('historical/future segment mismatch')
        if {float(a) for a in p['alphas']} != {0, .25, .5, .75, 1} or len(p['alphas']) != 5:
            raise ValueError('fixed alpha grid required')
        dc, ml = (np.asarray(p['components'][k], dtype=float) for k in ('dc', 'ml'))
        current = np.asarray(original['payload']['current_model']['matrix'], dtype=float)
        test = np.asarray(original['payload']['test_model']['matrix'], dtype=float)
        for matrix in (dc, ml, current, test):
            if (matrix.ndim != 2 or matrix.shape != current.shape or matrix.shape[0] != matrix.shape[1]
                    or not np.isfinite(matrix).all() or (matrix < 0).any()
                    or not np.isclose(matrix.sum(), 1, rtol=0, atol=1e-10)):
                raise ValueError('invalid probability matrix')
        alpha = p['original_alpha']
        if type(alpha) not in (int, float) or not 0 <= alpha <= 1:
            raise ValueError('invalid original alpha')
        reconstructed = (1-alpha)*dc + alpha*ml
        if not np.allclose(reconstructed, test, rtol=0, atol=1e-12):
            raise ValueError('original frozen Test matrix mismatch')
        pairs = [(p['current'], current), (p['test'], reconstructed)]
        pairs += [(metrics, (1-float(a))*dc + float(a)*ml) for a, metrics in p['alphas'].items()]
        if any(metrics['matrix_sha256'] != digest(matrix.tolist()) for metrics, matrix in pairs):
            raise ValueError('reconstructed matrix checksum mismatch')
        if future:
            receipt = p['candidate_freeze']; candidate = records[receipt['freeze_id']][1]
            expected = {k:v for k,v in p.items() if k != 'candidate_freeze'}
            cf = candidate['fixture']
            retained = planned.get('retained_candidate_freeze')
            if retained is not None:
                if (retained != receipt
                        or aware(candidate['recorded_at']) > aware(protocol['created_at'])):
                    raise ValueError('retained future candidate declaration mismatch')
                earliest_cutoff = aware(fixture['cutoff_at'])
            else:
                earliest_cutoff = aware(protocol['created_at'])
            if (candidate['track'] != 'alpha-grid-candidate' or candidate['stage'] != 'prematch'
                    or candidate['evaluation_slot'] != 'FIRST_PREMATCH'
                    or candidate['record_sha256'] != receipt['sha256']
                    or candidate['recorded_at'] != receipt['recorded_at']
                    or candidate['match_id'] != mid or candidate['payload'] != expected
                    or {k:v for k,v in cf.items() if k != 'cutoff_at'} != {k:v for k,v in fixture.items() if k != 'cutoff_at'}
                    or not earliest_cutoff <= aware(cf['cutoff_at']) <= aware(candidate['recorded_at']) < aware(cf['kickoff_at'])):
                raise ValueError('future candidate freeze mismatch')
        elif 'candidate_freeze' in p:
            raise ValueError('historical row cannot claim future candidate freeze')


def evaluate(predictions, roots, freeze_root):
    prediction_path = Path(predictions)
    protocol_path = prediction_path.with_name('protocol.json')
    protocol = json.loads(protocol_path.read_text())
    predictions = [json.loads(x) for x in prediction_path.read_text().splitlines()]
    wanted = {p['match_id'].removeprefix('espn:') for p in predictions}
    files = defaultdict(list)
    for root in roots:
        for path in Path(root).rglob('*.json'):
            for event in set(re.findall(r'(?<!\d)\d{9}(?!\d)', path.name)) & wanted:
                files[event].append(path)
    records = {}
    for path in Path(freeze_root).glob('*/000001.json'):
        r = json.loads(path.read_text())
        if r.get('track') in {'current-test-pair', 'alpha-grid-candidate'}:
            if r['freeze_id'] in records:
                raise ValueError('duplicate freeze identity')
            records[r['freeze_id']] = (path, verify(r))
    validate_predictions(predictions, records, protocol)
    originals = protocol_records(records, protocol)
    results = []; excluded = Counter()
    by_match = {p['match_id']: p for p in predictions}
    for planned in protocol['population']:
        p = by_match.get(planned['match_id'])
        if p is None:
            original = originals[planned['match_id']]; fixture = original['fixture']
            item = {'match_id': planned['match_id'], 'cohort': fixture['cohort'],
                    'kickoff_at': fixture['kickoff_at'], 'future': planned['future'],
                    'first_freeze_id': original['freeze_id'],
                    'status': 'MISSING_RECONSTRUCTED_PREDICTION', 'reconstructed': False}
            excluded[item['status']] += 1; results.append(item); continue
        fixture = records[p['first_freeze_id']][1]['fixture']; labels = {}
        for path in files[p['match_id'].removeprefix('espn:')]:
            try:
                kickoff = datetime.fromisoformat(fixture['kickoff_at'].replace('Z', '+00:00'))
                if datetime.now(timezone.utc) < kickoff + timedelta(minutes=90):
                    continue
                raw = json.loads(path.read_text()); comp = raw['header']['competitions'][0]
                teams = {t['homeAway']: t for t in comp['competitors']}
                scores = [int(teams[s]['score']) for s in ('home', 'away')]
                row = dict(fixture, home_goals=scores[0], away_goals=scores[1])
                evidence = {'url': 'https://site.api.espn.com/apis/site/v2/sports/soccer/' + p['cohort'] + '/summary?event=' + p['match_id'].removeprefix('espn:')}
                _finished_espn_label(row, evidence, path)
                labels.setdefault(tuple(scores), []).append({'path': str(path),
                    'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'url': evidence['url']})
            except (ValueError, KeyError, TypeError, IndexError):
                continue
        item = {k: p[k] for k in ('match_id', 'cohort', 'kickoff_at', 'future', 'first_freeze_id')}
        item['reconstructed'] = True
        if len(labels) != 1:
            item['status'] = 'CONFLICTING_RESULTS' if labels else 'PENDING_OR_MISSING_VERIFIED_FT'
            excluded[item['status']] += 1; results.append(item); continue
        score, evidence = next(iter(labels.items())); h, a = score
        item.update(status='SETTLED', actual=list(score), evidence=evidence,
                    result_verified_at=datetime.now(timezone.utc).isoformat(), arms={})
        dc = np.asarray(p['components']['dc']); ml = np.asarray(p['components']['ml'])
        record = records[p['first_freeze_id']][1]
        matrices = {'current': np.asarray(record['payload']['current_model']['matrix']),
                    'test': np.asarray(record['payload']['test_model']['matrix'])}
        matrices.update({'alpha_' + alpha: (1-float(alpha))*dc + float(alpha)*ml for alpha in p['alphas']})
        for name, m in matrices.items():
            pred = list(map(int, np.unravel_index(np.argmax(m), m.shape)))
            wdl = np.array([np.tril(m, -1).sum(), np.trace(m), np.triu(m, 1).sum()])
            y = 0 if h > a else 1 if h == a else 2
            item['arms'][name] = {'statistical_top1': pred, 'top1_hit': pred == [h, a],
                'wdl_hit': int(wdl.argmax()) == y, 'wdl_brier': float(((wdl - np.eye(3)[y])**2).sum()),
                'score_log_loss': float(-np.log(max(float(m[h, a]) if h < len(m) and a < len(m) else 0, 1e-15)))}
        results.append(item)
    return {'groups': summarize(results), 'rows': results, 'missing': dict(excluded),
            'groups_scope': 'POOLED_DIAGNOSTIC_ONLY; use segments for forward validation',
            'segments': {name: summarize([r for r in results if r['future'] is future])
                         for name, future in [('historical_reconstruction', False), ('future_frozen', True)]},
            'prediction_sha256': hashlib.sha256(prediction_path.read_bytes()).hexdigest(),
            'protocol_sha256': hashlib.sha256(protocol_path.read_bytes()).hexdigest(),
            'original_protocol_population': len(protocol['population']),
            'reconstructed_population': len(predictions),
            'alpha_selected': None, 'promotion': False,
            'primary': 'NOT_SCORED: independently frozen comprehensive selections not supplied',
            'actual_line_settlement': 'NOT_SCORED: no verified paired real quotes supplied',
            'historical_reconstruction_is_new_unseen_test': False}


def summarize(results):
    groups = {}
    for cohort in ['ALL'] + sorted({r['cohort'] for r in results}):
        population = [r for r in results if cohort == 'ALL' or r['cohort'] == cohort]
        settled = [r for r in population if r['status'] == 'SETTLED']
        arms = {}
        if settled:
            for arm in settled[0]['arms']:
                dates = defaultdict(lambda: [0, 0])
                for r in settled:
                    dates[r['kickoff_at'][:10]][0] += int(r['arms'][arm]['top1_hit']) - int(r['arms']['current']['top1_hit'])
                    dates[r['kickoff_at'][:10]][1] += 1
                values = np.array(list(dates.values())); rng = np.random.default_rng(20261112); draws = []
                for _ in range(2000):
                    s = values[rng.integers(0, len(values), len(values))].sum(axis=0); draws.append(s[0]/s[1])
                arms[arm] = {'hits': sum(r['arms'][arm]['top1_hit'] for r in settled),
                             'wdl_hits': sum(r['arms'][arm]['wdl_hit'] for r in settled),
                             'net_hits_vs_current': int(values[:, 0].sum()),
                             'date_cluster_bootstrap95_rate_vs_current': np.quantile(draws, [.025, .975]).tolist()}
                for metric in ('wdl_brier', 'score_log_loss'):
                    arms[arm][metric] = float(np.mean([r['arms'][arm][metric] for r in settled]))
        groups[cohort] = {'population': len(population), 'settled': len(settled),
                          'pending_or_missing': len(population)-len(settled),
                          'reconstructed': sum(r.get('reconstructed', True) for r in population),
                          'status_counts': dict(Counter(r['status'] for r in population)),
                          'coverage': len(settled)/len(population) if population else None, 'arms': arms}
    return groups


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--predictions', required=True); p.add_argument('--result-root', action='append', required=True)
    p.add_argument('--freeze-root', required=True); p.add_argument('--out', required=True); a = p.parse_args()
    result = evaluate(a.predictions, a.result_root, a.freeze_root)
    with Path(a.out).open('x') as f: json.dump(result, f, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps(result['groups']['ALL']))
