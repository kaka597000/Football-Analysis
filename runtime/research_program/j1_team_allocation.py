"""R25 conditional team score allocation for J1 regular-phase research."""

import argparse
from pathlib import Path
import tempfile

import numpy as np

from . import all_scope_model as base
from . import all_scope_study as study
from . import continuation_report as report
from . import continuation_scores as r7
from . import continuation_study as archive
from . import r8_score_study as control
from . import r9_team_shape as team_shape
from .goal_state_study import sha, write_new
from .ucl_zero_goals import IDENTITY, TOTAL, quality, reference_rows, selection, totals


COMPETITION = 'jpn.1'
STAGE = 'regular'
PROTOCOL = Path(__file__).resolve().parents[2] / 'research/20261126-r25-j1-allocation/protocol.json'
DOSES = team_shape.DOSES
HISTORY = ('late_2025', 'early_2026_27')


def fit(anchors, design, scores):
    """Fit the allocation likelihood conditional on the observed training totals."""
    if np.any(scores >= base.SIZE):
        raise ValueError('training score outside model support')
    fixed_total = TOTAL.reshape(base.H.shape) == scores.sum(axis=1)[:, None, None]
    conditional = anchors * fixed_total
    mass = conditional.sum(axis=(1, 2))
    if np.any(mass <= 0):
        raise ValueError('training total has no anchor mass')
    return team_shape.fit(conditional / mass[:, None, None], design, scores)


def adjust(prior, design, beta, dose):
    """Apply earlier-team residuals within each possible goal total."""
    if dose == 0.:
        return prior
    weighted = team_shape.adjust(prior, design, beta, 1.)
    if weighted is prior:
        return prior
    weighted_mass = totals(weighted)
    ratios = np.divide(totals(prior), weighted_mass, out=np.ones_like(weighted_mass),
                       where=weighted_mass > 0)
    matrix = weighted * ratios[TOTAL].reshape(prior.shape)
    matrix[0, 0] = prior[0, 0]
    return matrix if dose == 1. else (1. - dose) * prior + dose * matrix


def locked_path(data, protocol, key):
    spec = protocol['sources'][key]
    path = data / 'workspace-updates' / spec['path']
    if sha(path) != spec['sha256']:
        raise ValueError('registered archive differs: ' + key)
    return path


def match_rows(games, protocol):
    windows = {}
    for name, window in protocol['windows'].items():
        indices = [i for i, game in enumerate(games) if game['stage'] == STAGE
                   and window.get('from', '') <= game['date'] < window['before']]
        if len(indices) != window['fixtures'] or any(games[i]['score_basis'] != 'regulation_90' for i in indices):
            raise ValueError('wrong J1 regulation population: ' + name)
        windows[name] = indices
    all_indices = windows['train'] + windows['validation'] + windows[HISTORY[0]] + windows[HISTORY[1]]
    if all_indices != sorted(set(all_indices)):
        raise ValueError('overlapping or unordered J1 dates')
    return windows


def evaluated(game, prior, design, exposure, beta, dose, window, r7_ref, r9_ref):
    actual = game['home_goals'], game['away_goals']
    line = base.actual_line(game)
    row = dict(id=game['id'], cohort=COMPETITION, stage=game['stage'], date=game['date'],
               home=game['home'], away=game['away'], actual=list(actual), line=line,
               source_sha256=game['source_sha256'], window=window)
    if any(row[k] != ref[k] for ref in (r7_ref, r9_ref) for k in IDENTITY):
        raise ValueError('J1 fixture, result or line differs: ' + row['id'])
    original, market = (r7_ref['forecast'][k] for k in ('candidate', 'market'))
    if (r9_ref['forecast']['r7'], r9_ref['forecast']['market']) != (original, market):
        raise ValueError('R9 does not have the same R7 or market first: ' + row['id'])
    full = adjust(prior, design, beta, 1.)
    chosen = full if dose == 1. else adjust(prior, design, beta, dose)
    matrices = dict(r7=prior, candidate=chosen, full_dose=full)
    picks = {name: selection(matrix, prior, original) for name, matrix in matrices.items()}
    local = base.pick(prior, line)
    prior_totals = totals(prior)
    return dict(row, forecast=dict(picks, r9=r9_ref['forecast']['candidate'], market=market),
                quality={name: quality(matrix, picks[name], actual) for name, matrix in matrices.items()},
                prior_team_games=exposure, rebuilt_r7_if_different=local if local != original else None,
                r7_expected_total=float(np.arange(len(prior_totals)) @ prior_totals),
                r7_probability_three_plus=float(prior_totals[3:].sum()),
                max_total_mass_error=float(np.max(np.abs(totals(chosen) - prior_totals))))


def summarize(rows):
    def paired(arm, challenger):
        metrics = report.paired(rows, arm, challenger)
        for metric in metrics.values():
            metric['gained'] = (metric['disagreements'] + metric['net']) // 2
            metric['lost'] = metric['disagreements'] - metric['gained']
        return metrics

    goals = [sum(row['actual']) for row in rows]
    total_goals = dict(actual_mean=float(np.mean(goals)),
                       r7_expected_mean=float(np.mean([row['r7_expected_total'] for row in rows])),
                       actual_games_three_plus=sum(value >= 3 for value in goals),
                       r7_expected_games_three_plus=sum(row['r7_probability_three_plus'] for row in rows))
    return dict(fixtures=len(rows), scores={arm: report.rates(rows, arm) for arm in rows[0]['forecast']},
                paired={arm: {other: paired(other, arm) for other in ('r7', 'r9', 'market')}
                        for arm in ('candidate', 'full_dose')},
                total_goals=total_goals,
                both_team_histories=sum(min(row['prior_team_games']) > 0 for row in rows),
                changed_firsts=sum(row['forecast']['r7']['primary'] != row['forecast']['candidate']['primary']
                                   for row in rows),
                rebuilt_r7_differences=sum(row['rebuilt_r7_if_different'] is not None for row in rows),
                max_total_mass_error=max(row['max_total_mass_error'] for row in rows))


def run(data, out):
    protocol = control.read(PROTOCOL)
    with tempfile.TemporaryDirectory(prefix='r25-j1-') as scratch:
        root = Path(scratch)
        for key in ('r5_panel', 'r7_fit', 'r9_replay'):
            control.extract(locked_path(data, protocol, key), root / key)
        panel = root / 'r5_panel/panel.jsonl'
        saved = root / 'r7_fit/main'
        settings = control.read(saved / 'fit.json')
        if settings['competition'] != COMPETITION or sha(panel) != settings['source_panel_sha256']:
            raise ValueError('R7 panel does not match the registered J1 history')
        games = study.check_panel(panel, COMPETITION)
        r7_refs = reference_rows(saved / 'forecasts.jsonl.gz')
        r9_refs = reference_rows(root / 'r9_replay/main/jpn.1/forecasts.jsonl.gz')

    windows = match_rows(games, protocol)
    anchors, features, _ = r7.inputs(games, COMPETITION)
    design, exposure, _ = team_shape.history(games, anchors, COMPETITION)
    scores = np.array([(g['home_goals'], g['away_goals']) for g in games])
    train = windows['train']
    beta = fit(anchors[train], design[train], scores[train])
    indices = windows['validation'] + windows[HISTORY[0]] + windows[HISTORY[1]]
    priors = dict(zip(indices, control.matrices(anchors[indices], features[indices], settings), strict=True))

    def records(window, dose):
        return [evaluated(games[i], priors[i], design[i], exposure[i], beta, dose, window,
                          r7_refs[games[i]['id']], r9_refs[games[i]['id']]) for i in windows[window]]

    trials = {}
    for dose in DOSES:
        stats = report.rates(records('validation', dose), 'candidate')
        trials[dose] = dict(first=stats['counts']['score_top1'][0], three=stats['counts']['score_top3'][0],
                            log_loss=stats['score_log_loss'])
    chosen = min(DOSES, key=lambda dose: (-trials[dose]['first'], -trials[dose]['three'],
                                         trials[dose]['log_loss'], dose))
    evaluation = {window: records(window, chosen) for window in HISTORY}
    combined = evaluation[HISTORY[0]] + evaluation[HISTORY[1]]
    metrics = {key: summarize(rows) for key, rows in evaluation.items()}
    metrics['combined'] = summarize(combined)
    pair = metrics['combined']['paired']['candidate']['r7']
    eligible = chosen > 0 and pair['score_top1']['net'] > 0 and pair['score_top3']['net'] >= 0

    out.mkdir(parents=True, exist_ok=False)
    write_new(out / 'fit.json', dict(version=protocol['version'], competition=COMPETITION, stage=STAGE,
              label=protocol['label'], protocol_sha256=sha(PROTOCOL), windows=protocol['windows'],
              beta=beta, validation_trials=trials, selected_dose=chosen, prospective_screen_passed=eligible))
    write_new(out / 'metrics.json', metrics)
    archive.put(out / 'validation.jsonl.gz', records('validation', chosen))
    archive.put(out / 'forecasts.jsonl.gz', combined)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    run(args.data, args.out)
