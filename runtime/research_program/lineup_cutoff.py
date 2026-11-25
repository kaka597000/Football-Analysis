"""Measure the incremental value of the preceding seven days to the same fitted J1 model."""
import argparse
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from . import continuation_study as archive
from . import lineup_study as study
from . import r8_score_study as control
from .goal_state_study import sha, write_new
from .lineup_history import LineupHistory
from .paired_kpi_inference import compare
from .r9_score_study import verified_archive


PROTOCOL = Path(__file__).resolve().parents[2] / 'research/20261125-r18-j1-cutoff/protocol.json'
PROTOCOL_SHA = 'b639ff78f360e4f7ad458b3df285cb0253ee164ea8524cb6c9ee0c4a4755d915'


def load(r5, r7, fitted_study, roster_file, temp):
    metadata = control.read(fitted_study / 'inputs.json')
    fit = control.read(fitted_study / 'fit.json')
    recipe = control.read(fitted_study / 'forward.json')
    if (sha(PROTOCOL) != PROTOCOL_SHA or metadata['protocol_sha256'] != study.PROTOCOL_SHA256
            or fit['version'] != study.VERSION or tuple(fit['bounds']) != study.BOUNDS
            or fit['competition'] != study.CODE or fit['training_through'] >= study.BOUNDS[0]
            or fit['validation_through'] >= study.BOUNDS[1] or recipe['doses'] != fit['doses']
            or recipe['fit_sha256'] != sha(fitted_study / 'fit.json')
            or recipe['study_inputs_sha256'] != sha(fitted_study / 'inputs.json')):
        raise ValueError('cutoff protocol or frozen chronological R15 fit differs')
    manifests = {}
    for name, folder in (('r5', r5), ('r7', r7)):
        manifests[name] = control.read(folder / 'manifest.json')
        part = 'models/jpn.1.tar.gz'
        if metadata['archive_sha256'][name] != manifests[name]['files'][part]:
            raise ValueError('original J1 population differs: ' + name)
        verified_archive(folder, manifests[name], part, temp / name)
    panel = temp / 'r5/panel.jsonl'
    native = temp / 'r7/main/fit.json'
    if (sha(panel) != metadata['panel_sha256'] or sha(native) != metadata['r7_fit_sha256']
            or sha(temp / 'r7/main/forward.json') != metadata['baseline_sha256']
            or manifests['r5']['files']['evidence/source-index.json'] != metadata['schedule_index_sha256']):
        raise ValueError('J1 schedule or R7 weights differ')
    roster_rows, roster_hash = study.read_rosters(roster_file)
    if roster_hash != metadata['rosters_sha256']:
        raise ValueError('J1 lineup snapshot differs')
    stamp = study.snapshot_identity(roster_file.parent / 'snapshot-manifest.json', roster_rows, roster_hash,
                                    metadata['panel_sha256'], metadata['archive_sha256']['r5'])
    if any(metadata[k] != v for k, v in stamp.items()):
        raise ValueError('R15 lineup snapshot clock differs')
    games = study.check_panel(panel, study.CODE)
    study._schedule(games, r5, manifests['r5'])
    rosters, _, _ = study.roster_inputs(games, roster_rows)
    anchors, native_features, _ = study.r7.inputs(games, study.CODE)
    return games, rosters, anchors, native_features, fit, control.read(native), metadata


def cutoffs(history, game):
    day = date.fromisoformat(game['date'])
    week_ago = (day - timedelta(days=7)).isoformat()
    return history.context(game), history.context(game, cutoff=week_ago)


def evaluate(game, published, prior, history, fit):
    identity = ('id', 'date', 'stage', 'home', 'away', 'home_id', 'away_id', 'kickoff_at', 'source_sha256')
    window = 'evaluation' if game['date'] < study.BOUNDS[2] else 'recent'
    if (any(published[k] != game[k] for k in identity) or published['cohort'] != study.CODE
            or published['window'] != window or game['date'] < study.BOUNDS[1]
            or published['actual'] != [game['home_goals'], game['away_goals']]
            or published['line'] != study.base.actual_line(game)):
        raise ValueError('R15 original holdout fixture or label differs: ' + game['id'])
    originals = {arm: published['forecast'][arm] for arm in ('r5', 'r7', 'r10', 'market')}
    (current, observed), (older, stale) = cutoffs(history, game)
    if not np.allclose(current, published['features'], rtol=1e-10, atol=1e-12) or observed != published['process']:
        raise ValueError('R15 earlier-history exposure differs: ' + game['id'])
    line = published['line']
    tips, quality, _ = study.predictions(prior, current, line, originals, fit['fitted'], fit['doses'])
    if (tips['candidate'] != published['forecast']['candidate']
            or not np.isclose(quality['p_first'], published['quality']['candidate']['p_first'], rtol=1e-7, atol=1e-9)):
        raise ValueError('frozen R15 candidate did not reproduce: ' + game['id'])
    lagged, old_quality, _ = study.predictions(prior, older, line, originals, fit['fitted'], fit['doses'])
    record = {k: published[k] for k in (*identity, 'cohort', 'window', 'actual', 'line')}
    record.update(
        forecast=dict(current=tips['candidate'], lagged=lagged['candidate'], r7=originals['r7'], market=originals['market']),
        primary_p=dict(current=quality['p_first'], lagged=old_quality['p_first']),
        last_week_changed=bool(np.any(current != older)),
        features=dict(current=current.tolist(), lagged=older.tolist()), process=dict(current=observed, lagged=stale))
    return record


def summarize(records):
    pairs = {arm: compare(records, baseline=arm, candidate='current') for arm in ('lagged', 'r7', 'market')}
    days, coverage = {}, {}
    for arm in ('current', 'lagged'):
        adapted = [dict(r, forecast=dict(candidate=r['forecast'][arm]),
                        quality=dict(candidate=dict(p_first=r['primary_p'][arm]))) for r in records]
        days[arm] = study.selected_days(adapted)
        coverage[arm] = {side: sum(bool((r['process'][arm][side]['previous'] or {}).get('roster_available'))
                                   for r in records) for side in study.SIDES}
    result = dict(population=len(records), actual_lines=sum(r['line'] is not None for r in records),
                  updated_features=sum(r['last_week_changed'] for r in records), prior_roster_coverage=coverage,
                  comparisons=pairs, rankings={arm: study._rank_summary(seats) for arm, seats in days.items()})
    return result, days


def run(r5, r7, fitted_study, roster_file, out):
    if out.exists():
        raise FileExistsError(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=out.parent) as tmp:
        root = Path(tmp)
        games, rosters, anchors, native_features, fit, settings, metadata = load(r5, r7, fitted_study, roster_file, root)
        history = LineupHistory(rosters)
        history.walk(games, anchors)
        cut, test = (sum(g['date'] < d for g in games) for d in study.BOUNDS[:2])
        if (cut, test - cut, len(games)) != (1912, 380, 2822):
            raise ValueError('original J1 training, validation or full population differs')
        priors = control.matrices(anchors[cut:], native_features[cut:], settings)
        published = archive.read_gzip(fitted_study / 'forecasts.jsonl.gz')
        records = [evaluate(game, ref, prior, history, fit) for game, ref, prior in
                   zip(games[test:], published, priors[test-cut:], strict=True)]
        package = root / 'paired'
        package.mkdir()
        metrics = {}
        for window, expected in (('evaluation', 380), ('recent', 150)):
            selected = [r for r in records if r['window'] == window]
            if len(selected) != expected:
                raise ValueError('registered historical fixture count differs: ' + window)
            metrics[window], days = summarize(selected)
            write_new(package / (window + '-seats.json'), days)
        archive.put(package / 'forecasts.jsonl.gz', records)
        write_new(package / 'metrics.json', metrics)
        write_new(package / 'inputs.json', dict(protocol_sha256=PROTOCOL_SHA, r15_fit=sha(fitted_study / 'fit.json'),
                  r15_forecasts=sha(fitted_study / 'forecasts.jsonl.gz'), r15_inputs=sha(fitted_study / 'inputs.json'),
                  roster_sha256=metadata['rosters_sha256'], panel_sha256=metadata['panel_sha256']))
        package.rename(out)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('r5', 'r7', 'study', 'rosters', 'out'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    run(args.r5, args.r7, args.study, args.rosters, args.out)
