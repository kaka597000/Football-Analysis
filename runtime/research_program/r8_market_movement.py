"""Isolate same-bookmaker historical 1X2 changes from a shared T-1h forecast."""

import argparse
from collections import Counter, defaultdict
from datetime import date
import json
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
from scipy.special import softmax

from . import all_scope_model as base
from . import all_scope_study as study
from . import continuation_scores as r7
from . import continuation_study as archive
from .goal_state_study import read_lines, sha, write_new
from .r8_score_study import matrices, quality, read, summarize

BOUNDS = ('2022-07-01', '2023-07-01', '2026-07-01')
HORIZONS = (24 * 3600, 3600)
WEIGHTS = (0, .25, .5, .75, 1)
PENALTY = 8.


def endpoints(points):
    """Use source-clock observations available at each horizon, without line changes."""
    groups = defaultdict(lambda: defaultdict(set))
    for point in points:
        lag = point['seconds_to_kickoff']
        prices = base._prices(point['odds'], base.SIDES)
        if (type(lag) not in (int, float) or not np.isfinite(lag) or lag < HORIZONS[-1]
                or not prices or point['market'] != '1x2' or point.get('line') is not None):
            continue
        group = point['source'], point['bookmaker']
        if not all(isinstance(part, str) and part.strip() for part in group):
            continue
        groups[group][lag].add(prices)
    current, paired = [], []
    for _, history in sorted(groups.items()):
        near = min(history)
        if len(history[near]) != 1:
            continue
        near_prob = np.array(base._wdl(next(iter(history[near]))))
        current.append(near_prob)
        eligible = [lag for lag in history if lag >= HORIZONS[0]]
        if eligible:
            early = min(eligible)
            if early > near and len(history[early]) == 1:
                early_prob = np.array(base._wdl(next(iter(history[early]))))
                paired.append(near_prob - early_prob)
    return (np.mean(current, axis=0) if current else None,
            np.mean(paired, axis=0) if paired else None, len(paired))


def joint_wdl(matrix):
    return np.bincount(base.RESULT.ravel(), weights=matrix.ravel(), minlength=3)


def fit_residual(features, prior, labels):
    mean = features.mean(axis=0)
    sd = features.std(axis=0)
    sd = np.where(sd > 1e-8, sd, 1.)
    x = np.column_stack((np.ones(len(features)), (features - mean) / sd))
    offset = np.log(np.maximum(prior, 1e-14))
    outcome = np.eye(3)[labels]

    def objective(flat):
        coefficients = flat.reshape(x.shape[1], 3)
        probs = softmax(offset + x @ coefficients, axis=1)
        loss = -np.log(np.maximum(probs[np.arange(len(labels)), labels], 1e-300)).sum()
        gradient = x.T @ (probs - outcome)
        return loss + PENALTY / 2 * (flat @ flat), gradient.ravel() + PENALTY * flat

    fitted = minimize(objective, np.zeros(x.shape[1] * 3), jac=True, method='L-BFGS-B',
                      options={'ftol': 1e-11})
    if not fitted.success:
        raise ValueError('market residual fit failed: '+str(fitted.message))
    return dict(mean=mean.tolist(), sd=sd.tolist(), coefficients=fitted.x.reshape(x.shape[1],3).tolist())


def infer(features, prior, fitted):
    x = np.column_stack((np.ones(len(features)), (features - fitted['mean']) / fitted['sd']))
    return softmax(np.log(np.maximum(prior, 1e-14)) + x @ fitted['coefficients'], axis=1)


def aligned_games(rows, observations):
    by_id = {row['id']: row for row in rows}
    points = defaultdict(list)
    for item in observations:
        game = by_id.get(item['id'])
        if game is None:
            raise ValueError('observation is outside its registered fixture panel')
        if any(item[k] != game[k] for k in ('competition','home','away','date')):
            raise ValueError('odds and fixture identity differ: '+game['id'])
        points[game['id']].append(item)
    return points


def run(panel, points_path, settings, code, out):
    rows = [r for r in study.check_panel(panel, code) if r['date'] < BOUNDS[2]]
    if (list(BOUNDS) != settings['bounds'] or code != settings['competition']
            or sha(panel) != settings['source_panel_sha256']):
        raise ValueError('the T-1h anchor must reproduce its registered panel and training windows')
    points = aligned_games(rows, read_lines(points_path))
    priors, features, _ = r7.inputs(rows, code)
    joint = matrices(priors, features, settings)
    probabilities = np.asarray([joint_wdl(matrix) for matrix in joint])
    clock = np.asarray([date.fromisoformat(game['date']).toordinal() for game in rows])
    first, second, _ = [date.fromisoformat(value).toordinal() for value in BOUNDS]
    learn, tune, test = (clock < first), ((clock >= first) & (clock < second)), (clock >= second)
    observed = [endpoints(points[g['id']]) for g in rows]
    legit = np.asarray([n > 0 for _,_,n in observed])
    has_late = np.asarray([p is not None for p,_,_ in observed])
    wdl = np.asarray([0 if g['home_goals'] > g['away_goals'] else
                      2 if g['home_goals'] < g['away_goals'] else 1 for g in rows])
    report = dict(competition=code, bounds=BOUNDS, panel_sha256=sha(panel),
                  odds_sha256=sha(points_path),
                  coverage={name: dict(games=int(mask.sum()), late=int((mask & has_late).sum()),
                                       two_distinct_times=int((mask & legit).sum()))
                            for name, mask in [('train',learn),('calibration',tune),('test',test)]})
    out.mkdir(parents=True, exist_ok=False)
    if not (learn & legit).any() or not (tune & legit).any() or not (test & legit).any():
        report['status'] = 'INSUFFICIENT_VERIFIED_TIME_WINDOWS'
        write_new(out/'fit.json', report)
        return report

    # Both arms use the same fixtures and T-1h estimates; only movement differs.
    contemporary = np.array([p if p is not None else np.zeros(3) for p,_,_ in observed])
    movement = np.array([p if p is not None else np.zeros(3) for _,p,_ in observed])
    x0 = contemporary
    x1 = np.column_stack((contemporary,movement))
    fits = [fit_residual(x[learn & legit], probabilities[learn & legit], wdl[learn & legit])
            for x in (x0,x1)]
    validation = tune & legit
    prior = probabilities[validation]
    adjusted = infer(x0[validation], prior, fits[0])
    labels = wdl[validation]
    indexes = np.arange(len(labels))
    weight = min(WEIGHTS, key=lambda w: -np.log(np.maximum(
        ((1-w)*prior+w*adjusted)[indexes,labels], 1e-300)).mean())
    def both_arms(mix):
        control = probabilities.copy()
        control[has_late] = ((1-mix)*probabilities[has_late]
            + mix*infer(x0[has_late], probabilities[has_late], fits[0]))
        experimental = control.copy()
        experimental[legit] = ((1-mix)*probabilities[legit]
            + mix*infer(x1[legit], probabilities[legit], fits[1]))
        return control, experimental

    base_prob, augmented = both_arms(weight)
    half_control, half_augmented = both_arms(.5)

    records = []
    for i in np.flatnonzero(test):
        game = rows[i]
        actual = [game['home_goals'],game['away_goals']]
        line = base.actual_line(game)
        forecast = {'market': archive.market_control(game, joint[i])}
        evidence = {}
        for name, target in (('r7', probabilities[i]), ('t1_only', base_prob[i]),
                             ('candidate', augmented[i]), ('fixed_half_t1_only', half_control[i]),
                             ('fixed_half_movement', half_augmented[i])):
            corrected = joint[i] * (target / probabilities[i])[base.RESULT]
            forecast[name] = base.pick(corrected, line)
            evidence[name] = quality(corrected, actual)
        records.append(dict(id=game['id'],cohort=code,date=game['date'],actual=actual,line=line,
            paired_books=observed[i][2],forecast=forecast,quality=evidence))
    archive.put(out/'predictions.jsonl.gz', records)
    report.update(status='MEASURED', coefficients=fits, common_mix=weight)
    write_new(out/'fit.json', report)
    from .continuation_report import paired
    paired_records = [r for r in records if r['paired_books']]
    write_new(out/'metrics.json', dict(all=summarize(records), paired_subset=summarize(paired_records),
          incremental_all=paired(records,'t1_only','candidate'),
          incremental_paired=paired(paired_records,'t1_only','candidate'),
          fixed_half_incremental_paired=paired(paired_records,'fixed_half_t1_only','fixed_half_movement')))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('panel','points','settings','out'):
        parser.add_argument('--'+key, required=True, type=Path)
    parser.add_argument('--code', required=True, choices=['eng.1','esp.1'])
    args = parser.parse_args()
    print(json.dumps(run(args.panel,args.points,read(args.settings),args.code,args.out)))
