"""Lock the three highest first-score probabilities per competition and score whole batches."""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import math
from pathlib import Path

from research.analysis_freeze import DEFAULT_ROOT, aware
from . import goal_state_forward as ledger
from .goal_state_study import write_new
from .kpi_validation import _actual
from .r8_score_study import read

TRACK = 'research-ranked-first-scores-r10'
METRICS = {'first_exact_score':'score_top1', 'three_exact_scores':'score_top3', 'wdl':'wdl',
           'actual_line_full_win_nonpush':'actual_ou'}


def rank(candidates):
    """Prefer the probability of the displayed first score, then kickoff and identity."""
    for row in candidates:
        p = row['first_probability']
        if isinstance(p, bool) or not math.isfinite(p) or not 0 <= p <= 1:
            raise ValueError('invalid probability of displayed first score')
    return sorted(candidates, key=lambda r: (-r['first_probability'], aware(r['kickoff_at']), r['match_id']))[:3]


def population(source):
    ledger.review(source, [], source)
    refs = read(source/'receipts.json')
    registration = ledger._read(source, refs['registration'])
    fixtures, groups = {}, defaultdict(list)
    for mid, receipt in refs['matches'].items():
        record = ledger._read(source, receipt)
        fixture, payload = record['fixture'], record['payload']
        pick = payload['forecasts']['candidate']
        if pick['primary'] != pick['top3'][0] or len(set(pick['top3'])) != 3 or len(pick['top3']) != 3:
            raise ValueError('source first issue lacks its displayed three ordered scores')
        fixtures[mid] = fixture
        groups[fixture['cohort']].append(dict(match_id=mid, kickoff_at=fixture['kickoff_at'],
            first_probability=payload['quality']['p_first'], primary=pick['primary'], top3=pick['top3'],
            home=payload['home_name'], away=payload['away_name'], first_issue=receipt))
    return registration, fixtures, {code:rank(rows) for code,rows in groups.items()}


def lock(source, scope, roadmap, out, *, freeze_root=DEFAULT_ROOT):
    original, fixtures, picks = population(source)
    codes = [x['code'] for x in read(scope)['competitions']]
    if set(picks) - set(codes):
        raise ValueError('unapproved competition')
    selected = {code:picks.get(code, []) for code in codes}
    identity = dict(source_registration=original['record_sha256'], source_track=original['payload']['track'],
        policy='whole source registration; independently rank each competition by first-score probability, then kickoff then match ID',
        stages=read(roadmap)['research_roadmap_20261124']['stages'], slots_per_competition=3, selected=selected)
    plan = ledger.register(list(fixtures.values()), identity, TRACK, out, freeze_root=freeze_root)
    if any(aware(sel['first_issue']['recorded_at']) > aware(plan['recorded_at'])
           for rows in selected.values() for sel in rows):
        raise ValueError('selection used a forecast unavailable at lock')
    index = out/'plan.json'
    if index.exists():
        if read(index) != plan:
            raise ValueError('conflicting ranked study')
    else:
        write_new(index, plan)
    return plan


def sweep(rows, scores):
    settled = [(row, scores[row['match_id']]) for row in rows if row['match_id'] in scores]
    hits = sum(row['primary'] == score for row, score in settled)
    covered = sum(score in row['top3'] for row, score in settled)
    complete = len(rows) == len(settled) == 3
    return dict(planned_slots=3, occupied=len(rows), settled=len(settled), primary_hits=hits,
                displayed_three_hits=covered, complete_batches=int(complete), primary_sweeps=int(complete and hits==3),
                displayed_three_sweeps=int(complete and covered==3), pending=len(rows)-len(settled))


def review(source, out, results, evidence):
    from optimization.evaluate import settle_ou
    src, fixtures, ranking = population(source)
    plan = ledger._read(out, read(out/'plan.json'))
    identity = plan['payload']['identity']
    if (plan['track'] != TRACK+'-registration' or src['record_sha256'] != identity['source_registration']
            or ranking != {k:v for k,v in identity['selected'].items() if v}
            or plan['payload']['fixtures'] != src['payload']['fixtures']):
        raise ValueError('ranked plan and forecast population disagree')
    actual = {}
    for r in results:
        mid = r['match_id']
        if mid in actual or mid not in fixtures:
            raise ValueError('duplicate or unregistered outcome')
        actual[mid] = _actual(r, fixtures[mid], evidence, datetime.now(timezone.utc))
    scores = {mid:f'{g[0]}-{g[1]}' for mid,g in actual.items()}
    refs = read(source/'receipts.json')['matches']
    competitions = {}
    for code, selected in identity['selected'].items():
        mids = [mid for mid,f in fixtures.items() if f['cohort'] == code]
        counts = {k:[0,0] for k in METRICS}
        settlements = dict.fromkeys(('FULL_WIN','HALF_WIN','PUSH','HALF_LOSS','FULL_LOSS'), 0)
        for mid in mids:
            if mid not in actual:
                continue
            h,a = actual[mid]
            payload = ledger._read(source, refs[mid])['payload']
            pick = payload['forecasts']['candidate']
            values = dict(first_exact_score=pick['primary']==scores[mid], three_exact_scores=scores[mid] in pick['top3'],
                          wdl=pick['wdl']==('home' if h>a else 'away' if a>h else 'draw'))
            if pick['total_side'] is not None:
                outcome = settle_ou(h+a,payload['line'],pick['total_side'])
                settlements[outcome] += 1
                if outcome != 'PUSH':
                    values['actual_line_full_win_nonpush'] = outcome=='FULL_WIN'
            for k,hit in values.items():
                counts[k][0] += int(hit)
                counts[k][1] += 1
        batch = sweep(selected,scores)
        settled_n = sum(mid in actual for mid in mids)
        stages = {}
        for stage in identity['stages']:
            targets = stage['targets']
            status = {k: None if not n else h/n >= targets[k] for k,(h,n) in counts.items()}
            n = batch['complete_batches']
            status['selected_three_primary_sweep'] = None if not n else batch['primary_sweeps']/n >= targets['selected_three_primary_sweep']
            stages[stage['stage']] = dict(metrics=status, all_observed_thresholds_met=bool(mids and
                settled_n==len(mids) and all(v is True for v in status.values())))
        competitions[code] = dict(registered=len(mids), settled=settled_n, counts=counts, settlements=settlements,
                                  selected=selected,batch=batch,stages=stages)
    return dict(track=TRACK, registration=plan['freeze_id'], formal_stage_certification=False,
                population=len(fixtures), settled=len(actual), competitions=competitions)


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['lock','review'])
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--scope',type=Path,default=Path('configs/analysis-scope.json'))
    p.add_argument('--roadmap',type=Path,default=Path('project-goals.json'))
    p.add_argument('--results',type=Path)
    p.add_argument('--evidence',type=Path)
    p.add_argument('--report',type=Path)
    a=p.parse_args()
    if a.command=='lock':
        print(lock(a.source,a.scope,a.roadmap,a.out)['id'])
    else:
        if not a.report or (a.results and not a.evidence):
            p.error('review needs a new report path and an evidence root when supplying results')
        write_new(a.report,review(a.source,a.out,read(a.results) if a.results else [],a.evidence or a.out))
