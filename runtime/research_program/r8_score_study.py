"""Reconstruct the archived R7 base matrices and evaluate the locked R8 offsets."""

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tarfile
import tempfile

import numpy as np

from . import all_scope_model as base
from . import all_scope_study as study
from . import continuation_report as report
from . import continuation_scores as r7
from . import continuation_study as archive
from . import r8_score_calibration as calibration
from .goal_state_study import sha, write_new


def read(path):
    return json.loads(path.read_bytes())


def extract(path, destination):
    with tarfile.open(path) as tar:
        tar.extractall(destination, filter='data')


def matrices(priors, features, settings):
    policy = settings['selection']
    weight = policy['mix']
    if not weight or not len(priors):
        return priors
    arm = policy['arm']
    hazards = r7.probabilities(r7.hazard_inputs(priors, features), settings['training'], arm)
    return (1 - weight) * priors + weight * r7.reconstruct(priors, *hazards)


def quality(matrix, actual):
    return dict(p_observed=float(matrix[tuple(actual)]), p_first=float(matrix.max()),
                p_three=float(np.partition(matrix.ravel(), -3)[-3:].sum()),
                wdl=np.bincount(base.RESULT.ravel(), weights=matrix.ravel(), minlength=3).tolist())


def run_competition(panel, stored, code, mode):
    settings = read(stored / 'fit.json')
    if settings['competition'] != code or sha(panel) != settings['source_panel_sha256']:
        raise ValueError('R7 panel hash or competition mismatch for ' + code)
    train_start, test_start, test_end = settings['bounds']
    rows = study.check_panel(panel, code)
    if mode != 'main':
        rows = [row for row in rows if row['date'] < test_end]
    validation_start = sum(row['date'] < train_start for row in rows)
    test_index = sum(row['date'] < test_start for row in rows)
    priors, features, _ = r7.inputs(rows, code)
    predictions = matrices(priors[validation_start:], features[validation_start:], settings)
    validation_count = test_index - validation_start
    validation = rows[validation_start:test_index]
    offsets = calibration.fit(predictions[:validation_count],
        [(row['home_goals'], row['away_goals']) for row in validation],
        [base.actual_line(row) for row in validation])
    references = archive.read_gzip(stored / 'forecasts.jsonl.gz')
    output = []
    for game, original, prior in zip(rows[test_index:], references, predictions[validation_count:], strict=True):
        actual = [game['home_goals'], game['away_goals']]
        line = base.actual_line(game)
        if (game['id'], game['date'], game['home'], game['away'], actual, line) != (
            original['id'], original['date'], original['home'], original['away'], original['actual'], original['line']
        ):
            raise ValueError('R7 reference identity, label or line changed')
        baseline = base.pick(prior, line)
        if baseline != original['forecast']['candidate']:
            raise ValueError('archived R7 picks did not reproduce: ' + game['id'])
        corrected = calibration.apply(prior, offsets, line)
        group = calibration.partitions(line)
        before = np.bincount(group, weights=prior.ravel())
        after = np.bincount(group, weights=corrected.ravel())
        candidate = base.pick(corrected, line)
        if (not np.allclose(before, after, rtol=0, atol=2e-14)
                or (candidate['wdl'], candidate['total_side']) != (baseline['wdl'], baseline['total_side'])):
            raise ValueError('a protected probability or selection changed')
        row = {key: original[key] for key in ('id','cohort','window','stage','date','home','away',
            'actual','line','source_sha256')}
        row['forecast'] = dict(r7=baseline, candidate=candidate, market=original['forecast']['market'])
        row['quality'] = dict(r7=quality(prior, actual), candidate=quality(corrected, actual))
        output.append(row)
    fit = dict(version=calibration.VERSION, competition=code, bounds=settings['bounds'],
               panel_sha256=sha(panel), r7_fit_sha256=sha(stored/'fit.json'),
               calibration_games=validation_count, calibration_through=validation[-1]['date'] if validation else None,
               l2=calibration.PENALTY, offsets=offsets.tolist())
    return output, fit


def summarize(rows):
    result = report.result(rows)
    groups = defaultdict(list)
    for row in rows:
        groups[row['cohort']].append(row)
    result['overall']['comparisons']['r7'] = report.paired(rows, 'r7', 'candidate')
    for code, group in groups.items():
        result['competitions'][code]['comparisons']['r7'] = report.paired(group, 'r7', 'candidate')
    return result


def run(scope, evidence, prior, output, workers):
    codes = [row['code'] for row in read(scope)['competitions']]
    manifest = read(evidence/'manifest.json')
    if codes != manifest['scope']:
        raise ValueError('R8 scope differs from the archived comparison')
    for name, expected in manifest['files'].items():
        if sha(evidence/name) != expected:
            raise ValueError('R7 evidence checksum mismatch: ' + name)
    output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        extract(evidence/'inputs/pre2010.tar.gz', root/'pre2010')
        def run_code(code):
            target = root/code
            extract(evidence/'models'/(code+'.tar.gz'), target/'r7')
            extract(prior/'models'/(code+'.tar.gz'), target/'r5')
            specs = [('main', target/'r5/panel.jsonl')]
            if (target/'r7/pre2010').is_dir():
                specs.append(('pre2010', root/'pre2010/panels'/(code+'.jsonl')))
            return {mode: run_competition(panel, target/'r7'/mode, code, mode) for mode,panel in specs}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            studies = dict(zip(codes, pool.map(run_code, codes), strict=True))
    by_window = {}
    for code, modes in studies.items():
        for mode, (rows, fit) in modes.items():
            folder = output/mode/code
            folder.mkdir(parents=True)
            archive.put(folder/'predictions.jsonl.gz', rows)
            write_new(folder/'fit.json', fit)
            for row in rows:
                by_window.setdefault(row['window'], []).append(row)
    write_new(output/'metrics.json', {window: summarize(rows) for window, rows in by_window.items()})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('scope','evidence','prior','out'):
        parser.add_argument('--'+key, type=Path, required=True)
    parser.add_argument('--workers', type=int, default=2)
    args = parser.parse_args()
    run(args.scope, args.evidence, args.prior, args.out, args.workers)
