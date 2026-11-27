"""Replay the registered J1 outcome-conditional total-goals dispersion study."""

import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from . import all_scope_model as base
from . import all_scope_study as study
from . import continuation_report as report
from . import continuation_scores as r7
from . import continuation_study as archive
from . import europe_score_dispersion as dispersion
from . import j1_team_allocation as r25
from . import r8_score_study as control
from .goal_state_study import sha, write_new
from .paired_kpi_inference import compare
from .ucl_zero_goals import IDENTITY, quality, reference_rows, selection


PROTOCOL = Path(__file__).resolve().parents[2] / 'research/20261126-r29-j1-score-dispersion/protocol.json'
DOSES = {0.: 'r7', .5: 'half_dose', 1.: 'full_dose'}
SCORE_METRICS = ('score_top1', 'score_top3')


def record(game, prior, full, window, signed):
    actual = (game['home_goals'], game['away_goals'])
    row = dict(id=game['id'], cohort=r25.COMPETITION, stage=game['stage'], date=game['date'],
               home=game['home'], away=game['away'], actual=list(actual), line=base.actual_line(game),
               source_sha256=game['source_sha256'], window=window)
    if any(row[key] != signed[key] for key in IDENTITY):
        raise ValueError('R7 fixture identity, regulation score or original line differs: ' + row['id'])
    original = signed['forecast']['candidate']
    reference = signed['quality']['candidate']
    if (base.pick(prior, row['line']) != original or any(
            not np.allclose(value, reference[key], rtol=0., atol=2e-8)
            for key, value in control.quality(prior, actual).items())):
        raise ValueError('rebuilt matrix differs from the signed R7 predictions: ' + row['id'])
    matrices = {'r7': prior, 'half_dose': prior if full is prior else .5 * (prior + full), 'full_dose': full}
    picks = {name: selection(matrix, prior, original) for name, matrix in matrices.items()}
    return dict(row, forecast=dict(picks, market=signed['forecast']['market']),
                quality={name: quality(matrix, picks[name], actual) for name, matrix in matrices.items()})


def choose(trials):
    baseline = trials[0.]
    acceptable = [dose for dose, metrics in trials.items()
                  if all(metrics[key] >= baseline[key] for key in SCORE_METRICS)]
    return min(acceptable, key=lambda d: (-trials[d]['score_top1'], -trials[d]['score_top3'],
                                           trials[d]['score_log_loss'], d))


def summarize(rows, arms=('candidate', 'half_dose', 'full_dose'), baselines=('r7', 'market')):
    scores = {arm: report.rates(rows, arm) for arm in rows[0]['forecast']}
    paired = {arm: {baseline: compare(rows, baseline=baseline, candidate=arm)['paired']
                    for baseline in baselines if baseline != arm} for arm in arms}
    changes = {name: dict(first=sum(row['forecast'][name]['primary'] != row['forecast']['r7']['primary']
                                   for row in rows),
                          three_members=sum(set(row['forecast'][name]['top3']) != set(row['forecast']['r7']['top3'])
                                            for row in rows)) for name in arms}
    return dict(fixtures=len(rows), scores=scores, paired=paired, changed=changes)


def load_inputs(data, shared):
    """Load the regular J1 study's pin-checked panel and the signed historical R7 fit."""
    with TemporaryDirectory(prefix='j1-score-dispersion-') as directory:
        root = Path(directory)
        for name in ('r5_panel', 'r7_fit'):
            control.extract(r25.locked_path(data, shared, name), root / name)
        panel = root / 'r5_panel/panel.jsonl'
        fit_dir = root / 'r7_fit/main'
        settings = control.read(fit_dir / 'fit.json')
        if (settings['competition'] != r25.COMPETITION or settings['source_panel_sha256'] != sha(panel)
                or settings['training_through'] >= shared['windows']['validation']['from']
                or settings['bounds'][1] != shared['windows']['validation']['from']
                or settings['bounds'][2] != shared['windows'][r25.HISTORY[1]]['from']):
            raise ValueError('R7 fit differs from the registered J1 panel or historical date cut')
        games = study.check_panel(panel, r25.COMPETITION)
        signed = reference_rows(fit_dir / 'forecasts.jsonl.gz')
    return games, signed, settings


def replay(data, out):
    protocol = control.read(PROTOCOL)
    shared = control.read(r25.PROTOCOL)
    games, signed, settings = load_inputs(data, shared)
    windows = r25.match_rows(games, shared)
    anchors, features, _ = r7.inputs(games, r25.COMPETITION)
    train = windows['train']
    labels = np.array([(games[i]['home_goals'], games[i]['away_goals']) for i in train])
    if np.any((labels < 0) | (labels >= base.SIZE)):
        raise ValueError('training score outside the exact-score grid')
    beta = dispersion.fit(dispersion.bucket_masses(anchors[train]), labels)
    splits = ('validation', *r25.HISTORY)
    order = [(split, i) for split in splits for i in windows[split]]
    indices = [i for _, i in order]
    priors = control.matrices(anchors[indices], features[indices], settings)
    calibrated = any(beta)
    adjusted, errors = (dispersion.adjust(priors, beta) if calibrated
                        else (priors, dict(wdl=0., conditional_mean=0.)))
    rows = {split: [] for split in splits}
    for (split, i), prior, full in zip(order, priors, adjusted, strict=True):
        game = games[i]
        rows[split].append(record(game, prior, full if calibrated else prior, split, signed[game['id']]))
    validation = rows['validation']
    trials = {}
    for dose, arm in DOSES.items():
        stats = report.rates(validation, arm)
        trials[dose] = {key: stats['counts'][key][0] for key in SCORE_METRICS}
        trials[dose]['score_log_loss'] = stats['score_log_loss']
    selected = choose(trials)
    combined = [row for split in r25.HISTORY for row in rows[split]]
    for row in validation + combined:
        for field in ('forecast', 'quality'):
            row[field]['candidate'] = row[field][DOSES[selected]]
    metrics = {name: summarize(rows[name]) for name in r25.HISTORY}
    metrics['combined'] = summarize(combined)
    gains = [metrics['combined']['paired']['candidate']['r7'][key]['net_hits'] for key in SCORE_METRICS]
    eligible = selected > 0. and min(gains) >= 0 and max(gains) > 0

    out.mkdir(parents=True, exist_ok=False)
    write_new(out/'fit.json', dict(version=protocol['version'], competition=r25.COMPETITION, stage=r25.STAGE,
                                  label=protocol['label'], protocol_sha256=sha(PROTOCOL),
                                  r25_protocol_sha256=sha(r25.PROTOCOL), windows=shared['windows'],
                                  beta=dict(win=beta[0], draw=beta[1]), invariants=errors,
                                  selected_dose=selected, validation_trials=trials, prospective_screen_passed=eligible))
    write_new(out/'metrics.json', metrics)
    archive.put(out/'validation.jsonl.gz', validation)
    archive.put(out/'forecasts.jsonl.gz', combined)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    replay(args.data, args.out)
