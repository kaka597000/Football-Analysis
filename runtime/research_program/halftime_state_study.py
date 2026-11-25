"""Fit and replay the registered Japanese halftime-state model on complete R7 windows."""
import argparse
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from research.analysis_freeze import aware
from . import all_scope_model as base
from . import continuation_scores as r7
from . import continuation_study as archive
from . import halftime_state as model
from . import lineup_study as study
from . import r10_shot_model as decisions
from . import r8_score_study as control
from .goal_state_study import read_lines, sha, write_new
from .paired_kpi_inference import compare
from .r9_score_study import verified_archive
from .r9_team_shape import published_rank


PROTOCOL = Path(__file__).resolve().parents[2] / 'research/20261125-r20-j1-halftime/protocol.json'
PROTOCOL_SHA256 = '1d9e483923e84facc6f674411dc8e25c8543453d9778a671acd9dd1498aa1d30'


def match_halves(games, rows):
    ids = {g['id'] for g in games}
    by_id = {r['match_id']: r for r in rows}
    if len(rows) != len(by_id) or set(by_id) != ids:
        raise ValueError('one source observation or recorded exclusion required for every registered J1 fixture')
    observations = []
    for game in games:
        row = by_id[game['id']]
        if type(row['eligible']) is not bool:
            raise ValueError('halftime source eligibility is not boolean')
        if not row['eligible']:
            if not row.get('reason'):
                raise ValueError('missing reason for excluded source row')
            observations.append(None)
            continue
        scores = [game['home_goals'], game['away_goals']]
        halves = [row['half1'], row['half2']]
        source = row['source_sha256']
        if (any(str(row[s + '_id']) != game[s + '_id'] for s in ('home', 'away'))
                or aware(row['kickoff_utc']) != aware(game['kickoff_at']) or row['full'] != scores
                or not isinstance(source, str) or len(source) != 64 or any(c not in '0123456789abcdef' for c in source)
                or any(not isinstance(h, list) or len(h) != 2 or any(type(n) != int or n < 0 for n in h) for h in halves)
                or list(map(sum, zip(*halves))) != scores):
            raise ValueError('half scores, identity, or provenance differ from R5: ' + game['id'])
        observations.append(halves)
    return observations


def historical_inputs(folders, temp):
    names = dict(r5='models/jpn.1.tar.gz', r7='models/jpn.1.tar.gz', r10='replay/main.tar.gz')
    manifests = {key: control.read(folder / 'manifest.json') for key, folder in folders.items()}
    for key, folder in folders.items():
        verified_archive(folder, manifests[key], names[key], temp / key)
    panel = temp / 'r5/panel.jsonl'
    settings = control.read(temp / 'r7/main/fit.json')
    shot = control.read(temp / 'r10/main/jpn.1/fit.json')
    fingerprint = sha(panel)
    if ((settings['version'], settings['competition'], tuple(settings['bounds']), settings['source_panel_sha256']) !=
            (r7.VERSION, study.CODE, study.BOUNDS, fingerprint) or
            (shot['version'], shot['competition'], tuple(shot['bounds']), shot['panel_sha256']) !=
            (decisions.VERSION, study.CODE, study.BOUNDS, [fingerprint])):
        raise ValueError('historical anchors belong to a different panel or fold')
    games = study.check_panel(panel, study.CODE)
    study._schedule(games, folders['r5'], manifests['r5'])
    fifth = [dict(row, window=w) for w in ('evaluation', 'recent') for row in read_lines(temp / 'r5/study' / (w+'.jsonl'))]
    reference = study._controls([g for g in games if g['date'] >= study.BOUNDS[1]], fifth,
                                archive.read_gzip(temp / 'r7/main/forecasts.jsonl.gz'),
                                archive.read_gzip(temp / 'r10/main/jpn.1/forecasts.jsonl.gz'))
    return games, settings, reference, dict(panel_sha256=fingerprint, r7_fit_sha256=sha(temp/'r7/main/fit.json'),
        archive_sha256={key: manifest['files'][names[key]] for key, manifest in manifests.items()})


def evaluate(reference, prior, fitted, doses):
    identity, controls = reference
    line = identity['line']
    modified = model.adjusted(prior, fitted)
    fixed = base.pick(modified, line) if modified is not prior else controls['r7']
    candidate, heads = decisions.decisions(prior, modified, doses, line, controls['r7'])
    forecast = dict(controls, fixed_full=fixed, candidate=candidate)
    quality, ranks = {}, {}
    score = tuple(identity['actual'])
    for name, matrix in (('candidate', heads['score']), ('r7', prior), ('fixed_full', modified)):
        picks = forecast[name]['top3']
        cells = [tuple(map(int, pick.split('-'))) for pick in picks]
        quality[name] = dict(p_first=float(matrix[cells[0]]), p_three=float(sum(matrix[c] for c in cells)),
                             p_observed=float(matrix[score]))
        ranks[name] = published_rank(matrix, score, picks)
    return dict(identity, forecast=forecast, quality=quality, actual_ranks=ranks)


def summarize(rows, days):
    comparisons, tracks = {}, {}
    for baseline in ('r7', 'r10', 'market', 'fixed_full'):
        comparison = compare(rows, baseline=baseline, candidate='candidate')
        comparisons[baseline] = comparison['paired']
        tracks.update(comparison['tracks'])
    movements = Counter()
    for r in rows:
        for name in ('r7','candidate'):
            rank = r['actual_ranks'][name]
            movements[f'{name}_rank_{rank if rank <= 3 else "outside"}'] += 1
        old, new = (r['actual_ranks'][k] for k in ('r7','candidate'))
        movements['rescued_into_three'] += old > 3 >= new
        movements['lost_from_three'] += new > 3 >= old
    specific = {}
    for score in ('1-1','2-2'):
        games = [r for r in rows if '-'.join(map(str, r['actual'])) == score]
        specific[score] = dict(actual=len(games), arms={name: dict(
            issued_first=sum(r['forecast'][name]['primary'] == score for r in rows),
            first=sum(r['actual_ranks'][name] == 1 for r in games), three=sum(r['actual_ranks'][name] <= 3 for r in games))
            for name in ('r7', 'candidate', 'fixed_full')})
    return dict(registered=len(rows), observed_lines=sum(r['line'] is not None for r in rows), tracks=tracks,
                paired=comparisons, movements=dict(movements), draw_scores=specific, selected=study._rank_summary(days))


def run(r5, r7_folder, r10, halves, out):
    if out.exists():
        raise FileExistsError(out)
    if sha(PROTOCOL) != PROTOCOL_SHA256:
        raise ValueError('halftime protocol differs from registration')
    out.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=out.parent) as temporary:
        temp = Path(temporary)
        games, settings, reference, inputs = historical_inputs(dict(r5=r5, r7=r7_folder, r10=r10), temp)
        half_hash = sha(halves)
        observations = match_halves(games, list(read_lines(halves)))
        cut, test, recent = (sum(g['date'] < bound for g in games) for bound in study.BOUNDS)
        if not 0 < cut < test < recent < len(games) or settings['training_games'] != cut:
            raise ValueError('incomplete R7 calendar')
        anchors, features, _ = r7.inputs(games, study.CODE)
        priors = control.matrices(anchors[cut:], features[cut:], settings)
        fitted = model.fit(anchors[:cut], observations[:cut]); validation = priors[:test-cut]
        adjusted = [model.adjusted(m, fitted) for m in validation]
        doses, trials = decisions.choose(games[cut:test], validation, adjusted)
        rows = [evaluate(ref, prior, fitted, doses) for ref, prior in zip(reference, priors[test-cut:], strict=True)]
        common = dict(version=model.VERSION, competition=study.CODE, protocol_sha256=PROTOCOL_SHA256,
                      halves_sha256=half_hash, panel_sha256=inputs['panel_sha256'])
        result = temp / 'study'; result.mkdir()
        folds = dict(training=(0, cut), validation=(cut, test), evaluation=(test, recent), recent=(recent, len(games)))
        cover = {k: dict(fixtures=b-a, verified_halves=sum(r is not None for r in observations[a:b])) for k,(a,b) in folds.items()}
        write_new(result/'fit.json', dict(common, calibration=fitted, doses=doses, trials=trials, bounds=list(study.BOUNDS),
                  training_through=games[cut-1]['date'], validation_through=games[test-1]['date'], coverage=cover))
        write_new(result/'forward.json', dict(common, calibration=model.fit(anchors, observations), doses=doses,
                  fit_sha256=sha(result/'fit.json'), training_through=games[-1]['date'], training_games=len(games)))
        archive.put(result/'forecasts.jsonl.gz', rows)
        days = study.selected_days(rows)
        archive.put(result/'seats.jsonl.gz', days)
        metrics = {w: summarize([r for r in rows if r['window'] == w], [d for d in days if d['window'] == w])
                   for w in ('evaluation','recent')}
        write_new(result/'metrics.json', metrics)
        write_new(result/'inputs.json', dict(common, **{k:v for k,v in inputs.items() if k != 'panel_sha256'}))
        if sha(halves) != half_hash:
            raise ValueError('half-score observations changed during the study')
        result.rename(out)
    return metrics


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ('r5', 'r7', 'r10', 'halves', 'out'):
        parser.add_argument('--' + field, type=Path, required=True)
    args = parser.parse_args()
    run(args.r5, args.r7, args.r10, args.halves, args.out)
