"""Paired outcomes for model selections. User-delivered KPIs require their own receipts."""

from collections import Counter, defaultdict
import json
from math import log
from pathlib import Path

import numpy as np

from optimization.evaluate import settle_ou
from research_program.prior_team_strength import SHAPE
from research_program.score_form import poisson_matrix

ARMS = ('baseline','candidate')
METRICS = ('first','top3','wdl','total')
SCOPES = json.loads((Path(__file__).resolve().parents[2]/'configs/analysis-scope.json').read_bytes())['competitions']


def outcome(r, actual):
    home, away = actual
    truth = f'{home}-{away}'
    wdl = 'home' if home>away else ('away' if away>home else 'draw')
    arms = []
    for name in ARMS:
        f = r[name]
        m = poisson_matrix(f['rates'], SHAPE)
        p = m[home,away] if home < m.shape[0] and away < m.shape[1] else 0.
        total = settle_ou(home+away,r['line'],f['total']) if f['total'] is not None else None
        arms.append({'first':f['first']==truth, 'top3':truth in f['top3'], 'wdl':f['wdl']==wdl,
                     'total':None if total is None else total=='FULL_WIN', 'settlement':total,
                     'log_loss':-log(max(float(p),1e-300))})
    return (r, *arms)


def blocked_interval(pairs, metric):
    """Paired matchday clusters, sampling dates together across all competitions."""
    days = defaultdict(lambda: [0.,0])
    for r, b, c in pairs:
        if b[metric] is not None and c[metric] is not None:
            d = days[r['day']]
            d[0] += float(c[metric])-float(b[metric])
            d[1] += 1
    if len(days) < 5:
        return None
    rows = np.array(list(days.values()))
    draws = np.random.default_rng(24112026).integers(len(rows),size=(2000,len(rows)))
    sampled = rows[draws].sum(axis=1)
    return np.quantile(sampled[:,0]/sampled[:,1],[.025,.975]).tolist()


def summarize(records, cache, intervals=False):
    pairs = [cache[r['id']] for r in records if r['id'] in cache]
    measures = {}
    for metric in METRICS:
        valid = [(r,b,c) for r,b,c in pairs if b[metric] is not None and c[metric] is not None]
        measures[metric] = {
            'n':len(valid),'baseline_hits':sum(b[metric] for _,b,_ in valid),
            'candidate_hits':sum(c[metric] for _,_,c in valid),
            'gained':sum(c[metric] and not b[metric] for _,b,c in valid),
            'lost':sum(b[metric] and not c[metric] for _,b,c in valid),
            'choices_changed':sum(r['baseline'][metric] != r['candidate'][metric] for r,_,_ in valid)}
        if metric == 'total':
            measures[metric]['settlements'] = {name:dict(Counter(p[i]['settlement'] for p in valid))
                                                for i,name in enumerate(ARMS,1)}
        if intervals:
            measures[metric]['delta_95_matchday_bootstrap'] = blocked_interval(pairs,metric)
    measures['score_log_loss'] = {name:sum(p[i]['log_loss'] for p in pairs)/len(pairs) if pairs else None
                                   for i,name in enumerate(ARMS,1)}
    if intervals:
        measures['score_log_loss']['delta_95_matchday_bootstrap'] = blocked_interval(pairs,'log_loss')
    return {'candidate_fixtures':len(records), 'paired_scores':len(pairs),
            'prediction_statuses':dict(Counter(r['status'] for r in records)),
            'candidate_fallbacks':sum(r.get('candidate_fallback',False) for r in records),
            'metrics':measures}


def report(records, labels, training):
    cache = {r['id']:outcome(r,labels[r['id']]) for r in records if r['id'] in labels}
    windows = {}
    for window in ('early_development','later_archival_check','clocked_reconstruction'):
        subset = [r for r in records if r['window']==window]
        summary = summarize(subset,cache,True)
        summary['by_competition'] = {c['code']:summarize([r for r in subset if r['league']==c['code']],cache)
                                     for c in SCOPES}
        windows[window] = summary
    later = windows['later_archival_check']['metrics']
    passed = (later['first']['candidate_hits'] > later['first']['baseline_hits']
              and later['top3']['candidate_hits'] >= later['top3']['baseline_hits']
              and later['score_log_loss']['candidate'] <= later['score_log_loss']['baseline'])
    return {'windows':windows,'training':training,
            'decision':'eligible_for_separate_future_consideration' if passed else 'retire_recipe',
            'paired_actually_delivered_first':0, 'paired_actually_delivered_three':0,
            'paired_actually_delivered_preselected_batches':0}
