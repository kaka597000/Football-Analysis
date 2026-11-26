"""Check whether team and shot models can resolve close R13 score choices."""

import argparse
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from . import all_scope_model as base
from . import continuation_study as archive
from . import europe_score_dispersion as locked_grids
from . import r8_score_study as control
from .goal_state_study import sha, write_new
from .paired_kpi_inference import compare
from .r9_team_shape import published_rank
from .ucl_zero_goals import IDENTITY, reference_rows


PROTOCOL = Path(__file__).resolve().parents[2] / 'research/20261126-r35-europe-close-score-agreement/protocol.json'
GAP = .015
ARMS = ('first_agreement', 'three_agreement')
SCORES = ('score_top1', 'score_top3')


def cell(score):
    return tuple(map(int, score.split('-')))


def pools(matrix, ordered):
    ranked = np.argsort(-matrix.ravel(), kind='stable')[:6]
    ranks = [f'{home}-{away}' for home, away in zip(*np.unravel_index(ranked, matrix.shape), strict=True)]
    if ordered != ranks[:3]:
        return [ordered[0]], []
    first_mass, third_mass = (matrix[cell(ordered[index])] for index in (0, 2))
    return ([score for score in ordered if first_mass - matrix[cell(score)] <= GAP],
            [score for score in ranks[3:] if third_mass - matrix[cell(score)] <= GAP])


def choose_scores(ordered, first_pool, third_pool, team, shot):
    first = team[0] if team[0] == shot[0] and team[0] in first_pool else ordered[0]
    first_only = [first, *(value for value in ordered if value != first)]
    last = first_only[-1]
    team_rank, shot_rank = ({score: i for i, score in enumerate(arm)} for arm in (team, shot))
    options = [score for score in third_pool if team_rank.get(score, 3) < team_rank.get(last, 3)
               and shot_rank.get(score, 3) < shot_rank.get(last, 3)]
    if not options:
        return first_only, first_only
    extra = min(options, key=lambda score: team_rank[score] + shot_rank[score])
    return first_only, [*first_only[:2], extra]


def record(ref, matrix, team, shot):
    if any(pick[key] != ref[key] for pick in (team, shot) for key in IDENTITY):
        raise ValueError('team or shot fixture, result or line differs: ' + ref['id'])
    original = ref['forecast']['candidate']
    ordered = original['top3']
    old = dict(r13=original, r7=ref['forecast']['r7'], market=ref['forecast']['market'],
               r9=team['forecast']['full_dose'], r10=shot['forecast']['fixed_full'])
    controls_agree = all(pick['forecast'][arm] == old[arm] for pick in (team, shot) for arm in ('r7', 'market'))
    ready = min(team['prior_team_games']) > 0 and shot['shot_ready'] is True and controls_agree
    first_pool, third_pool = pools(matrix, ordered)
    changed = (ordered, ordered)
    if ready:
        changed = choose_scores(ordered, first_pool, third_pool, old['r9']['top3'], old['r10']['top3'])
    forecasts = {arm: dict(original, primary=scores[0], top3=scores) for arm, scores in zip(ARMS, changed, strict=True)}
    actual = tuple(ref['actual'])
    exact = f'{actual[0]}-{actual[1]}'
    probability = matrix[actual]
    return dict({key: ref[key] for key in IDENTITY}, forecast=dict(old, **forecasts),
                prior_team_games=team['prior_team_games'], prior_shot_games=shot['prior_shot_games'],
                comparators_agree=controls_agree, paired_inputs=ready, first_pool=first_pool, third_pool=third_pool,
                first_pool_contains_actual=exact in first_pool, third_pool_contains_actual=exact in ordered + third_pool,
                original_rank=published_rank(matrix, actual, ordered),
                original_p_first=round(float(matrix[cell(ordered[0])]), 12),
                original_p_third=round(float(matrix[cell(ordered[2])]), 12),
                original_p_actual=round(float(probability), 12),
                actual_direction_in_original_three=any(base.RESULT[cell(score)] == base.RESULT[actual] for score in ordered))


def changed(rows, arm):
    return dict(first=sum(r['forecast'][arm]['primary'] != r['forecast']['r13']['primary'] for r in rows),
                three=sum(set(r['forecast'][arm]['top3']) != set(r['forecast']['r13']['top3']) for r in rows),
                sorted_three=sum(r['forecast'][arm]['top3'] != r['forecast']['r13']['top3'] for r in rows))


def diagnose(rows):
    ranks = Counter(row['original_rank'] for row in rows)
    misses = [row for row in rows if row['original_rank'] > 3]
    first_gaps = [row['original_p_first'] - row['original_p_actual'] for row in rows if row['original_rank'] > 1]
    third_gaps = [row['original_p_third'] - row['original_p_actual'] for row in misses]
    return dict(fixtures=len(rows), inputs_paired=sum(row['paired_inputs'] for row in rows),
                comparators_agree=sum(row['comparators_agree'] for row in rows),
                first_gap_pool_can_switch=sum(len(row['first_pool']) > 1 for row in rows),
                third_gap_pool_can_switch=sum(bool(row['third_pool']) for row in rows),
                actual_ranks=dict(first=ranks[1], second_or_third=ranks[2]+ranks[3],
                                  fourth_to_sixth=sum(ranks[i] for i in range(4, 7)),
                                  seventh_or_lower=sum(row['original_rank'] >= 7 for row in rows)),
                hindsight_first_hits=sum(row['first_pool_contains_actual'] for row in rows),
                hindsight_three_hits=sum(row['third_pool_contains_actual'] for row in rows),
                original_three_misses_with_actual_result_in_list=sum(row['actual_direction_in_original_three'] for row in misses),
                missed_median_first_probability_gap=round(float(np.median(first_gaps)), 9) if first_gaps else None,
                missed_median_third_probability_gap=round(float(np.median(third_gaps)), 9) if third_gaps else None,
                mean_original_first_prob=round(float(np.mean([r['original_p_first'] for r in rows])), 9))


def grade(folds):
    validation = folds['validation']
    trials = {}
    for arm in ARMS:
        results = compare(validation, baseline='r13', candidate=arm)
        trials[arm] = dict(change=changed(validation, arm), score={m: results['paired'][m]['net_hits'] for m in SCORES})
    eligible = [arm for arm in ARMS if trials[arm]['change']['sorted_three']
                and min(trials[arm]['score'].values()) >= 0]
    selected = min(eligible, key=lambda arm: (-trials[arm]['score']['score_top1'],
                                              -trials[arm]['score']['score_top3'], ARMS.index(arm)), default='r13')
    evaluation = folds['evaluation']
    chosen = [dict(row, forecast=dict(row['forecast'], selected=row['forecast'][selected])) for row in evaluation]
    comparison = compare(chosen, baseline='r13', candidate='selected')
    gains = [comparison['paired'][key]['net_hits'] for key in SCORES]
    return dict(selected=selected, diagnostics={name: diagnose(rows) for name, rows in folds.items()}, validation=trials,
                evaluation=comparison, controls={arm: compare(chosen, baseline=arm, candidate='selected')
                                                 for arm in ('market', 'r7', 'r9', 'r10')},
                evaluation_policies={arm: compare(evaluation, baseline='r13', candidate=arm) for arm in ARMS},
                evaluation_changes={arm: changed(evaluation, arm) for arm in ARMS},
                forward_screen_passed=selected != 'r13' and min(gains) >= 0 and max(gains) > 0)


def run(data, out):
    protocol = control.read(PROTOCOL)
    if sha(locked_grids.PROTOCOL) != protocol['r30_protocol_sha256']:
        raise ValueError('locked comparison dates differ')
    locked = control.read(locked_grids.PROTOCOL)
    summary = {}
    ledger = {name: [] for name in locked['windows']}
    with TemporaryDirectory(prefix='close-score-agreement-') as path:
        root = Path(path)
        locked_grids.unpack(data, locked['archives']['r13'], root/'r13')
        for name, spec in protocol['archives'].items():
            locked_grids.unpack(data, spec, root/name)
        for code in locked['competitions']:
            study, signed = root/code, root/'r13'/code
            for arm in ('panel', 'r7'):
                locked_grids.unpack(data, locked['archives'][code][arm], study/arm)
            records, grids = locked_grids.original_grids(study/'panel/panel.jsonl', control.read(study/'r7/main/fit.json'),
                control.read(signed/'fit.json'), reference_rows(signed/'forecasts.jsonl.gz'), code, locked)
            evidence = {}
            for name in ('r9', 'r10'):
                folder = root/name/'main'/code
                fit = control.read(folder/'fit.json')
                if fit['competition'] != code or fit['validation_through'] >= locked['windows']['train']['from']:
                    raise ValueError('evidence model used target dates or a different competition')
                evidence[name] = reference_rows(folder/'forecasts.jsonl.gz')
            rows = [record(ref, matrix, evidence['r9'][ref['id']], evidence['r10'][ref['id']])
                    for ref, matrix in zip(records, grids, strict=True)]
            folds = {name: [row for row in rows if span['from'] <= row['date'] < span['before']]
                     for name, span in locked['windows'].items()}
            if any(len(group) != locked['expected_per_window'][code] for group in folds.values()):
                raise ValueError('incomplete signed league-phase sample: ' + code)
            summary[code] = grade(folds)
            for name, group in folds.items():
                ledger[name].extend(group)
    out.mkdir(parents=True, exist_ok=False)
    write_new(out/'summary.json', dict(version=protocol['version'], protocol_sha256=sha(PROTOCOL), competitions=summary))
    for name, rows in ledger.items():
        archive.put(out/f'{name}.jsonl.gz', rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    run(args.data, args.out)
