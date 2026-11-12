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
from research.analysis_freeze import verify


def evaluate(predictions, roots, freeze_root):
    predictions = [json.loads(x) for x in Path(predictions).read_text().splitlines()]
    wanted = {p['match_id'].removeprefix('espn:') for p in predictions}
    files = defaultdict(list)
    for root in roots:
        for path in Path(root).rglob('*.json'):
            for event in set(re.findall(r'(?<!\d)\d{9}(?!\d)', path.name)) & wanted:
                files[event].append(path)
    records = {}
    for path in Path(freeze_root).glob('*/000001.json'):
        r = json.loads(path.read_text())
        if r.get('track') == 'current-test-pair':
            records[r['freeze_id']] = (path, verify(r))
    results = []; excluded = Counter()
    for p in predictions:
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
                          'coverage': len(settled)/len(population), 'arms': arms}
    return {'groups': groups, 'rows': results, 'missing': dict(excluded),
            'alpha_selected': None, 'promotion': False,
            'primary': 'NOT_SCORED: independently frozen comprehensive selections not supplied',
            'actual_line_settlement': 'NOT_SCORED: no verified paired real quotes supplied',
            'historical_reconstruction_is_new_unseen_test': False}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--predictions', required=True); p.add_argument('--result-root', action='append', required=True)
    p.add_argument('--freeze-root', required=True); p.add_argument('--out', required=True); a = p.parse_args()
    result = evaluate(a.predictions, a.result_root, a.freeze_root)
    with Path(a.out).open('x') as f: json.dump(result, f, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps(result['groups']['ALL']))
