"""Compare season chance creation and scored goals at fixed J1 goal totals."""

import argparse
import gzip
from hashlib import sha256
import json
from pathlib import Path
import tarfile

import numpy as np

from research.analysis_freeze import DEFAULT_ROOT, aware, digest
from . import all_scope_model as base
from . import continuation_scores as r7
from . import continuation_study as archive
from . import j1_first_consensus as r43
from . import j1_fixed_first_backups as r42
from . import j1_process_forward as r28
from . import j1_score_dispersion as r29
from . import j1_team_allocation as j1
from . import r8_score_study as controls
from .goal_state_study import sha, write_new
from .j1_process_at_fixed_total import match_goal_totals
from .paired_kpi_inference import compare
from .ucl_zero_goals import quality, selection


PROTOCOL = Path(__file__).resolve().parents[2] / 'research/20261127-r49-j1-season-chance-rates/protocol.json'
ARMS = ('source_goals', 'regulation_goals', 'xg')
SCHEDULE_SHA = 'c986c90b61ed364a403ebf2ead6fee34ab9e0a45e626ef0de7fd3633a4003d80'


def pinned(data, ref):
    path = data / ref['path']
    if sha(path) != ref['sha256']:
        raise ValueError('registered Football LAB evidence differs')
    return controls.read(path)


def fixed_matrices(prior, home_id, away_id, snapshot):
    roster = snapshot['teams']
    if home_id not in roster or away_id not in roster:
        return {arm: prior for arm in ('r7', *ARMS)}, None
    home, away = roster[home_id], roster[away_id]
    n = min(home['played'], away['played'])
    expected = np.array([(prior * cells).sum() for cells in (base.H, base.A)])
    if n <= 0 or np.any(expected <= 0.):
        raise ValueError('positive team exposure and base expected goals required')
    matrices = {'r7': prior}
    for arm in ARMS:
        h, a = (np.asarray(club[arm], dtype=float) for club in (home, away))
        if arm == 'regulation_goals':
            h, a = h / home['played'], a / away['played']
        mean = .5 * np.array([h[0] + a[1], a[0] + h[1]])
        adjusted = (n * mean + r28.PRIOR_MATCHES * expected) / (n + r28.PRIOR_MATCHES)
        rates = np.clip(adjusted / expected, 1. / r28.RATIO_LIMIT, r28.RATIO_LIMIT)
        weighted = prior * np.exp(base.H * np.log(rates[0]) + base.A * np.log(rates[1]))
        matrices[arm] = match_goal_totals(prior, weighted)[0]
    return matrices, n


def archive_schedule(data):
    root = data / 'workspace-updates/20261123-all-scope-kpi/evidence'
    ref = controls.read(root / 'source-index.json')[SCHEDULE_SHA]
    with tarfile.open(root / ref['archive']) as packed:
        body = gzip.decompress(packed.extractfile(ref['member']).read())
    if sha256(body).hexdigest() != SCHEDULE_SHA:
        raise ValueError('the original J1 fixture clock archive differs')
    return {'espn:' + event['id']: aware(event['date']) for event in json.loads(body)['events']}


def replay(data, snapshots, out):
    shared = controls.read(j1.PROTOCOL)
    panel, signed, settings = r29.load_inputs(data, shared)
    indices = j1.match_rows(panel, shared)['early_2026_27']
    anchors, features, _ = r7.inputs(panel, j1.COMPETITION)
    grids = controls.matrices(anchors[indices], features[indices], settings)
    clocks = archive_schedule(data)
    groups = {'previous_season': [], 'archived_pregame': []}
    archived = snapshots['archived']
    for i, prior in zip(indices, grids, strict=True):
        game = panel[i]
        ref = signed[game['id']]
        prior_season = game['date'] < archived['before']
        name = 'previous_season' if prior_season else 'archived_pregame'
        snap = snapshots['previous'] if prior_season else archived
        if not prior_season and not aware(snap['captured_at']) < clocks[game['id']]:
            raise ValueError('archived table follows the historical fixture cutoff')
        row, original = r29.validated_reference(game, prior, name, ref)
        matrices, n = fixed_matrices(prior, row['home'], row['away'], snap)
        picks = {arm: selection(grid, prior, original) for arm, grid in matrices.items()}
        groups[name].append(dict(row, matched_team_games=n, forecast=dict(picks, market=ref['forecast']['market']),
                                 quality={arm: quality(grid, picks[arm], tuple(row['actual']))
                                          for arm, grid in matrices.items()}))
    if [len(rows) for rows in groups.values()] != [140, 10]:
        raise ValueError('the published 140 and 10 match windows changed')
    summaries = {}
    for name, rows in groups.items():
        item = r29.summarize(rows, ARMS)
        item['covered'] = sum(row['matched_team_games'] is not None for row in rows)
        for arm in ('source_goals', 'regulation_goals'):
            item['paired']['xg'][arm] = compare(rows, baseline=arm, candidate='xg')['paired']
        summaries[name] = item
    out.mkdir(parents=True, exist_ok=False)
    write_new(out / 'metrics.json', dict(protocol_sha256=sha(PROTOCOL), windows=summaries))
    archive.put(out / 'forecasts.jsonl.gz', [row for rows in groups.values() for row in rows])


def prepare_forward(data, spec, paths, snapshot):
    parent, originals, priors, _ = r43.forward_inputs(paths, spec)
    teams = pinned(data, spec['team_ids'])['jpn.1']
    capture = aware(snapshot['captured_at'])
    deadline = min(aware(ref['fixture']['kickoff_at']) for _, ref, _, _ in originals)
    if (snapshot['season'] != '2026-27' or snapshot['fixtures'] != 157 or capture >= deadline
            or set(snapshot['teams']) != {team['espn_name'] for team in teams.values()}):
        raise ValueError('current table or schedule no longer matches the registered J1 comparison')
    rows = {}
    for (mid, ref, original, saved), prior in zip(originals, priors, strict=True):
        fixture = ref['fixture']
        home, away = (teams[fixture[side]]['espn_name'] for side in ('home', 'away'))
        matrices, n = fixed_matrices(prior, home, away, snapshot)
        picks = {}
        for arm, grid in matrices.items():
            chosen = selection(grid, prior, original)
            cell = tuple(map(int, chosen['primary'].split('-')))
            picks[arm] = dict(chosen, first_probability=float(grid[cell]))
        rows[mid] = dict(fixture=fixture, line=ref['line'], teams=[home, away], matched_team_games=n,
                         source_r42_sha256=saved['record_sha256'], source_table_sha256=snapshot['source_sha256'],
                         forecasts=dict(picks, market=ref['forecasts']['market'],
                                        comprehensive=ref['forecasts']['comprehensive']),
                         matrices={arm: grid.tolist() for arm, grid in matrices.items()})
    cutoff = max(capture, *(aware(saved['recorded_at']) for _, _, _, saved in originals))
    identity = dict(version=spec['version'], protocol_sha256=sha(PROTOCOL), competitions=[j1.COMPETITION],
                    parent_sha256=parent['record_sha256'], source_cutoff_at=cutoff.isoformat(),
                    source_table_sha256=snapshot['source_sha256'], team_fixture_count=snapshot['fixtures'],
                    selected={arm: r28.ranked(rows, arm) for arm in ('r7', *ARMS)},
                    pairs={f'xg_vs_{arm}': [arm, 'xg'] for arm in
                           ('r7', 'source_goals', 'regulation_goals', 'market', 'comprehensive')},
                    forecast_sha256={mid: digest(row) for mid, row in rows.items()})
    return dict(identity=identity, rows=rows)


def comparison(prepared):
    rows = [dict(match_id=mid, teams=row['teams'], kickoff=row['fixture']['kickoff_at'],
                 matched_team_games=row['matched_team_games'],
                 scores={arm: row['forecasts'][arm]['top3'] for arm in ('r7', *ARMS)})
            for mid, row in prepared['rows'].items()]
    movements = {}
    for arm in ('r7', 'source_goals', 'regulation_goals'):
        movements[arm] = dict(first=sum(row['scores'][arm][0] != row['scores']['xg'][0] for row in rows),
                              ordered_three=sum(row['scores'][arm] != row['scores']['xg'] for row in rows),
                              three_members=sum(set(row['scores'][arm]) != set(row['scores']['xg']) for row in rows))
    return dict(fixtures=len(rows), movements=movements, rows=rows,
                selected=prepared['identity']['selected'], identity_sha256=digest(prepared['identity']))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    modes = parser.add_mutually_exclusive_group(required=True)
    for name in ('replay', 'check', 'issue', 'verify'):
        modes.add_argument('--' + name, type=Path)
    parser.add_argument('--freeze-root', type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    spec = controls.read(PROTOCOL)
    paths = r43.locked_archives(args.data, spec, (('forward', r42.PROTOCOL),))
    snapshots = pinned(args.data, spec['snapshots'])
    if args.replay:
        replay(args.data, snapshots, args.replay)
    else:
        prepared = prepare_forward(args.data, spec, paths, snapshots['current'])
        audit = comparison(prepared)
        if args.check:
            write_new(args.check, audit)
        else:
            if not audit['movements']['source_goals']['ordered_three']:
                raise ValueError('no distinct season xG or recorded-goals forecast to freeze')
            if args.issue:
                r42.issue_forward(prepared, args.issue, spec['track'], args.freeze_root)
            r42.verify_forward(prepared, args.issue or args.verify, spec['track'])
