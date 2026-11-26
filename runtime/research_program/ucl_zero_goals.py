"""Test zero-goal score allocation in the Champions League league phase."""

import argparse
from collections import Counter
from pathlib import Path
import tempfile

import numpy as np
from scipy.optimize import minimize

from . import all_scope_model as base
from . import all_scope_study as study
from . import continuation_report as report
from . import continuation_scores as r7
from . import continuation_study as archive
from . import r8_score_study as control
from .goal_state_study import sha, write_new
from .r9_team_shape import published_rank


PROTOCOL = Path(__file__).resolve().parents[2] / 'research/20261126-r24-ucl-zero-goals/protocol.json'
COMPETITION = 'uefa.champions'
STAGE = 'league-phase'
DOSES = (0., .5, 1.)
TOTAL = (base.H + base.A).ravel()
BLANK = np.where((base.H == 0) & (base.A > 0), 1,
                 np.where((base.A == 0) & (base.H > 0), 2, 0)).ravel()
IDENTITY = ('id', 'cohort', 'stage', 'date', 'home', 'away', 'actual', 'line', 'source_sha256')


def in_support(score):
    return all(0 <= goals < base.SIZE for goals in score)


def totals(matrix):
    return np.bincount(TOTAL, weights=matrix.ravel())


def fit(matrices, scores):
    """Fit the two scoreless offsets from the observed allocation within each total."""
    categories, labels = [], []
    for matrix, score in zip(matrices, scores, strict=True):
        if not in_support(score):
            continue
        home, away = score
        at_total = TOTAL == home + away
        mass = np.bincount(BLANK[at_total], weights=matrix.ravel()[at_total], minlength=3)
        label = BLANK[home * base.SIZE + away]
        if matrix[home, away] <= 0:
            raise ValueError('observed training score has zero prior mass')
        categories.append(mass / mass.sum())
        labels.append(label)
    if not categories:
        raise ValueError('no on-grid scores in the registered fit')
    priors = np.asarray(categories)
    counts = np.bincount(labels, minlength=3)[1:]

    def objective(beta):
        weights = priors * np.exp(np.r_[0., beta])
        normalizers = weights.sum(axis=1)
        loss = np.log(normalizers).sum() - counts @ beta + 8 * (beta @ beta)
        gradient = (weights[:, 1:] / normalizers[:, None]).sum(axis=0) - counts + 16 * beta
        return loss, gradient

    result = minimize(objective, np.zeros(2), jac=True, method='L-BFGS-B', bounds=[(-2., 2.)] * 2)
    if not result.success:
        raise ValueError('conditional score fit failed: ' + str(result.message))
    return result.x, len(scores) - len(labels)


def adjust(prior, beta, dose):
    """Preserve mass at each total, including 0-0 and zero-support totals."""
    if dose == 0. or not np.any(beta):
        return prior
    weighted = prior.ravel() * np.exp(np.r_[0., beta])[BLANK]
    mass = totals(prior)
    weighted_mass = np.bincount(TOTAL, weights=weighted)
    ratios = np.divide(mass, weighted_mass, out=np.ones_like(mass), where=weighted_mass > 0)
    projected = (weighted * ratios[TOTAL]).reshape(prior.shape)
    projected[0, 0] = prior[0, 0]
    return projected if dose == 1. else (1. - dose) * prior + dose * projected


def selection(matrix, prior, original):
    if matrix is prior:
        return original
    ordered = base.top_three_scores(matrix)
    return dict(original, primary=ordered[0], top3=ordered)


def quality(matrix, picks, score):
    cells = [tuple(map(int, value.split('-'))) for value in picks['top3']]
    return dict(p_observed=float(matrix[score]) if in_support(score) else 0.,
                p_first=float(matrix[cells[0]]), p_three=float(sum(matrix[cell] for cell in cells)),
                wdl=np.bincount(base.RESULT.ravel(), weights=matrix.ravel()).tolist(),
                p_either_blank=float(matrix[0, :].sum() + matrix[1:, 0].sum()),
                actual_rank=published_rank(matrix, score, picks['top3']) if in_support(score) else None)


def reference_rows(path):
    rows = archive.read_gzip(path)
    by_id = {row['id']: row for row in rows}
    if len(by_id) != len(rows):
        raise ValueError('duplicate comparison fixture in ' + path.name)
    return by_id


def game_record(game, window, prior, beta, dose, r7_ref, r13_ref):
    score = game['home_goals'], game['away_goals']
    line = base.actual_line(game)
    row = dict(id=game['id'], cohort=game['competition'], stage=game['stage'], date=game['date'],
               home=game['home'], away=game['away'], actual=list(score), line=line,
               source_sha256=game['source_sha256'], window=window)
    if any(row[k] != ref[k] for ref in (r7_ref, r13_ref) for k in IDENTITY):
        raise ValueError('archived fixture, regulation score or line differs: ' + game['id'])
    original = r7_ref['forecast']['candidate']
    market = r7_ref['forecast']['market']
    local = base.pick(prior, line)
    if r13_ref['forecast']['r7'] != original or r13_ref['forecast']['market'] != market:
        raise ValueError('R13 and R7 archived controls differ: ' + game['id'])
    chosen, full = (adjust(prior, beta, value) for value in (dose, 1.))
    picks = dict(r7=original, r13=r13_ref['forecast']['candidate'], market=market,
                 candidate=selection(chosen, prior, original), full_dose=selection(full, prior, original))
    row['forecast'] = picks
    row['quality'] = dict(r7=quality(prior, original, score),
                          candidate=quality(chosen, picks['candidate'], score),
                          full_dose=quality(full, picks['full_dose'], score))
    row['rebuilt_r7_if_different'] = local if local != original else None
    row['max_total_mass_error'] = float(np.max(np.abs(totals(chosen) - totals(prior))))
    return row


def summarize(rows):
    scores = {arm: report.rates(rows, arm) for arm in rows[0]['forecast']}
    paired = {}
    for arm in ('candidate', 'full_dose'):
        paired[arm] = {ref: report.paired(rows, ref, arm) for ref in ('r7', 'r13', 'market')}
        for comparison in paired[arm].values():
            for value in comparison.values():
                value['gained'] = (value['disagreements'] + value['net']) // 2
                value['lost'] = value['disagreements'] - value['gained']
    rank_counts = {arm: Counter(row['quality'][arm]['actual_rank'] for row in rows)
                   for arm in ('r7', 'candidate', 'full_dose')}
    blank = [row for row in rows if 0 in row['actual']]
    groups = {'at_least_one_blank': blank, 'both_scored': [row for row in rows if 0 not in row['actual']]}
    blank_groups = {name: {arm: report.rates(group, arm)['counts'] for arm in scores}
                    for name, group in groups.items()}
    blank_firsts = {arm: sum('0' in row['forecast'][arm].get('primary', '').split('-') for row in rows)
                    for arm in scores}
    return dict(fixtures=len(rows), scores=scores, paired=paired, realized_ranks=rank_counts,
                blanks=dict(actual=len(blank), first_picks=blank_firsts, splits=blank_groups,
                            mean_probability={arm: float(np.mean([row['quality'][arm]['p_either_blank']
                                for row in rows])) for arm in rank_counts}),
                rebuilt_r7_differences=sum(row['rebuilt_r7_if_different'] is not None for row in rows),
                max_total_mass_error=max(row['max_total_mass_error'] for row in rows))


def load_inputs(data, protocol, directory):
    for name, spec in protocol['sources']['archives'].items():
        path = data / 'workspace-updates' / spec['directory'] / spec['archive']
        if sha(path) != spec['sha256']:
            raise ValueError('registered archive changed: ' + str(path))
        control.extract(path, directory / name)

    panel = directory / 'r5/panel.jsonl'
    saved = directory / 'r7/main'
    settings = control.read(saved / 'fit.json')
    if settings['competition'] != COMPETITION or sha(panel) != settings['source_panel_sha256']:
        raise ValueError('R7 was fitted on a different panel')
    games = study.check_panel(panel, COMPETITION)
    windows = {}
    for name, window in protocol['windows'].items():
        indices = [i for i, game in enumerate(games) if game['stage'] == STAGE
                   and window['from'] <= game['date'] < window['before']]
        if len(indices) != window['fixtures'] or any(
                not games[i]['score_basis'].startswith('regulation_90') for i in indices):
            raise ValueError('registered regulation population differs: ' + name)
        windows[name] = indices
    training = windows['train']
    after_training = windows['validation'] + windows['late_2025_26'] + windows['early_2026_27']
    if not training[-1] < after_training[0] or after_training != sorted(set(after_training)):
        raise ValueError('training and evaluation windows are not disjoint and ordered')
    anchor, features, _ = r7.inputs(games, COMPETITION)
    priors = dict(zip(after_training, control.matrices(anchor[after_training], features[after_training], settings),
                      strict=True))
    beta, excluded = fit(anchor[training], [(games[i]['home_goals'], games[i]['away_goals']) for i in training])
    r7_refs = reference_rows(saved / 'forecasts.jsonl.gz')
    r13_refs = reference_rows(directory / 'r13/uefa.champions/forecasts.jsonl.gz')
    return games, windows, priors, beta, excluded, r7_refs, r13_refs


def run(data, out):
    protocol = control.read(PROTOCOL)
    with tempfile.TemporaryDirectory(prefix='r24-ucl-') as scratch:
        games, windows, priors, beta, excluded, r7_refs, r13_refs = load_inputs(data, protocol, Path(scratch))

    def rows(window, dose):
        return [game_record(games[i], window, priors[i], beta, dose, r7_refs[games[i]['id']],
                            r13_refs[games[i]['id']]) for i in windows[window]]

    validation = {}
    for dose in DOSES:
        evaluation = rows('validation', dose)
        stats = report.rates(evaluation, 'candidate')
        first, three = (stats['counts'][key][0] for key in ('score_top1', 'score_top3'))
        validation[dose] = dict(first=first, three=three, log_loss=stats['score_log_loss'])

    def order(dose):
        stats = validation[dose]
        return -stats['first'], -stats['three'], stats['log_loss'], dose

    dose = min(DOSES, key=order)
    evaluations = {window: rows(window, dose) for window in ('late_2025_26', 'early_2026_27')}
    evaluation = [row for records in evaluations.values() for row in records]
    metrics = {window: summarize(records) for window, records in evaluations.items()}
    metrics['combined'] = summarize(evaluation)
    delta = metrics['combined']['paired']['candidate']['r7']
    eligible = dose > 0 and delta['score_top1']['net'] > 0 and delta['score_top3']['net'] >= 0

    out.mkdir(parents=True, exist_ok=False)
    write_new(out / 'fit.json', dict(version=protocol['version'], competition=COMPETITION, stage=STAGE,
              protocol_sha256=sha(PROTOCOL), sources=protocol['sources'], windows=protocol['windows'],
              beta_home_blank=float(beta[0]), beta_away_blank=float(beta[1]), training_out_of_grid=excluded,
              validation_trials=validation, selected_dose=dose, prospective_screen_passed=eligible))
    write_new(out / 'metrics.json', metrics)
    archive.put(out / 'validation.jsonl.gz', rows('validation', dose))
    archive.put(out / 'forecasts.jsonl.gz', evaluation)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True, help='R23 data repository root')
    parser.add_argument('--out', type=Path, required=True, help='new directory for the R24 run')
    args = parser.parse_args()
    run(args.data, args.out)
