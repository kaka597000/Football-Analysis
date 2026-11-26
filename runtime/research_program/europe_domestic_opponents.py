"""Replay a European score candidate using opponent-adjusted domestic history."""

import argparse
from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from . import all_scope_model as base
from . import continuation_study as archive
from . import domestic_form as domestic
from . import domestic_form_study as identity
from . import europe_score_dispersion as locked_grids
from . import r9_team_shape as fit_score
from .goal_state_study import read_lines, sha, write_new
from .paired_kpi_inference import compare
from .r8_score_study import read
from .ucl_zero_goals import IDENTITY, reference_rows


PROTOCOL = Path(__file__).resolve().parents[2] / 'research/20261126-r38-europe-domestic-opponents/protocol.json'
ARMS = {'r13': 0., 'half': .5, 'full': 1.}
SCORE_KEYS = ('score_top1', 'score_top3')
HOME_BUCKET, AWAY_BUCKET = np.indices((5, 5)).reshape(2, -1)


def regulation_domestic(rows):
    return [row for row in rows if row['status'] == 'FT' and row['regulation_period'] == 2]


class OppositionHistory:
    """Index domestic opportunities using each opponent's strictly earlier profile."""

    def __init__(self, records):
        records = sorted(records, key=lambda row: (row['date'], row['competition'], row['id']))
        venue = domestic.DomesticHistory(records)
        self.games = defaultdict(list)
        self.dates = venue.dates
        entries = {team: iter(rows) for team, rows in venue.games.items()}
        for row in records:
            day = date.fromisoformat(row['date']).toordinal()
            home, away = (domestic.team_id(row[k]) for k in ('home_id', 'away_id'))
            for team, opponent in ((home, away), (away, home)):
                prior_day, _, _, expected_for, expected_against = next(entries[team])
                if prior_day != day:
                    raise ValueError('domestic venue history differs from the ordered source')
                (attack, defence, _, _), n = venue.profile(opponent, day, day)
                self.games[team].append((day, expected_for, expected_against,
                                         expected_for*np.exp(defence), expected_against*np.exp(attack), n > 0))

    def profile(self, team, day):
        games = self.games.get(team, ())
        end = bisect_left(self.dates.get(team, ()), day)
        recent = [row for row in games[max(0, end-8):end] if row[0] >= day-120]
        if not recent:
            return (0., 0.), dict(domestic=0, opponent=0, last_domestic_date=None)
        base_for, base_against, adj_for, adj_against, exposed = np.asarray(recent)[:, 1:].sum(axis=0)
        correction = np.clip(np.log((5.6 + np.array([base_for, base_against])) /
                                    (5.6 + np.array([adj_for, adj_against]))), -1., 1.)
        return correction, dict(domestic=len(recent), opponent=int(exposed),
                                last_domestic_date=date.fromordinal(recent[-1][0]).isoformat())

    def context(self, teams, day):
        ordinal = date.fromisoformat(day).toordinal()
        (h_attack, h_defence), h_exposure = self.profile(teams[0], ordinal)
        (a_attack, a_defence), a_exposure = self.profile(teams[1], ordinal)
        design = np.column_stack((HOME_BUCKET*h_attack + AWAY_BUCKET*a_attack,
                                  HOME_BUCKET*a_defence + AWAY_BUCKET*h_defence))
        return design, dict(home=h_exposure, away=a_exposure, cutoff_utc_day=day)


def score_row(ref, grid, design, exposure, beta):
    original = ref['forecast']['candidate']
    actual = tuple(ref['actual'])
    forecasts = {key: ref['forecast'][key] for key in ('market', 'r7')}
    loss = {}
    for arm, dose in ARMS.items():
        matrix = fit_score.adjust(grid, design, beta, dose)
        scores = original['top3'] if matrix is grid else base.top_three_scores(matrix)
        forecasts[arm] = dict(original, primary=scores[0], top3=scores)
        loss[arm] = round(float(-np.log(max(1e-300, matrix[actual]))), 12)
    return dict({key: ref[key] for key in IDENTITY}, forecast=forecasts, exact_log_loss=loss,
                exposure=exposure, correction_design=design.round(12).tolist())


def paired_changes(rows, arm):
    changed = []
    for row in rows:
        actual = '-'.join(map(str, row['actual']))
        old, new = (row['forecast'][candidate] for candidate in ('r13', arm))
        first = int(actual == new['primary']) - int(actual == old['primary'])
        three = int(actual in new['top3']) - int(actual in old['top3'])
        if first or three:
            changed.append(dict(id=row['id'], actual=actual, original=old['top3'], candidate=new['top3'],
                                first_delta=first, three_delta=three, actual_in_original_three=actual in old['top3']))
    return changed


def grade(folds, beta):
    validation, held = (folds[phase] for phase in ('validation', 'evaluation'))
    trials = {}
    for arm, dose in ARMS.items():
        result = compare(validation, baseline='market', candidate=arm)
        metrics = result['tracks'][arm]['metrics']
        trials[arm] = dict(first=metrics['score_top1']['hits'], three=metrics['score_top3']['hits'], dose=dose,
                           log_loss=round(float(np.mean([row['exact_log_loss'][arm] for row in validation])), 12))
    original = trials['r13']
    admitted = [arm for arm, scores in trials.items() if all(scores[k] >= original[k] for k in ('first', 'three'))]
    selected = min(admitted, key=lambda arm: (-trials[arm]['first'], -trials[arm]['three'],
                                               trials[arm]['log_loss'], trials[arm]['dose']))
    controls = {arm: compare(held, baseline='r13', candidate=arm) for arm in ARMS if arm != 'r13'}
    net = tuple(controls[selected]['paired'][metric]['net_hits'] for metric in SCORE_KEYS) if selected != 'r13' else (0, 0)
    changes = {arm: paired_changes(held, arm) for arm in controls}
    exposures = {}
    for phase, rows in folds.items():
        sides = Counter(sum(row['exposure'][side]['opponent'] > 0 for side in ('home', 'away')) for row in rows)
        exposures[phase] = dict(sorted(sides.items()))
    return dict(beta=beta, fixtures={key: len(rows) for key, rows in folds.items()}, exposure_sides=exposures,
                validation=trials, selected=selected, evaluation=controls,
                original_market=compare(held, baseline='market', candidate='r13'),
                selected_market=compare(held, baseline='market', candidate=selected), evaluation_changes=changes,
                forward_screen_passed=selected != 'r13' and min(net) >= 0 and max(net) > 0)


def run(data, out):
    spec = read(PROTOCOL)
    if sha(locked_grids.PROTOCOL) != spec['r30_protocol_sha256']:
        raise ValueError('original R13 grid reconstruction or dates differ')
    original = read(locked_grids.PROTOCOL)
    ledger = {name: [] for name in original['windows']}
    summary = {}
    with TemporaryDirectory(prefix='europe-domestic-opponents-') as directory:
        work = Path(directory)
        for source in spec['archives'].values():
            locked_grids.unpack(data, source, work)
        locked_grids.unpack(data, original['archives']['r13'], work/'r13')
        raw_domestic = list(read_lines(work/'domestic/domestic.jsonl'))
        records = regulation_domestic(raw_domestic)
        history = OppositionHistory(records)
        club_ids = identity.identities(work/'continental/identities.jsonl')
        for code in original['competitions']:
            team, source = work/code, work/'r13'/code
            for key in ('panel', 'r7'):
                locked_grids.unpack(data, original['archives'][code][key], team/key)
            refs, matrices = locked_grids.original_grids(team/'panel/panel.jsonl', read(team/'r7/main/fit.json'),
                read(source/'fit.json'), reference_rows(source/'forecasts.jsonl.gz'), code, original)
            contexts = [identity.context(history, club_ids, dict(ref, competition=code)) for ref in refs]
            indices = {phase: [i for i, row in enumerate(refs) if window['from'] <= row['date'] < window['before']]
                       for phase, window in original['windows'].items()}
            if any(len(items) != original['expected_per_window'][code] for items in indices.values()):
                raise ValueError('incomplete registered analysis population: ' + code)
            train = indices['train']
            beta = fit_score.fit(matrices[train], np.asarray([contexts[i][0] for i in train]),
                                 np.asarray([refs[i]['actual'] for i in train]))
            folds = {name: [score_row(refs[i], matrices[i], *contexts[i], beta) for i in group]
                     for name, group in indices.items()}
            summary[code] = grade(folds, beta)
            for phase, rows in folds.items():
                ledger[phase].extend(rows)
    out.mkdir(parents=True, exist_ok=False)
    write_new(out/'summary.json', dict(version=spec['version'], protocol_sha256=sha(PROTOCOL),
              domestic=dict(original=len(raw_domestic), included=len(records), leagues=len({r['competition'] for r in records})),
              competitions=summary))
    for phase, rows in ledger.items():
        archive.put(out/f'{phase}.jsonl.gz', rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    run(args.data, args.out)
