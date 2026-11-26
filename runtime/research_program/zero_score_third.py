"""Replay a bounded zero-goal backup on signed score grids, without refitting."""

import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from . import all_scope_model as base
from . import all_scope_study as study
from . import continuation_scores as r7
from . import continuation_study as archive
from . import europe_score_dispersion as europe
from . import j1_team_allocation as j1
from . import r8_score_study as control
from . import score_coverage_policy as coverage
from .goal_state_study import sha, write_new
from .paired_kpi_inference import compare
from .ucl_zero_goals import IDENTITY, reference_rows


PROTOCOL = Path(__file__).resolve().parents[2] / 'research/20261126-r40-zero-score-third/protocol.json'
WINDOWS = ('earlier', 'later')
PATHS = ('home_zero', 'away_zero')


def record(ref, grid):
    original = ref['forecast']['candidate']
    ordered = original['top3']
    proposal = coverage.select_scores([grid], primary=original['primary'], policy='diverse', mass_budget=.015)
    matches = proposal['reference_scores'] == ordered
    cells = [tuple(map(int, score.split('-'))) for score in ordered]
    no_zero = [path for axis, path in enumerate(PATHS) if all(cell[axis] > 0 for cell in cells)]
    paths = proposal['path_evidence']
    added = [path for path in no_zero if paths[path]['supported'] and paths[path]['covered_in_selection']]
    picks = [proposal['primary'], *proposal['alternates']] if matches and added else ordered
    if picks[:2] != ordered[:2]:
        raise ValueError('zero-goal rule changed the first score or the first backup')
    can_compare = [path for path in no_zero if paths[path]['supported']]
    zero_misses = [path for axis, path in enumerate(PATHS) if path in no_zero and ref['actual'][axis] == 0]
    zero_probability = {path: paths[path]['probability'] for path in PATHS}
    excluded_probability = sum(zero_probability[path] for path in no_zero)
    if len(no_zero) == 2:
        excluded_probability -= grid[0, 0]
    return dict({key: ref[key] for key in IDENTITY},
                forecast=dict(original=original, market=ref['forecast']['market'], third=dict(original, top3=picks)),
                grid_matches_order=matches, uncovered_zero_paths=no_zero, supported_uncovered_zero_paths=can_compare,
                actual_zero_paths_excluded=zero_misses, added_paths=added if picks != ordered else [],
                path_probability={path: round(p, 12) for path, p in zero_probability.items()},
                probability_of_any_excluded_zero=round(float(excluded_probability), 12),
                probability_cost=round(proposal['pooled_probability_loss'], 12) if picks != ordered else 0.)


def european_history(data, root):
    spec = control.read(europe.PROTOCOL)
    europe.unpack(data, spec['archives']['r13'], root / 'r13')
    for code in spec['competitions']:
        folder, signed = root / code, root / 'r13' / code
        for item in ('panel', 'r7'):
            europe.unpack(data, spec['archives'][code][item], folder / item)
        refs, grids = europe.original_grids(folder / 'panel/panel.jsonl', control.read(folder / 'r7/main/fit.json'),
            control.read(signed / 'fit.json'), reference_rows(signed / 'forecasts.jsonl.gz'), code, spec)
        histories = {}
        for name, window in zip(WINDOWS, ('validation', 'evaluation'), strict=True):
            bounds = spec['windows'][window]
            histories[name] = [record(ref, grid) for ref, grid in zip(refs, grids, strict=True)
                               if bounds['from'] <= ref['date'] < bounds['before']]
        yield code, histories


def j1_history(data, root):
    spec = control.read(j1.PROTOCOL)
    for item in ('r5_panel', 'r7_fit'):
        control.extract(j1.locked_path(data, spec, item), root / item)
    panel, fit = root / 'r5_panel/panel.jsonl', root / 'r7_fit/main'
    settings = control.read(fit / 'fit.json')
    if (settings['competition'] != j1.COMPETITION or settings['source_panel_sha256'] != sha(panel)
            or settings['training_through'] >= spec['windows']['validation']['from']
            or settings['bounds'][1] != spec['windows']['validation']['from']
            or settings['bounds'][2] != spec['windows']['early_2026_27']['from']):
        raise ValueError('signed J1 recipe, history, or cutoff differs')
    games = study.check_panel(panel, j1.COMPETITION)
    signed = reference_rows(fit / 'forecasts.jsonl.gz')
    windows = j1.match_rows(games, spec)
    anchors, features, _ = r7.inputs(games, j1.COMPETITION)
    histories = {}
    for name, history in zip(WINDOWS, j1.HISTORY, strict=True):
        idx = windows[history]
        grids = control.matrices(anchors[idx], features[idx], settings)
        rows = []
        for i, grid in zip(idx, grids, strict=True):
            game = games[i]
            actual = [game['home_goals'], game['away_goals']]
            ref = signed[game['id']]
            observed = dict(game, cohort=j1.COMPETITION, actual=actual, line=base.actual_line(game))
            quality = control.quality(grid, actual)
            if (any(ref[key] != observed[key] for key in IDENTITY)
                    or base.pick(grid, ref['line']) != ref['forecast']['candidate']
                    or any(not np.allclose(value, ref['quality']['candidate'][key], rtol=0., atol=2e-8)
                           for key, value in quality.items())):
                raise ValueError('signed J1 forecast, grid, line or result differs: ' + game['id'])
            rows.append(record(ref, grid))
        histories[name] = rows
    return j1.COMPETITION, histories


def summarize(rows):
    omitted = [r['path_probability'][path] for r in rows for path in r['uncovered_zero_paths']]
    return dict(fixtures=len(rows), grid_agrees=sum(r['grid_matches_order'] for r in rows),
                any_uncovered_zero=sum(bool(r['uncovered_zero_paths']) for r in rows),
                supported_uncovered_zero=sum(bool(r['supported_uncovered_zero_paths']) for r in rows),
                realized_zero_excluded=sum(bool(r['actual_zero_paths_excluded']) for r in rows),
                expected_excluded_zero=round(sum(r['probability_of_any_excluded_zero'] for r in rows), 6),
                max_omitted_zero_probability=max(omitted, default=None),
                swapped=sum(bool(r['added_paths']) for r in rows),
                max_swap_probability_cost=max(r['probability_cost'] for r in rows),
                original=compare(rows, baseline='original', candidate='third'),
                market=compare(rows, baseline='market', candidate='third'))


def run(data, out):
    spec = control.read(PROTOCOL)
    for path, name in ((europe.PROTOCOL, 'europe_source_protocol'), (j1.PROTOCOL, 'j1_source_protocol'),
                       (Path(coverage.__file__), 'coverage_policy')):
        if sha(path) != spec[name + '_sha256']:
            raise ValueError('a preregistered input or selection rule differs: ' + name)
    with TemporaryDirectory(prefix='r40-zero-score-') as folder:
        root = Path(folder)
        scopes = dict(european_history(data, root))
        code, histories = j1_history(data, root)
        scopes[code] = histories
    if {code: {name: len(rows) for name, rows in windows.items()} for code, windows in scopes.items()} != spec['windows']:
        raise ValueError('missing registered competition or historical fixture coverage')
    results = {}
    for code, windows in scopes.items():
        metrics = {name: summarize(rows) for name, rows in windows.items()}
        earlier, later = (metrics[name]['original']['paired']['score_top3']['net_hits'] for name in WINDOWS)
        market = metrics['later']['market']['paired']['score_top3']
        results[code] = dict(windows=metrics, eligible_for_prospective_research=(
            earlier >= 0 and later > 0 and market['eligible'] > 0 and market['net_hits'] >= 0))
    out.mkdir(parents=True, exist_ok=False)
    write_new(out / 'summary.json', dict(version=spec['version'], protocol_sha256=sha(PROTOCOL), competitions=results))
    for name in WINDOWS:
        archive.put(out / f'{name}.jsonl.gz', [row for code in sorted(scopes) for row in scopes[code][name]])


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--data', type=Path, required=True)
    cli.add_argument('--out', type=Path, required=True)
    args = cli.parse_args()
    run(args.data, args.out)
