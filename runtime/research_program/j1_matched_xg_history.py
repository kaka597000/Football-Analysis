"""Replay prior-date J1 chance creation and goals on identical retained matches."""

import argparse
from collections import defaultdict, deque
from itertools import groupby
from math import isfinite
from pathlib import Path

from research.analysis_freeze import aware
from . import continuation_scores as r7
from . import continuation_study as archive
from . import j1_process_forward as r28
from . import j1_score_dispersion as r29
from . import j1_team_allocation as j1
from . import r8_score_study as controls
from .goal_state_study import read_lines, sha, write_new
from .j1_process_at_fixed_total import match_goal_totals
from .paired_kpi_inference import compare
from .ucl_zero_goals import quality, selection


PROTOCOL = Path(__file__).resolve().parents[2] / 'research/20261127-r48-j1-matched-xg-history/protocol.json'
SIDES = ('home', 'away')
ARMS = ('goals', 'xg', 'goals_fixed_total', 'xg_fixed_total', 'goals_at_capture', 'xg_at_capture',
        'xg_later_source', 'xg_fixed_total_later_source')
PAIRS = (('goals', 'xg'), ('goals_fixed_total', 'xg_fixed_total'), ('goals_at_capture', 'xg_at_capture'))


def observations(path, pin, panel):
    if sha(path) != pin['sha256']:
        raise ValueError('retained J1 process evidence differs')
    original = {row['id']: row for row in panel}
    rows = list(read_lines(path))
    by_id = {row['id']: row for row in rows}
    if len(rows) != len(by_id) or len(by_id) != pin['records']:
        raise ValueError('expected each recovered FotMob record once')
    for mid, row in by_id.items():
        game = original[mid]
        keys = ('date', 'home', 'away', 'home_goals', 'away_goals', 'stage', 'score_basis', 'competition')
        if (any(row[key] != game[key] for key in keys) or row['stage'] != j1.STAGE
                or row['result_source_sha256'] != game['source_sha256']
                or any(type(rate) not in (int, float) or not isfinite(rate) or rate < 0 for rate in row['xg'])
                or len(row['xg']) != 2):
            raise ValueError('source differs from the signed R5 fixture or xG contract: ' + mid)
    return by_id


def histories(first, second, records, *, captured_before=None):
    """Pair an attacking team's shots with its opponent's allowed chances."""
    output = []
    for attack, defense in ((first, second), (second, first)):
        seen = {}
        for team, facing in ((attack, False), (defense, True)):
            for game, side in team:
                row = records.get(game)
                if row is None:
                    continue
                stamp = row['observed_at']
                if captured_before is not None and (stamp is None or aware(stamp) >= captured_before):
                    continue
                scoring = 1 - side if facing else side
                seen[game, scoring] = (row[SIDES[scoring] + '_goals'], row['xg'][scoring])
        output.append(list(seen.values()))
    return output


def process(prior, samples):
    if not any(samples):
        return prior, prior
    arms, _ = r28.process_arms(prior, samples)
    return arms['goals_history'], arms['xg_history']


def fixed(prior, shifted):
    return shifted if shifted is prior else match_goal_totals(prior, shifted)[0]


def evaluate(game, prior, original, samples, clocked, later, window):
    row, base_pick = r29.validated_reference(game, prior, window, original)
    goals, xg = process(prior, samples)
    goals_at_capture, xg_at_capture = process(prior, clocked)
    _, xg_later = process(prior, later) if later != samples else (goals, xg)
    matrices = dict(r7=prior, goals=goals, xg=xg, goals_fixed_total=fixed(prior, goals),
                    xg_fixed_total=fixed(prior, xg), goals_at_capture=goals_at_capture, xg_at_capture=xg_at_capture,
                    xg_later_source=xg_later, xg_fixed_total_later_source=fixed(prior, xg_later))
    forecasts = {name: selection(grid, prior, base_pick) for name, grid in matrices.items()}
    return dict(row, forecast=dict(forecasts, market=original['forecast']['market']),
                quality={arm: quality(grid, forecasts[arm], tuple(row['actual'])) for arm, grid in matrices.items()},
                exposures=[len(pair) for pair in samples], captured_exposures=[len(pair) for pair in clocked])


def summaries(rows):
    report = r29.summarize(rows, ARMS)
    for control, arm in PAIRS:
        report['paired'][arm][control] = compare(rows, baseline=control, candidate=arm)['paired']
    revisions = {}
    for control, arm in (('xg', 'xg_later_source'), ('xg_fixed_total', 'xg_fixed_total_later_source')):
        report['paired'][arm][control] = compare(rows, baseline=control, candidate=arm)['paired']
        picks = [(row['forecast'][control], row['forecast'][arm]) for row in rows]
        revisions[arm] = dict(first=sum(a['primary'] != b['primary'] for a, b in picks),
                              three_order=sum(a['top3'] != b['top3'] for a, b in picks),
                              three_members=sum(set(a['top3']) != set(b['top3']) for a, b in picks))
    report['changes_from_later_raw_xg'] = revisions
    report['process_coverage'] = {
        'earlier_match_history': sum(any(row['exposures']) for row in rows),
        'earlier_hash_bound_capture': sum(any(row['captured_exposures']) for row in rows),
        'both_scoring_sides': sum(all(row['exposures']) for row in rows),
    }
    return report


def replay(data, spec, out):
    if sha(j1.PROTOCOL) != spec['r25_protocol_sha256'] or sha(r28.PROTOCOL) != spec['r28_protocol_sha256']:
        raise ValueError('original J1 populations or chance-creation recipe changed')
    shared = controls.read(j1.PROTOCOL)
    panel, signed, settings = r29.load_inputs(data, shared)
    pin = spec['observations']
    source = observations(data / pin['path'], pin, panel)
    alternate = spec['alternate']
    replacement = observations(data / alternate['path'], alternate, panel)
    if not replacement.keys() <= source.keys() or any(source[mid]['xg'] == row['xg'] for mid, row in replacement.items()):
        raise ValueError('the later xG variant must refer to a genuine retained revision')
    revised = source | replacement
    anchors, features, _ = r7.inputs(panel, j1.COMPETITION)
    example_indices = j1.match_rows(panel, shared)['early_2026_27']
    original_priors = controls.matrices(anchors[example_indices], features[example_indices], settings)
    histories_by_team = defaultdict(lambda: deque(maxlen=5))
    groups = {name: [] for name in spec['windows']}
    joined = zip((panel[i] for i in example_indices), original_priors, strict=True)
    for day, objects in groupby(joined, key=lambda pair: pair[0]['date']):
        window, = [name for name, spec_row in spec['windows'].items()
                   if spec_row['from'] <= day < spec_row['before']]
        cutoff = aware(day + 'T00:00:00+00:00')
        fixtures = list(objects)
        for game, prior in fixtures:
            home, away = (histories_by_team[game[side]] for side in SIDES)
            samples = histories(home, away, source)
            clocked = histories(home, away, source, captured_before=cutoff)
            latest = histories(home, away, revised)
            groups[window].append(evaluate(game, prior, signed[game['id']], samples, clocked, latest, window))
        for game, _ in fixtures:
            for side_index, side in enumerate(SIDES):
                histories_by_team[game[side]].append((game['id'], side_index))
    if any(len(rows) != spec['windows'][name]['fixtures'] for name, rows in groups.items()):
        raise ValueError('a complete original J1 date slice must be retained')
    all_rows = [row for rows in groups.values() for row in rows]
    report = {name: summaries(rows) for name, rows in (*groups.items(), ('all', all_rows))}
    record = dict(version=spec['version'], protocol_sha256=sha(PROTOCOL),
                  observations=len(source), hash_bound_captures=sum(row['observed_at'] is not None for row in source.values()),
                  distinct_later_sources=sorted(replacement), windows=report)
    out.mkdir(parents=True, exist_ok=False)
    archive.put(out / 'forecasts.jsonl.gz', all_rows)
    write_new(out / 'metrics.json', record)


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--data', type=Path, required=True)
    cli.add_argument('--out', type=Path, required=True)
    args = cli.parse_args()
    replay(args.data, controls.read(PROTOCOL), args.out)
