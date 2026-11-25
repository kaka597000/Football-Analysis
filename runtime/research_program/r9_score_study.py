"""Train team goal-bucket residuals and replay same-fixture R7, R8 and market controls."""

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile

import numpy as np

from . import all_scope_model as base
from . import all_scope_study as study
from . import continuation_report as report
from . import continuation_scores as r7
from . import continuation_study as archive
from . import r8_score_calibration as r8
from . import r8_score_study as control
from . import r9_team_shape as model
from .goal_state_study import sha, write_new

OLDER_CODES = ('eng.1', 'esp.1', 'ger.1', 'ita.1', 'fra.1', 'ned.1')
OLDER_BOUNDS = ('2004-07-01', '2005-07-01', '2006-07-01')
IDENTITY = ('id', 'cohort', 'window', 'stage', 'date', 'home', 'away', 'actual', 'line', 'source_sha256')


def verified_archive(folder, manifest, name, target):
    path = folder / name
    if sha(path) != manifest['files'][name]:
        raise ValueError('archive checksum mismatch: ' + name)
    control.extract(path, target)


def refit_r7(anchor, features, scores, cut, test):
    trained = r7.fit(r7.hazard_inputs(anchor[:cut], features[:cut]), scores[:cut])
    val = anchor[cut:test]
    arms = r7.arm_matrices(val, features[cut:test], trained)
    selection = r7.choose(val, arms, scores[cut:test])
    return dict(training=trained, selection=selection)


def compare_rows(rows, anchors, design, exposures, r7_matrices, offsets, beta, dose, window, end, refs):
    if refs is not None and len(rows) != len(refs):
        raise ValueError('archive has a different fixture count')
    records = []
    for i, (g, anchor, x, exposure, prior) in enumerate(zip(rows, anchors, design, exposures, r7_matrices, strict=True)):
        line = base.actual_line(g)
        actual = [g['home_goals'], g['away_goals']]
        full = model.adjust(prior, x, beta, 1.)
        selected = full if dose == 1. else model.adjust(prior, x, beta, dose)
        variants = dict(r7=prior, r8=r8.apply(prior, offsets, line), full_dose=full, candidate=selected)
        picks = {arm: base.pick(m, line) for arm, m in variants.items()}
        rank = {arm: model.actual_rank(m, actual) for arm, m in variants.items()}
        quality = {arm: control.quality(m, actual) for arm, m in variants.items()}
        picks['local_r7'], picks['local_candidate'] = picks['r7'], picks['candidate']
        # The originally issued market ranking is authoritative for archived ties.
        picks['market'] = refs[i]['forecast']['market'] if refs is not None else archive.market_control(g, anchor)
        tag = ('evaluation' if g['date'] < end else 'recent') if window == 'main' else window
        record = dict(id=g['id'], cohort=g['competition'], window=tag, stage=g['stage'], date=g['date'],
                      home=g['home'], away=g['away'], actual=actual, line=line, source_sha256=g['source_sha256'],
                      forecast=picks, quality=quality, actual_rank=rank, prior_team_games=exposure)
        if refs is not None:
            ref = refs[i]
            if any(record[k] != ref[k] for k in IDENTITY):
                raise ValueError('archived identity, score or line differs: ' + g['id'])
            drift = {}
            for arm, old in (('r7', 'r7'), ('r8', 'candidate')):
                issued = ref['forecast'][old]
                if picks[arm] != issued:
                    drift[arm] = picks[arm]
                    rank[arm] = model.published_rank(variants[arm], actual, issued['top3'])
                picks[arm] = issued
                quality[arm] = ref['quality'][old]
            record['different_rebuilt_picks'] = drift
            # A zero correction must inherit its control, even across numerical backends.
            for arm in ('candidate', 'full_dose'):
                if variants[arm] is prior:
                    picks[arm], rank[arm], quality[arm] = picks['r7'], rank['r7'], quality['r7']
        records.append(record)
    return records


def replay(panel, code, window, target, r7_dir=None, r8_dir=None):
    target.mkdir(parents=True, exist_ok=False)
    rows = study.check_panel(panel, code)
    panel_hash = sha(panel)
    if r7_dir is None:
        bounds = OLDER_BOUNDS
        season_roles = {'0203': 'train', '0304': 'train', '0405': 'calibration', '0506': 'test'}
        if {g['season'] for g in rows} != set(season_roles):
            raise ValueError('independent panel is missing a registered season: ' + code)
        for g in rows:
            role = 'train' if g['date'] < bounds[0] else 'calibration' if g['date'] < bounds[1] else 'test'
            if (g['date'] >= bounds[2] or season_roles[g['season']] != role or g['score_basis'] != 'regulation_90'):
                raise ValueError('independent panel season, date or score basis mismatch: ' + g['id'])
    else:
        settings = control.read(r7_dir / 'fit.json')
        if settings['source_panel_sha256'] != panel_hash or settings['competition'] != code:
            raise ValueError('panel does not match R7 fit: ' + code)
        bounds = settings['bounds']
    if window != 'main':
        rows = [g for g in rows if g['date'] < bounds[2]]
    cut = sum(g['date'] < bounds[0] for g in rows)
    test = sum(g['date'] < bounds[1] for g in rows)
    scores = np.array([(g['home_goals'], g['away_goals']) for g in rows], dtype=int)
    anchor, features, _ = r7.inputs(rows, code)
    refs = None
    if r7_dir is None:
        settings = refit_r7(anchor, features, scores, cut, test)
        write_new(target / 'r7-control.json', settings)
    priors = control.matrices(anchor[cut:], features[cut:], settings)
    validation = priors[:test - cut]
    if r8_dir is None:
        offsets = r8.fit(validation, scores[cut:test], [base.actual_line(g) for g in rows[cut:test]])
        write_new(target / 'r8-control.json', dict(offsets=offsets.tolist()))
    else:
        r8_fit = control.read(r8_dir / 'fit.json')
        if (r8_fit['panel_sha256'], r8_fit['r7_fit_sha256']) != (panel_hash, sha(r7_dir / 'fit.json')):
            raise ValueError('R8 fit identity differs from R7: ' + code)
        offsets = r8_fit['offsets']
        refs = archive.read_gzip(r8_dir / 'predictions.jsonl.gz')
    design, exposures, state = model.history(rows, anchor, code)
    beta = model.fit(anchor[:cut], design[:cut], scores[:cut])
    dose, trials = model.choose(validation, design[cut:test], beta, scores[cut:test])
    records = compare_rows(rows[test:], anchor[test:], design[test:], exposures[test:], priors[test-cut:],
                           offsets, beta, dose, window, bounds[2], refs)
    archive.put(target / 'forecasts.jsonl.gz', records)
    fit = dict(version=model.VERSION, competition=code, panel_sha256=panel_hash, bounds=bounds, beta=beta,
               training_games=cut, training_through=rows[cut-1]['date'] if cut else None,
               validation_through=rows[test-1]['date'] if test > cut else None,
               validation=trials, dose=dose)
    write_new(target / 'fit.json', fit)
    if window == 'main':
        forward = dict(version=model.VERSION, competition=code, through=rows[-1]['date'], games=len(rows),
                       beta=model.fit(anchor, design, scores), dose=dose, state=state,
                       fit_sha256=sha(target / 'fit.json'), r7_forward_sha256=sha(r7_dir / 'forward.json'))
        write_new(target / 'forward.json', forward)
    return records


def rank_summary(rows):
    def band(rank):
        return next((label for stop, label in ((1, '1'), (3, '2-3'), (6, '4-6'), (10, '7-10'))
                     if rank <= stop), '11+')
    groups = defaultdict(Counter)
    missed = Counter()
    for r in rows:
        old, new = r['actual_rank']['r7'], r['actual_rank']['candidate']
        count = groups[band(old)]
        count['games'] += 1
        count['r9_first'] += new == 1
        count['r9_top3'] += new <= 3
        if old > 3:
            missed['-'.join(map(str, r['actual']))] += 1
    drift = {k: [r['id'] for r in rows if k in r.get('different_rebuilt_picks', {})] for k in ('r7', 'r8')}
    return dict(r7_rank=dict(groups), r7_missed_scores=dict(missed.most_common()), different_rebuilt_picks=drift,
                with_both_team_histories=sum(min(r['prior_team_games']) > 0. for r in rows))


def summarize(rows):
    summary = report.result(rows)
    by_code = defaultdict(list)
    for r in rows:
        by_code[r['cohort']].append(r)
    groups = [(summary['competitions'][c], v) for c, v in by_code.items()]
    for table, sample in [(summary['overall'], rows), *groups]:
        for arm in ('r7', 'r8'):
            table['comparisons'][arm] = report.paired(sample, arm, 'candidate')
        table['comparisons']['both_local'] = report.paired(sample, 'local_r7', 'local_candidate')
        table['diagnostics'] = rank_summary(sample)
    return summary


def run(scope, output, workers, *, r5=None, r7_evidence=None, r8_evidence=None, older=None):
    codes = [x['code'] for x in control.read(scope)['competitions']]
    output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        if older is None:
            r7_manifest = control.read(r7_evidence / 'manifest.json')
            if r7_manifest['scope'] != codes:
                raise ValueError('configured scope differs from R7')
            verified_archive(r7_evidence, r7_manifest, 'inputs/pre2010.tar.gz', root / 'earlier')
            verified_archive(r8_evidence, control.read(r8_evidence / 'manifest.json'),
                             'score/forecasts.tar.gz', root / 'r8')
        else:
            codes = list(OLDER_CODES)

        def run_code(code):
            if older is not None:
                return replay(older / 'panels' / (code + '.jsonl'), code, 'pre2006', output / 'pre2006' / code)
            old = root / code / 'r7'
            verified_archive(r7_evidence, r7_manifest, 'models/' + code + '.tar.gz', old)
            control.extract(r5 / 'models' / (code + '.tar.gz'), root / code / 'r5')
            panels = [('main', root / code / 'r5/panel.jsonl')]
            if (old / 'pre2010').is_dir():
                panels.append(('pre2010', root / 'earlier/panels' / (code + '.jsonl')))
            output_rows = []
            for mode, panel in panels:
                output_rows.extend(replay(panel, code, mode, output / mode / code, old / mode,
                                          root / 'r8' / mode / code))
            return output_rows

        windows = defaultdict(list)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for rows in pool.map(run_code, codes):
                for row in rows:
                    windows[row['window']].append(row)
    write_new(output / 'metrics.json', {w: summarize(rows) for w, rows in windows.items()})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scope', type=Path, default=Path('configs/analysis-scope.json'))
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=2)
    for key in ('r5', 'r7-evidence', 'r8-evidence', 'older'):
        parser.add_argument('--' + key, type=Path)
    args = parser.parse_args()
    if args.older is None and not all((args.r5, args.r7_evidence, args.r8_evidence)):
        parser.error('archived replay needs --r5, --r7-evidence and --r8-evidence')
    run(args.scope, args.out, args.workers, r5=args.r5, r7_evidence=args.r7_evidence,
        r8_evidence=args.r8_evidence, older=args.older)
