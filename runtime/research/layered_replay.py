"""Read verified freezes; reconstruct selector comparisons, never backdate them."""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import numpy as np

try:
    from .analysis_freeze import verify, aware
    from .layered_selector import compare_pair, digest, POLICY
except ImportError:
    from analysis_freeze import verify, aware
    from layered_selector import compare_pair, digest, POLICY


def evaluate(root, results):
    if len({r['match_id'] for r in results}) != len(results):
        raise ValueError('duplicate result IDs require explicit reconciliation')
    observed = {}
    for r in results:
        if r.get('completed') is False: continue
        explicit90 = (r.get('score_basis') == 'regulation_90' or
                      (r.get('stats_scope') == 'REGULATION_90_PROVIDER_ALL'
                       and r.get('extra_time_played') is False))
        if not explicit90: continue
        score = r.get('score', [r.get('home_goals'), r.get('away_goals')])
        if len(score) != 2 or any(type(v) is not int or v < 0 for v in score):
            raise ValueError('integer regulation scores required')
        if not r.get('source_url', '').startswith('https://'):
            raise ValueError('result source required')
        if aware(r['observed_at']) > datetime.now(timezone.utc):
            raise ValueError('future result observation')
        observed[r['match_id']] = dict(r, score=score)
    records = {}; rejected = []
    for path in sorted(Path(root).glob('*/[0-9]*.json')):
        r = verify(json.loads(path.read_text()))
        if r.get('track') != 'current-test-pair' or r.get('stage') != 'prematch': continue
        if r.get('revision') != 1: continue  # Predeclared first version; no best-of-updates.
        if r['match_id'] in records: raise ValueError('duplicate first freeze')
        f = r['fixture']
        if not (aware(f['cutoff_at']) <= aware(r['recorded_at']) < aware(f['kickoff_at'])):
            rejected.append({'path': str(path), 'reason': 'NOT_REAL_PREMATCH'}); continue
        records[r['match_id']] = (path, r)
    groups = defaultdict(list); pending = []; abstained = []
    for mid, (path, r) in records.items():
        comparison = compare_pair(r['payload'])
        for name, row in comparison['models'].items():
            if row['status'] != 'EXPERIMENTAL':
                abstained.append({'match_id': mid, 'model': name, 'reason': row['status']}); continue
            if mid not in observed:
                pending.append({'match_id': mid, 'model': name}); continue
            actual = observed[mid]['score']
            if 'kickoff_at' in observed[mid] and aware(observed[mid]['kickoff_at']) != aware(r['fixture']['kickoff_at']):
                raise ValueError('result kickoff identity conflict')
            if aware(observed[mid]['observed_at']) <= aware(r['recorded_at']):
                raise ValueError('result must be observed after freeze')
            group = (name, str(row['source_model_version']), str(r['fixture'].get('cohort')))
            groups[group].append({'match_id': mid, 'freeze_id': r['freeze_id'],
                'source_freeze_sha256': r['record_sha256'], 'source_path': str(path),
                'source_result_url': observed[mid]['source_url'],
                'recorded_at': r['recorded_at'], 'actual': actual,
                'joint': row['joint_top1'], 'layered': row['layered_pick'],
                'joint_hit': row['joint_top1'] == actual, 'layered_hit': row['layered_pick'] == actual})
    reports = []
    for key, rows in sorted(groups.items()):
        # Small-sample description only; clustered future inference remains required.
        changed_right = sum(not x['joint_hit'] and x['layered_hit'] for x in rows)
        changed_wrong = sum(x['joint_hit'] and not x['layered_hit'] for x in rows)
        reports.append({'model_track': key[0], 'model_version': key[1], 'cohort': key[2],
            'n': len(rows), 'joint_hits': sum(x['joint_hit'] for x in rows),
            'layered_hits': sum(x['layered_hit'] for x in rows),
            'changed_wrong_to_right': changed_right, 'changed_right_to_wrong': changed_wrong,
            'net_top1_gain': changed_right-changed_wrong,
            'disagreements': sum(x['joint'] != x['layered'] for x in rows), 'rows': rows})
    return {'schema': 'WCPP-LAYERED-REPLAY-1', 'created_at': datetime.now(timezone.utc).isoformat(),
        'mode': 'RETROSPECTIVE_SELECTOR_RECONSTRUCTION_NOT_NEW_PREMATCH_FREEZE',
        'result_input_sha256': digest(results),
        'policy_sha256': digest(POLICY), 'policy': POLICY,
        'first_prematch_matches': len(records), 'compatible_results_supplied': len(observed),
        'reports': reports, 'pending_results': pending, 'abstained': abstained,
        'rejected': rejected, 'promotion_proven': False,
        'uncertainty': 'Descriptive only; versions/cohorts separate; no significance claim from this sample.'}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', required=True); p.add_argument('--results', required=True); p.add_argument('--out', required=True)
    a = p.parse_args()
    if Path(a.out).exists(): raise FileExistsError(a.out)
    result = evaluate(a.root, json.loads(Path(a.results).read_text()))
    with Path(a.out).open('x') as f: json.dump(result, f, ensure_ascii=False, indent=2, allow_nan=False)
    print(a.out)
