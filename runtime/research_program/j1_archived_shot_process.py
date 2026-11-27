"""Replay the recovered J1 shots-on-target history against the signed R7 scores."""

import argparse
from datetime import date, timezone
from pathlib import Path

from research.analysis_freeze import aware
from . import continuation_report as report
from . import continuation_scores as r7
from . import continuation_study as archive
from . import j1_score_dispersion as r29
from . import j1_team_allocation as j1
from . import r10_shot_model as model
from . import r8_score_study as controls
from .goal_state_study import read_lines, sha, write_new
from .j1_process_at_fixed_total import match_goal_totals
from .ucl_zero_goals import quality, selection


PROTOCOL = Path(__file__).resolve().parents[2] / 'research/20261127-r47-j1-archived-shot-process/protocol.json'
ARMS = ('r7', 'rate_half', 'rate_full', 'rate_and_covariance', 'rate_fixed_total')


def shot_history(path, pin, games):
    if sha(path) != pin['sha256']:
        raise ValueError('the archived paired shots changed')
    observations = {row['id']: row for row in read_lines(path)}
    if len(observations) != pin['records']:
        raise ValueError('the recovered shot population changed')
    panel = {game['id']: game for game in games}
    for mid, row in observations.items():
        game = panel[mid]
        if (game['stage'] != j1.STAGE or game['score_basis'] != 'regulation_90'
                or game['source_sha256'] != row['result_source_sha256']
                or [game['home_goals'], game['away_goals']] != row['actual']
                or model.observation(row, game) is None
                or not aware(row['kickoff_utc']) < aware(row['source_observed_at'])):
            raise ValueError('shot source no longer matches subject, label or observations for ' + mid)
    return observations


def windows(games, registrations):
    indices = {}
    for name, bounds in registrations.items():
        rows = [i for i, game in enumerate(games) if game['stage'] == j1.STAGE
                and bounds['from'] <= game['date'] < bounds['before']]
        if len(rows) != bounds['fixtures']:
            raise ValueError('the registered J1 calendar changed for ' + name)
        indices[name] = rows
    return indices


def variants(prior, context, fitted):
    rate, covariance = model.alternatives(prior, context, fitted)
    fixed = prior if rate is prior else match_goal_totals(prior, rate)[0]
    return dict(r7=prior, rate_half=model.blend(prior, rate, .5), rate_full=rate,
                rate_and_covariance=covariance, rate_fixed_total=fixed)


def trial_counts(rows):
    trials = {}
    for arm in ARMS:
        counts = report.rates(rows, arm)['counts']
        trials[arm] = [counts[key][0] for key in r29.SCORE_METRICS]
    return trials


def choose(trials):
    acceptable = [arm for arm in ARMS if all(a >= b for a, b in zip(trials[arm], trials['r7'], strict=True))]
    return min(acceptable, key=lambda arm: (-trials[arm][0], -trials[arm][1], ARMS.index(arm)))


def replay(data, spec):
    if sha(j1.PROTOCOL) != spec['archives']['r25_protocol_sha256']:
        raise ValueError('the original historical populations changed')
    shared = controls.read(j1.PROTOCOL)
    games, signed, settings = r29.load_inputs(data, shared)
    splits = windows(games, spec['windows'])
    pin = spec['archives']['shots']
    observed = shot_history(data / pin['path'], pin, games)
    latest = max(aware(row['source_observed_at']) for row in observed.values()).astimezone(timezone.utc)
    if latest.date() >= date.fromisoformat(spec['windows']['holdout']['from']):
        raise ValueError('the recovered source was not observed before the holdout dates')
    contexts, shots, _ = model.walk(games, observed, j1.COMPETITION)
    anchors, features, _ = r7.inputs(games, j1.COMPETITION)
    indices = [i for group in splits.values() for i in group]
    priors = dict(zip(indices, controls.matrices(anchors[indices], features[indices], settings), strict=True))
    train = splits['fit']
    fitted = model.fit([priors[i] for i in train], [contexts[i] for i in train], [shots[i] for i in train],
                       [(games[i]['home_goals'], games[i]['away_goals']) for i in train])

    def records(split):
        rows = []
        for i in splits[split]:
            game, prior, context = games[i], priors[i], contexts[i]
            ref = signed[game['id']]
            row, original = r29.validated_reference(game, prior, split, ref)
            matrices = variants(prior, context, fitted)
            picks = {arm: selection(grid, prior, original) for arm, grid in matrices.items()}
            rows.append(dict(row, forecast=dict(picks, market=ref['forecast']['market']),
                             quality={arm: quality(grid, picks[arm], tuple(row['actual']))
                                      for arm, grid in matrices.items()},
                             history_ready=context['ready'], history_exposure=context['exposure'],
                             paired_shots_for_later_dates=shots[i] is not None))
        return rows

    validation = records('validation')
    trials = trial_counts(validation)
    selected = choose(trials)
    holdout = records('holdout')
    metrics = {}
    for name, rows in (('validation', validation), ('holdout', holdout)):
        for row in rows:
            for field in ('forecast', 'quality'):
                row[field]['candidate'] = row[field][selected]
        metrics[name] = r29.summarize(rows, ('candidate', *ARMS[1:]))
        metrics[name]['history_ready'] = sum(row['history_ready'] for row in rows)
        metrics[name]['same_match_observed_for_future_dates'] = sum(row['paired_shots_for_later_dates'] for row in rows)
        metrics[name]['history_unavailable'] = len(rows) - metrics[name]['history_ready']
    pair = metrics['holdout']['paired']['candidate']['r7']
    net = [pair[key]['net_hits'] for key in r29.SCORE_METRICS]
    fit_record = dict(version=spec['version'], protocol_sha256=sha(PROTOCOL), windows=spec['windows'], fit=fitted,
                      paired_matches_in_fit_window=sum(shots[i] is not None for i in train),
                      latest_observed_at=latest.isoformat(), validation_counts=trials, selected_arm=selected,
                      forward_eligible=selected != 'r7' and min(net) >= 0 and max(net) > 0)
    return fit_record, metrics, validation, holdout


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--replay', type=Path, required=True)
    args = parser.parse_args()
    spec = controls.read(PROTOCOL)
    fit_record, metrics, validation, holdout = replay(args.data, spec)
    args.replay.mkdir(parents=True, exist_ok=False)
    write_new(args.replay / 'fit.json', fit_record)
    write_new(args.replay / 'metrics.json', metrics)
    archive.put(args.replay / 'validation.jsonl.gz', validation)
    archive.put(args.replay / 'forecasts.jsonl.gz', holdout)
