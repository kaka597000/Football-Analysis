"""Fit on the earlier international matchdays, seal the next group round, then grade by explicit command."""

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import subprocess

import numpy as np
from scipy.stats import binomtest

from archive_inputs import HERE, inputs
from labels import ANCHOR, certified
from research.analysis_freeze import aware, digest
from research_program.three_arm_model import _fit_weight, score_picks, top_three_scores
from research_program.three_arm_review import settle_ou

RECIPE = 'WCPP-NATIONS-TIER-SUPPORTED-POOL-1'
SEAL_COMMIT = 'eb2fe3e7abd83a738e51e3d459551771d8dbc5f8'
STATES = ('FULL_WIN', 'HALF_WIN', 'PUSH', 'HALF_LOSS', 'FULL_LOSS')
ARMS = ('original_test', 'tier_pool', 'pooled_ablation', 'market_reference')


def fit(rows, labels):
    pairs, days, tiers = [], set(), Counter()
    for mid, row in sorted(rows.items()):
        if row['date'] >= '2026-11-15':
            raise ValueError('held-out date supplied to fit')
        if mid not in labels or row['market'] is None:
            continue
        known = labels[mid]
        if max(aware(known['observed_at']), aware(known['committed_at'])) > ANCHOR:
            raise ValueError('training result not independently available by the fit origin')
        h, a = known['score']
        pairs.append((row['base'][h,a], row['market'][h,a]))
        days.add(row['date'])
        tiers[row['tier']] += 1
    global_w = min(0.5, _fit_weight(pairs)) if len(pairs) >= 16 and len(days) >= 2 else 0.
    weights = {k: global_w*tiers[k]/(tiers[k]+4) if tiers[k] >= 4 else 0. for k in 'ABCD'}
    return dict(registered=len(rows), certified_results=len(labels), pairs=len(pairs), days=sorted(days),
                by_tier=dict(sorted(tiers.items())), global_weight=global_w, weights=weights)


def forecast(row, weights):
    base, market = row['base'], row['market']
    if market is not None and market.shape != base.shape:
        raise ValueError('the two experts must use the same score support')
    ready = aware(row['cutoff']) >= ANCHOR and market is not None
    w = weights['weights'][row['tier']] if ready else 0.
    pooled = weights['global_weight'] if ready else 0.
    def mixture(weight):
        return (1-weight)*base + weight*market if weight else base
    matrices = {'original_test':base, 'tier_pool':mixture(w), 'pooled_ablation':mixture(pooled)}
    if market is not None:
        matrices['market_reference'] = market
    return matrices, w


def selection(m, line):
    s = score_picks(m, line=line)
    return dict(primary=s['primary'], top3=top_three_scores(m), wdl=s['wdl'],
                total=s['total']['direction'] if s['total'] else None)


def build(repo):
    rows = inputs(repo)
    train = {k:v for k,v in rows.items() if v['date'] < '2026-11-15'}
    late = {k:v for k,v in rows.items() if v['date'] >= '2026-11-15'}
    if len(train) != 26 or len(late) != 26:
        raise ValueError('registered group rounds changed')
    observed, issues = certified(repo, train, training=True)
    weights = fit(train, observed)
    seals, predictions = [], {}
    for mid,row in sorted(late.items()):
        matrices,w = forecast(row, weights)
        decisions = {key:selection(m,row['line']) for key,m in matrices.items()}
        item = dict(match_id=mid, tier=row['tier'], date=row['date'], cutoff=row['cutoff'],
                    fixture_pair_sha=row['pair_sha'], original_comprehensive_sha=row['issue_sha'],
                    raw_quote_sha=row.get('raw_sha'), line=row['line'], book=row['book'],
                    missing=row['gap'], weight=w, decisions=decisions,
                    matrices={k:digest(m.tolist()) for k,m in matrices.items()})
        seals.append(dict(id=mid, tier=row['tier'], date=row['date'], fixture_pair_sha=row['pair_sha'],
                          original_issue_sha=row['issue_sha'], forecast_sha256=digest(item)))
        predictions[mid] = (matrices, decisions, w)
    locked = dict(recipe=RECIPE, protocol_sha256=sha256((HERE/'protocol.json').read_bytes()).hexdigest(),
                  sources_sha256=sha256((HERE/'sources.json').read_bytes()).hexdigest(), training=weights,
                  train_label_evidence_hash=digest(observed), train_validation_issues=issues,
                  earlier_input_gaps=dict(Counter(v['gap'] or 'MATCHED' for v in train.values())),
                  later_input_gaps=dict(Counter(v['gap'] or 'MATCHED' for v in late.values())),
                  heldout_seals=seals, heldout_sha256=digest(seals), r7_independent_ids=192)
    return locked, late, predictions


def outcomes(matrix, picks, goals, line):
    h,a = goals
    actual = f'{h}-{a}'
    settlement = settle_ou(h+a,line,picks['total']) if picks['total'] else None
    return dict(top1=picks['primary']==actual, top3=actual in picks['top3'],
                wdl=picks['wdl']==('home' if h>a else 'away' if a>h else 'draw'),
                total=None if settlement in (None,'PUSH') else settlement=='FULL_WIN',
                settlement=settlement, score_log_loss=-float(np.log(max(matrix[h,a],1e-300))))


def aggregate(items):
    result = dict(registered=len(items), certified=sum('score' in r for r in items),
                  first_comprehensive=sum(r['has_issue'] for r in items), expert=sum(r['expert'] for r in items),
                  positive_weight=sum(r['weight']>0 for r in items), original_lines=sum(r['has_line'] for r in items))
    for arm in ARMS:
        records = [r['arms'][arm] for r in items if 'score' in r and arm in r['arms']]
        loss = [v['score_log_loss'] for v in records]
        result[arm] = dict(n=len(records), score_log_loss=float(np.mean(loss)) if loss else None,
                           states=dict((s,sum(v['settlement']==s for v in records)) for s in STATES))
        for k in ('top1','top3','wdl','total'):
            scored = [v[k] for v in records if v[k] is not None]
            result[arm][k] = dict(n=len(scored), hits=sum(scored) if scored else None)
    for a,b in (('original_test','tier_pool'), ('pooled_ablation','tier_pool'), ('market_reference','tier_pool')):
        details = {}
        for metric,choice in (('top1','primary'),('top3','top3'),('wdl','wdl'),('total','total')):
            comparable = [r for r in items if 'score' in r and a in r['arms'] and b in r['arms']
                          and r['arms'][a][metric] is not None and r['arms'][b][metric] is not None]
            gained = sum(not r['arms'][a][metric] and r['arms'][b][metric] for r in comparable)
            lost = sum(r['arms'][a][metric] and not r['arms'][b][metric] for r in comparable)
            details[metric] = dict(n=len(comparable), gained=gained,lost=lost,
                                   changed=sum(r['choices'][a][choice]!=r['choices'][b][choice] for r in comparable))
            if metric == 'top1':
                details[metric]['two_sided_exact_p'] = (float(binomtest(gained,gained+lost,.5).pvalue)
                                                         if gained+lost else None)
        result[b+'_minus_'+a] = details
    return result


def grade(repo, late, predictions):
    labels, issues = certified(repo, late, training=False)
    items = []
    for mid,row in sorted(late.items()):
        mats, choices, w = predictions[mid]
        item = dict(tier=row['tier'], date=row['date'], has_issue=row['issue_sha'] is not None,
                    expert=row['market'] is not None, weight=w, has_line=row['line'] is not None,
                    book=row['book'], choices=choices, arms={})
        if mid in labels:
            score = labels[mid]['score']
            item.update(score=score, arms={k:outcomes(m,choices[k],score,row['line']) for k,m in mats.items()})
        items.append(item)
    return dict(panel=aggregate(items), common_expert=aggregate([r for r in items if r['expert']]),
                original_fallback=aggregate([r for r in items if r['weight']==0]),
                positive_weight=aggregate([r for r in items if r['weight']>0]),
                by_tier={k:aggregate([r for r in items if r['tier']==k]) for k in 'ABCD'},
                by_date={d:aggregate([r for r in items if r['date']==d]) for d in sorted({r['date'] for r in items})},
                actual_lines_by_book={b:aggregate([r for r in items if r['book']==b])
                                      for b in sorted({r['book'] for r in items if r['book']})},
                missing_certified_results=sorted(set(late)-set(labels)), result_issues=issues,
                grade_evidence_digest=digest(labels), quote_age_seconds=sorted(
                    r['quote_age_s'] for r in late.values() if r['line'] is not None),
                genuine_candidate_deliveries=dict(top1=0,displayed_three=0,preselected_batches=0))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('seal','grade'))
    parser.add_argument('--data',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--seal-commit')
    args = parser.parse_args()
    if args.command == 'grade' and args.seal_commit != SEAL_COMMIT:
        parser.error('grade requires the published pre-grading --seal-commit ' + SEAL_COMMIT)
    locked, late, predictions = build(args.data)
    if args.command == 'grade':
        path = 'research/20261124-nations-tier-fusion/forecast-lock.json'
        raw = subprocess.check_output(['git','-C',str(HERE),'show',args.seal_commit+':'+path])
        if locked != json.loads(raw):
            raise ValueError('recomputed fits, input identities or forecasts differ from the committed lock')
        data = grade(args.data,late,predictions)
        data['seal_commit'] = args.seal_commit
    else:
        data = locked
    with args.out.open('x',encoding='utf-8') as out:
        out.write(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+'\n')


if __name__ == '__main__':
    main()
