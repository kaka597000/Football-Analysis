"""Paired exact-score, WDL, and strict totals inference on fixed fixtures.

The JSONL interface is for research replays. For prospective claims, call
compare_audited to verify the registration, captures, and result evidence first.
"""
import argparse
from collections import Counter, defaultdict
from datetime import date
import json
import math
from pathlib import Path
import re
import sys
from zoneinfo import ZoneInfo

import numpy as np

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from optimization.evaluate import settle_ou
from research.analysis_freeze import aware
from research_program.future_validation import read, write_new
from research_program.kpi_validation import OUTCOMES, TARGETS, review


METRICS = tuple(TARGETS)
TOTAL_METRIC = 'actual_ou'
SCORE = re.compile(r'(0|[1-9][0-9]?)-(0|[1-9][0-9]?)')
DIRECTIONS = ('home', 'draw', 'away')
SETTLEMENTS = dict(zip((2, 1, 0, -1, -2), OUTCOMES))


def _actual(row):
    actual = row.get('actual')
    if actual is not None and (
        not isinstance(actual, (list, tuple)) or len(actual) != 2
        or any(type(goal) is not int or goal < 0 for goal in actual)
    ):
        raise ValueError('actual score must contain two nonnegative integers')
    return actual


def _line(row):
    line = row.get('line')
    if line is not None and (
        type(line) not in (float, int) or not math.isfinite(line)
        or line < 0 or not (float(line) * 4).is_integer()
    ):
        raise ValueError('a common actual quarter-goal line is required')
    return line


def _decisions(arm, actual, line):
    top3, primary = arm.get('top3'), arm.get('primary')
    if top3 is not None:
        if (not isinstance(top3, (list, tuple)) or len(top3) != 3
                or any(not isinstance(s, str) or not SCORE.fullmatch(s) for s in top3)
                or len(set(top3)) != 3):
            raise ValueError('three unique ordered exact scores are required')
        if primary is not None and primary != top3[0]:
            raise ValueError('first score differs from the first Top3 selection')
        primary = top3[0]
    if primary is not None and (not isinstance(primary, str) or not SCORE.fullmatch(primary)):
        raise ValueError('invalid first score')
    wdl = arm.get('wdl')
    if wdl is not None and wdl not in DIRECTIONS:
        raise ValueError('unknown win/draw/loss selection')
    side, settlement = arm.get('total_side'), arm.get('settlement')
    if side is not None and side not in ('over', 'under'):
        raise ValueError('unknown totals side')
    quoted_line = arm.get('total_line')
    if quoted_line is not None and (type(quoted_line) not in (float, int) or quoted_line != line):
        raise ValueError('forecast total line differs from the common line')
    if settlement is not None:
        if type(settlement) is int:
            settlement = SETTLEMENTS.get(settlement, 'INVALID')
        elif isinstance(settlement, str):
            settlement = {'WIN': 'FULL_WIN', 'LOSS': 'FULL_LOSS'}.get(settlement.upper(), settlement.upper())
        if settlement not in OUTCOMES or actual is None:
            raise ValueError('settlement requires a resolved score and a known outcome')
    issued = {name for name, pick in zip(METRICS, (primary, top3, wdl, side or settlement)) if pick is not None}
    if TOTAL_METRIC in issued and line is None:
        raise ValueError('totals selection requires an actual common line')
    outcomes = dict.fromkeys(METRICS)
    if actual is not None:
        home, away = actual
        score = f'{home}-{away}'
        actual_wdl = 'home' if home > away else 'away' if home < away else 'draw'
        if TOTAL_METRIC in issued:
            possible = [settle_ou(home + away, line, s) for s in ('over', 'under')]
            if side is not None:
                expected = possible[0 if side == 'over' else 1]
                if settlement not in (None, expected):
                    raise ValueError('settlement differs from selected side, actual score, or common line')
                settlement = expected
            elif settlement not in possible:
                raise ValueError('settlement impossible at the common line and actual score')
        outcomes.update(
            score_top1=None if primary is None else score == primary,
            score_top3=None if top3 is None else score in top3,
            wdl=None if wdl is None else wdl == actual_wdl,
            actual_ou=None if settlement in (None, 'PUSH') else settlement == 'FULL_WIN',
        )
    return issued, outcomes, settlement


def _normalized_rows(rows, arms):
    seen = set()
    for row in rows:
        mid, day = row['id'], row['date']
        if not isinstance(mid, str) or not mid.strip() or mid in seen:
            raise ValueError('distinct fixture IDs are required')
        if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
            raise ValueError('match date must use YYYY-MM-DD')
        seen.add(mid)
        actual, line = _actual(row), _line(row)
        decisions = {role: _decisions(row['forecast'].get(role) or {}, actual, line) for role in arms}
        push = line is not None and actual is not None and settle_ou(sum(actual), line, 'over') == 'PUSH'
        yield {'id': mid, 'date': day, 'cohort': row.get('cohort'), 'resolved': actual is not None,
               'push': push, 'issued': {r: v[0] for r, v in decisions.items()},
               'outcomes': {r: v[1] for r, v in decisions.items()},
               'settlements': {r: v[2] for r, v in decisions.items()}}


def _rate(hits, denominator):
    return hits / denominator if denominator else None


def _bootstrap(blocks, draws, seed, confidence):
    days = sorted(blocks)
    if len(days) < 2:
        return None
    net = np.array([blocks[d][0] for d in days])
    sizes = np.array([blocks[d][1] for d in days])
    rng = np.random.default_rng(seed)
    values = np.empty(draws)
    # Whole-day resampling weights fixtures by sampled day size.
    for start in range(0, draws, 128):
        stop = min(start + 128, draws)
        sample = rng.integers(len(days), size=(stop - start, len(days)))
        values[start:stop] = net[sample].sum(axis=1) / sizes[sample].sum(axis=1)
    tail = (1. - confidence) / 2
    return np.quantile(values, [tail, 1. - tail]).tolist()


def _paired(rows, baseline, candidate, metric, draws, seed, confidence):
    paired_days, fixed_days = defaultdict(lambda: [0, 0]), defaultdict(lambda: [0, 0])
    gains = losses = ties = 0
    for row in rows:
        if not row['resolved'] or metric == TOTAL_METRIC and row['push']:
            continue
        a, b = (row['outcomes'][arm][metric] for arm in (baseline, candidate))
        day = row['date']
        fixed_days[day][0] += int(bool(b)) - int(bool(a))
        fixed_days[day][1] += 1
        if a is None or b is None:
            continue
        change = int(b) - int(a)
        paired_days[day][0] += change
        paired_days[day][1] += 1
        gains += change == 1
        losses += change == -1
        ties += change == 0
    eligible = gains + losses + ties
    net = gains - losses
    fixed_net = sum(v[0] for v in fixed_days.values())
    fixed = sum(v[1] for v in fixed_days.values())
    return {'eligible': eligible, 'resolved_gate_denominator': fixed, 'coverage': _rate(eligible, fixed),
            'gained': gains, 'lost': losses, 'tied': ties, 'net_hits': net, 'net_rate': _rate(net, eligible),
            'matchday_clusters': len(paired_days),
            'date_cluster_ci': _bootstrap(paired_days, draws, seed, confidence),
            'fixed_population_net_hits': fixed_net, 'fixed_population_net_rate': _rate(fixed_net, fixed),
            'fixed_population_date_cluster_ci': _bootstrap(fixed_days, draws, seed, confidence)}


def _compare(normalized, baseline, candidate, draws, seed, confidence, evidence, targets):
    if baseline == candidate or not baseline or not candidate:
        raise ValueError('two distinct track names are required')
    if (type(draws) is not int or draws < 1 or type(seed) is not int or seed < 0
            or not 0 < confidence < 1):
        raise ValueError('positive bootstrap draws, nonnegative seed, and confidence required')
    rows = list(normalized)
    if not rows:
        raise ValueError('fixed fixture population is empty')
    settled = [r for r in rows if r['resolved']]
    pushes = sum(r['push'] for r in settled)
    tracks = {}
    for arm in (baseline, candidate):
        metrics = {}
        for k, target in targets.items():
            eligible = [r['outcomes'][arm][k] for r in settled if r['outcomes'][arm][k] is not None]
            hits, n = sum(eligible), len(eligible)
            denominator = len(settled) - (pushes if k == TOTAL_METRIC else 0)
            issued = sum(k in r['issued'][arm] for r in rows)
            metrics[k] = {'hits': hits, 'eligible': n, 'eligible_rate': _rate(hits, n),
                          'issued': issued, 'registered_coverage': _rate(issued, len(rows)),
                          'resolved_gate_denominator': denominator, 'resolved_gate_rate': _rate(hits, denominator),
                          'target': target}
        settlements = Counter(r['settlements'][arm] for r in settled)
        tracks[arm] = {'metrics': metrics, 'total_settlements': {k: settlements[k] for k in OUTCOMES}}
    return {'schema': 'WCPP-PAIRED-KPI-INFERENCE-1', 'evidence': evidence, 'population': len(rows),
            'settled_population': len(settled), 'pending': len(rows) - len(settled), 'common_total_pushes': pushes,
            'cohorts': dict(Counter(r['cohort'] for r in rows if r['cohort'])),
            'tracks': tracks, 'comparison': {'baseline': baseline, 'candidate': candidate},
            'paired': {k: _paired(rows, baseline, candidate, k, draws, seed, confidence) for k in METRICS},
            'bootstrap': {'unit': 'whole match date', 'confidence': confidence, 'repetitions': draws,
                          'seed': seed, 'per_metric_percentile_intervals': True}}


def compare(rows, *, baseline, candidate, draws=5000, seed=20261123, confidence=.95):
    """Evaluate JSONL-style replay rows on identical fixtures and common lines.

    Each arm may supply an ordered top3, primary, WDL and totals side or known
    settlement. A row missing a pick retains its place in the fixed population.
    """
    return _compare(_normalized_rows(rows, (baseline, candidate)), baseline, candidate, draws, seed, confidence,
                    {'mode': 'REPLAY_COMPARISON', 'matchday_basis': 'supplied date',
                     'prospective_certified': False}, TARGETS)


def compare_audited(plan_record, captures, results, *, evidence_root, timezone='Asia/Shanghai',
                    draws=5000, seed=20261123, confidence=.95):
    """Use the existing prospective integrity gate before the same KPI inference."""
    captures = list(captures)
    audited = review(plan_record, captures, results, evidence_root=evidence_root)
    zone = ZoneInfo(timezone)
    fixtures = plan_record['payload']['fixtures']
    forecast = {r['match_id']: r['payload'] for r in captures}
    arms = ('market_baseline', 'candidate')
    keys = ('primary', 'top3', 'wdl', 'total_side')
    rows = []
    for r in audited['rows']:
        mid = r['match_id']
        f = fixtures[mid]
        found = forecast.get(mid, {})
        rows.append({'id': mid, 'cohort': f['cohort'], 'date': aware(f['kickoff_at']).astimezone(zone).date().isoformat(),
                     'resolved': r['result'] is not None, 'push': r['common_line_push'],
                     'issued': {arm: {k for k, key in zip(METRICS, keys)
                                      if found.get(arm, {}).get(key) is not None} for arm in arms},
                     'outcomes': r['outcomes'], 'settlements': r['settlements']})
    return _compare(rows, *arms, draws, seed, confidence,
                    {'mode': 'PROSPECTIVE_DUAL_FREEZE_REVIEW', 'matchday_basis': timezone,
                     'prospective_certified': True, 'plan_sha256': audited['plan_sha256']},
                    plan_record['payload']['targets'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    ordinary = sub.add_parser('replay')
    ordinary.add_argument('--input', type=Path, required=True, help='one declared fixture per JSONL row')
    ordinary.add_argument('--baseline', required=True)
    ordinary.add_argument('--candidate', required=True)
    audit = sub.add_parser('audited')
    audit.add_argument('--plan', type=Path, required=True)
    audit.add_argument('--captures', type=Path, nargs='*', default=[])
    audit.add_argument('--results', type=Path, required=True)
    audit.add_argument('--evidence-root', type=Path, required=True)
    for command in (ordinary, audit):
        command.add_argument('--out', type=Path, required=True)
        command.add_argument('--draws', type=int, default=5000)
    args = parser.parse_args()
    if args.command == 'replay':
        with args.input.open(encoding='utf-8') as handle:
            data = compare((json.loads(line) for line in handle if line.strip()), baseline=args.baseline,
                           candidate=args.candidate, draws=args.draws)
    else:
        data = compare_audited(read(args.plan), [read(p) for p in args.captures], read(args.results),
                               evidence_root=args.evidence_root, draws=args.draws)
    write_new(args.out, data)


if __name__ == '__main__':
    main()
