"""Replay the registered continental club study on the original R7 and R10 fixtures."""

import argparse
from collections import Counter, defaultdict
from datetime import timezone
from pathlib import Path
import tempfile

import numpy as np

from research.analysis_freeze import aware
from . import all_scope_model as base
from . import all_scope_study as study
from . import continuation_forward as forward
from . import continuation_report as report
from . import continuation_scores as r7
from . import continuation_study as archive
from . import domestic_form as model
from . import r8_score_study as control
from . import r10_shot_model as decision
from .goal_state_study import read_lines, sha, write_new
from .r9_score_study import verified_archive


def identities(path):
    result = {}
    for row in read_lines(path):
        key = row['competition'], row['id']
        if key in result or len(row['source_sha256']) != 64:
            raise ValueError('missing origin or duplicate continental identity: ' + str(key))
        pair = [model.team_id(row[k]) for k in ('home_id', 'away_id')]
        if pair[0] == pair[1]:
            raise ValueError('duplicate continental team: ' + str(key))
        utc_day = aware(row['kickoff_utc']).astimezone(timezone.utc).date().isoformat()
        result[key] = (row['date'], utc_day, pair)
    return result


def context(history, lookup, game):
    identity = lookup.get((game['competition'], game['id']))
    if identity is None:
        return history.context((None, None), game['date'])
    archived_day, utc_day, pair = identity
    if archived_day != game['date']:
        raise ValueError('continental archive date mismatch: ' + game['id'])
    return history.context(pair, utc_day)


def evaluated(game, window, prior, x, exposure, fitted, doses, original, market, *, r10=None):
    line = base.actual_line(game)
    actual = [game['home_goals'], game['away_goals']]
    local = base.pick(prior, line)
    full = model.adjust(prior, x, fitted)
    chosen, heads = decision.decisions(prior, full, doses, line, original)
    local_chosen, _ = decision.decisions(prior, full, doses, line, local)
    forecasts = dict(candidate=chosen, r7=original, local_r7=local, local_candidate=local_chosen,
                     market=market, full_dose=base.pick(full, line))
    if r10 is not None:
        forecasts['r10'] = r10
    q = control.quality(heads['score'], actual)
    cells = [tuple(map(int, value.split('-'))) for value in chosen['top3']]
    q['p_first'] = float(heads['score'][cells[0]])
    q['p_three'] = float(sum(heads['score'][cell] for cell in cells))
    q['wdl'] = np.bincount(base.RESULT.ravel(), weights=heads['wdl'].ravel(), minlength=3).tolist()
    return dict(cohort=game['competition'], id=game['id'], date=game['date'], window=window,
                stage=game['stage'], home=game['home'], away=game['away'], actual=actual, line=line,
                forecast=forecasts, quality=dict(candidate=q, local_r7=control.quality(prior, actual)),
                domestic_games=exposure, features=x.tolist(), source_sha256=game['source_sha256'])


def summarize(rows):
    result = report.result(rows)
    by_code = defaultdict(list)
    for row in rows:
        by_code[row['cohort']].append(row)
    for table, group in [(result['overall'], rows), *((result['competitions'][c], g) for c,g in by_code.items())]:
        for key in ('r7', 'r10'):
            table['comparisons'][key] = report.paired(group, key, 'candidate')
        table['comparisons']['same_runtime'] = report.paired(group, 'local_r7', 'local_candidate')
        coverage = Counter()
        for row in group:
            sides = sum(n > 0 for n in row['domestic_games'])
            coverage[('neither', 'one', 'both')[sides]] += 1
            old = report.indicators(row, 'r7')[0]
            new = report.indicators(row, 'candidate')[0]
            for i, head in enumerate(('first', 'three')):
                coverage[head + '_gained'] += int(new[i] and not old[i])
                coverage[head + '_lost'] += int(old[i] and not new[i])
        table['coverage_and_changes'] = dict(coverage)
    return result


def replay(code, panel, settings, baseline_path, refs, elite_extra, extension, history, lookup, out):
    games = study.check_panel(panel, code)
    elite = code == 'afc.champions'
    if elite:
        extra = study.check_panel(elite_extra, code, last='2026-11-25')
        if extra[0]['date'] <= games[-1]['date']:
            raise ValueError('overlapping Elite panel extension')
        games.extend(extra)
        bounds = ('2025-07-01', '2026-07-01', '2026-11-26')
    else:
        if settings['source_panel_sha256'] != sha(panel) or settings['competition'] != code:
            raise ValueError('R7 competition or historical source mismatch')
        bounds = settings['bounds']
    cut, test = (sum(g['date'] < d for g in games) for d in bounds[:2])
    if not 0 < cut < test <= len(games) or len(refs) != len(games) - test:
        raise ValueError('invalid chronological folds or comparator population')
    anchor, features, _ = r7.inputs(games, code)
    scores = np.asarray([[g['home_goals'], g['away_goals']] for g in games])
    contexts = [context(history, lookup, game) for game in games]
    design = np.asarray([x for x, _ in contexts])
    fitted = model.fit(anchor[:cut], design[:cut], scores[:cut])
    priors = control.matrices(anchor[cut:], features[cut:], settings)
    validation = priors[:test-cut]
    full = [model.adjust(m, x, fitted) for m, x in zip(validation, design[cut:test], strict=True)]
    doses, trials = decision.choose(games[cut:test], validation, full)
    write_new(out/'fit.json', dict(version=model.VERSION, competition=code, bounds=bounds,
              training_through=games[cut-1]['date'], validation_through=games[test-1]['date'],
              train_n=cut, validation_n=test-cut, features=model.FEATURES, fitted=fitted, doses=doses, trials=trials))
    records = []
    for i, original in enumerate(refs, start=test):
        game = games[i]
        if (original['id'], original['date'], original['cohort'], original['stage'], original['home'],
                original['away'], original['actual'], original['line'], original['source_sha256']) != (
                game['id'], game['date'], code, game['stage'], game['home'], game['away'],
                scores[i].tolist(), base.actual_line(game), game['source_sha256']):
            raise ValueError('R10 benchmark fixture, line or actual differs: ' + game['id'])
        picks = original['forecast']
        records.append(evaluated(game, original['window'], priors[i-cut], *contexts[i], fitted, doses,
                                 picks['r7'], picks['market'], r10=picks['candidate']))
    previous = control.read(baseline_path)
    new_ids = set()
    for game in extension:
        if (game['competition'] != code or game['date'] != '2026-11-24' or game['id'] in new_ids
                or game['date'] <= games[-1]['date'] or any(type(game[k]) is not int or game[k] < 0
                    for k in ('home_goals','away_goals'))):
            raise ValueError('invalid extension fixture: ' + game['id'])
        new_ids.add(game['id'])
        prior = forward.matrix(game, previous)
        records.append(evaluated(game, 'nov24_first_issues', prior, *context(history, lookup, game),
                                 fitted, doses, game['r7_forecast'], game['market_forecast']))
    archive.put(out/'forecasts.jsonl.gz', records)
    all_games = games + extension
    if extension:
        # The November holdout is also chronologically earlier than the prospective issue.
        # Rebuild chronological anchor offsets before fitting the prospective coefficients.
        anchor, _, _ = r7.inputs(all_games, code)
        design = np.asarray([context(history, lookup, g)[0] for g in all_games])
        scores = np.asarray([[g['home_goals'], g['away_goals']] for g in all_games])
    write_new(out/'forward.json', dict(version=model.VERSION, competition=code, through=all_games[-1]['date'],
              baseline_sha256=sha(baseline_path), fit_sha256=sha(out/'fit.json'), doses=doses,
              features=model.FEATURES, domestic_through=history.through, fitted=model.fit(anchor, design, scores)))
    return records


def run(r5, r7_dir, r10, domestic, targets, extension_path, out):
    history = model.DomesticHistory(read_lines(domestic))
    lookup = identities(targets)
    additions = defaultdict(list)
    for row in read_lines(extension_path):
        if row['competition'] != 'uefa.champions':
            raise ValueError('unregistered new test competition')
        additions[row['competition']].append(row)
    out.mkdir(parents=True, exist_ok=False)
    by_window = defaultdict(list)
    manifests = {p: control.read(p/'manifest.json') for p in (r5, r7_dir, r10)}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name in ('replay/main.tar.gz','replay/elite.tar.gz','inputs/elite.tar.gz'):
            verified_archive(r10, manifests[r10], name, root/'r10')
        for code in model.CODES:
            folder = out/code
            folder.mkdir()
            name = 'models/' + code + '.tar.gz'
            verified_archive(r5, manifests[r5], name, root/code/'r5')
            elite = code == 'afc.champions'
            kind = 'elite' if elite else 'main'
            ref = root/'r10'/kind/code
            if elite:
                old, base_path = ref/'r7-control.json', ref/'r7-forward.json'
            else:
                verified_archive(r7_dir, manifests[r7_dir], name, root/code/'r7')
                old, base_path = root/code/'r7/main/fit.json', root/code/'r7/main/forward.json'
            rows = replay(code, root/code/'r5/panel.jsonl', control.read(old), base_path,
                          archive.read_gzip(ref/'forecasts.jsonl.gz'), root/'r10/checked/extend.jsonl',
                          sorted(additions[code], key=lambda g: (g['date'], g['id'])), history, lookup, folder)
            for row in rows:
                by_window[row['window']].append(row)
    write_new(out/'metrics.json', {w: summarize(rows) for w, rows in sorted(by_window.items())})
    write_new(out/'inputs.json', dict(domestic_sha256=sha(domestic), identities_sha256=sha(targets),
                                      extension_sha256=sha(extension_path), domestic_through=history.through))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('r5','r7','r10','domestic','identities','extension','out'):
        parser.add_argument('--'+key, type=Path, required=True)
    a = parser.parse_args()
    run(a.r5, a.r7, a.r10, a.domestic, a.identities, a.extension, a.out)
