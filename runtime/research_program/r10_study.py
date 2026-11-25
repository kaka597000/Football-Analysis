"""Competition-local replay of the shot model, the original controls and the refreshed Elite calendar."""

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
from . import r8_score_study as control
from . import r9_score_study as r9
from . import r10_shot_model as model
from .goal_state_study import read_lines, sha, write_new
from .r9_team_shape import actual_rank, published_rank


def observations(paths):
    result = defaultdict(dict)
    for path in paths:
        for row in read_lines(path):
            code, mid = row['competition'], row['id']
            if row.get('home_sot') is None or row.get('away_sot') is None:
                continue
            if len(row['source_sha256']) != 64:
                raise ValueError('missing shot source: ' + mid)
            old = result[code].get(mid)
            if old is not None:
                keys = ('date', 'home', 'away', 'home_sot', 'away_sot')
                if any(row[k] != old[k] for k in keys):
                    raise ValueError('conflicting shot evidence: ' + mid)
            else:
                result[code][mid] = row
    return result


def label(mode, day, end):
    if mode == 'main':
        return 'evaluation' if day < end else 'recent'
    if mode == 'elite':
        return 'elite_extension' if day > '2026-10-12' else 'elite_known'
    return 'independent2019'


def evaluate(games, priors, anchors, contexts, shots, fit, doses, mode, end, refs):
    if refs is not None and len(refs) != len(games):
        raise ValueError('published fixture population changed')
    records = []
    for i, (g, prior, anchor, ctx, shot) in enumerate(zip(games, priors, anchors, contexts, shots, strict=True)):
        line = base.actual_line(g)
        actual = [g['home_goals'], g['away_goals']]
        window = label(mode, g['date'], end)
        row = dict(id=g['id'], cohort=g['competition'], stage=g['stage'], date=g['date'], home=g['home'],
                   away=g['away'], line=line, actual=actual, window=window, source_sha256=g['source_sha256'])
        local = base.pick(prior, line)
        original = local
        reference = None
        if refs is not None:
            reference = refs[i]
            if any(row[k] != reference[k] for k in r9.IDENTITY):
                raise ValueError('published fixture or price identity mismatch: ' + g['id'])
            original = reference['forecast']['r7']
        mean_only, full = model.alternatives(prior, ctx, fit)
        chosen, heads = model.decisions(prior, full, doses, line, original)
        score_matrix = heads['score']
        local_chosen, _ = model.decisions(prior, full, doses, line, local) if original != local else (chosen, None)
        market = reference['forecast']['market'] if reference else archive.market_control(g, anchor)
        picks = dict(r7=original, local_r7=local, local_candidate=local_chosen, candidate=chosen, market=market,
                     mean_only=base.pick(mean_only, line), fixed_full=base.pick(full, line))
        quality = dict(r7=control.quality(prior, actual), candidate=control.quality(score_matrix, actual))
        if reference:
            picks['r9'] = reference['forecast']['candidate']
            quality['r7'] = reference['quality']['r7']
            if score_matrix is prior:
                quality['candidate'] = dict(quality['r7'])
        quality['candidate']['wdl'] = (quality['r7']['wdl'] if heads['wdl'] is prior
            else np.bincount(base.RESULT.ravel(), weights=heads['wdl'].ravel(), minlength=3).tolist())
        rank7 = actual_rank(prior, actual) if local == original else published_rank(prior, actual, original['top3'])
        rank_new = rank7 if score_matrix is prior else actual_rank(score_matrix, actual)
        row.update(forecast=picks, quality=quality, r7_rank=rank7, r10_rank=rank_new, shot_ready=ctx['ready'],
                   prior_shot_games=ctx['exposure'], target_box_score_available=shot is not None)
        records.append(row)
    return records


def replay(rows, hashes, code, mode, obs, output, *, r7_dir=None, refs=None):
    output.mkdir(parents=True, exist_ok=False)
    if mode == 'main':
        settings = control.read(r7_dir / 'fit.json')
        if settings['source_panel_sha256'] != hashes[0] or settings['competition'] != code:
            raise ValueError('R7 panel hash mismatch: ' + code)
        bounds = settings['bounds']
    elif mode == 'elite':
        bounds = ('2025-07-01', '2026-07-01', '2026-11-26')
    else:
        bounds = ('2017-07-01', '2018-07-01', '2019-07-01')
    if mode != 'main' and (rows[-1]['date'] >= bounds[2] or not rows[0]['date'] < bounds[0]):
        raise ValueError('study calendar does not span the registered folds')
    cut = sum(g['date'] < bounds[0] for g in rows)
    test = sum(g['date'] < bounds[1] for g in rows)
    scores = np.array([(g['home_goals'], g['away_goals']) for g in rows])
    anchors, features, r7_state = r7.inputs(rows, code)
    if mode != 'main':
        settings = r9.refit_r7(anchors, features, scores, cut, test)
        write_new(output / 'r7-control.json', settings)
    priors = control.matrices(anchors[cut:], features[cut:], settings)
    contexts, shots, state = model.walk(rows, obs, code)
    fitted = model.fit(anchors[:cut], contexts[:cut], shots[:cut], scores[:cut])
    val = priors[:test - cut]
    adjusted = [model.alternatives(m, ctx, fitted)[1] for m, ctx in zip(val, contexts[cut:test], strict=True)]
    doses, trials = model.choose(rows[cut:test], val, adjusted)
    records = evaluate(rows[test:], priors[test-cut:], anchors[test:], contexts[test:], shots[test:],
                       fitted, doses, mode, bounds[2], refs)
    archive.put(output / 'forecasts.jsonl.gz', records)
    write_new(output / 'fit.json', dict(version=model.VERSION, competition=code, panel_sha256=hashes,
              bounds=bounds, train_games=cut, validation_games=test-cut, training_through=rows[cut-1]['date'] if cut else None,
              validation_through=rows[test-1]['date'] if test > cut else None, fitted=fitted, doses=doses, trials=trials,
              box_scores=sum(s is not None for s in shots), conversion_sides_excluded=sum(
                  int(np.sum(y > s)) for y,s in zip(scores, shots) if s is not None)))
    if mode in ('main', 'elite'):
        if mode == 'elite':
            recipe = dict(competition=code, through=rows[-1]['date'], policy=settings['selection'],
                          model=r7.fit(r7.hazard_inputs(anchors, features), scores), state=r7.dump(r7_state))
            r7_path = output / 'r7-forward.json'
            write_new(r7_path, recipe)
        else:
            r7_path = r7_dir / 'forward.json'
        write_new(output / 'forward.json', dict(version=model.VERSION, competition=code, through=rows[-1]['date'],
                  fit_sha256=sha(output/'fit.json'), r7_forward_sha256=sha(r7_path), state=state, doses=doses,
                  fitted=model.fit(anchors, contexts, shots, scores)))
    return records


def summarize(rows):
    result = report.result(rows)
    groups = defaultdict(list)
    for row in rows:
        groups[row['cohort']].append(row)
    for table, group in [(result['overall'], rows), *((result['competitions'][k], v) for k,v in groups.items())]:
        comparisons = table['comparisons']
        comparisons['r7'] = report.paired(group, 'r7', 'candidate')
        comparisons['same_runtime'] = report.paired(group, 'local_r7', 'local_candidate')
        comparisons['pace_vs_mean_only'] = report.paired(group, 'mean_only', 'fixed_full')
        if 'r9' in table['scores']:
            comparisons['r9'] = report.paired(group, 'r9', 'candidate')
        counts = Counter()
        for r in group:
            old, new = r['r7_rank'], r['r10_rank']
            counts['exposed'] += r['shot_ready']
            counts['future_box_score_present'] += r['target_box_score_available']
            counts['r7_ranks_7_plus'] += old > 6
            counts['rescued_from_7_plus'] += old > 6 and new <= 3
            counts['rescued_from_4_to_6'] += 4 <= old <= 6 and new <= 3
            counts['lost_top3'] += old <= 3 and new > 3
            counts['first_wrong_to_right'] += old != 1 and new == 1
            counts['first_right_to_wrong'] += old == 1 and new != 1
        table['coverage_and_rank_changes'] = counts
    return result


def run(args):
    codes = [r['code'] for r in control.read(args.scope)['competitions']]
    args.out.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        process_paths = list(args.process)
        if args.mode == 'independent':
            codes = r9.OLDER_CODES
            process_paths.append(args.input / 'process.jsonl')
        else:
            r9.verified_archive(args.r6, control.read(args.r6/'manifest.json'), 'inputs.tar.gz', root/'process')
            process_paths.insert(0, root/'process/inputs/replay-input.jsonl')
            if args.mode == 'elite':
                codes = ['afc.champions']
            else:
                r9.verified_archive(args.r9, control.read(args.r9/'manifest.json'),
                                    'replay/artifacts.tar.gz', root/'r9')
        observed = observations(process_paths)

        def by_code(code):
            old = None
            refs = None
            if args.mode == 'independent':
                panel = args.input / 'panels' / (code + '.jsonl')
            else:
                control.extract(args.r5 / 'models' / (code + '.tar.gz'), root / code / 'panel')
                panel = root / code / 'panel/panel.jsonl'
            rows = study.check_panel(panel, code)
            hashes = [sha(panel)]
            if args.mode == 'main':
                r9.verified_archive(args.r7, control.read(args.r7/'manifest.json'),
                                    'models/' + code + '.tar.gz', root / code / 'r7')
                old = root / code / 'r7/main'
                refs = archive.read_gzip(root/'r9/main'/code/'forecasts.jsonl.gz')
            elif args.mode == 'elite':
                extend = args.input / 'extend.jsonl'
                extra = study.check_panel(extend, code, last='2026-11-25')
                if any(g['date'] <= rows[-1]['date'] for g in extra):
                    raise ValueError('AFC extension overlaps original dates')
                rows.extend(extra)
                hashes.append(sha(extend))
                if len({r['id'] for r in rows}) != len(rows):
                    raise ValueError('duplicate Elite fixture ID')
            return replay(rows, hashes, code, args.mode, observed[code], args.out / args.mode / code,
                          r7_dir=old, refs=refs)
        windows = defaultdict(list)
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for records in pool.map(by_code, codes):
                for record in records:
                    windows[record['window']].append(record)
    write_new(args.out/'metrics.json', {w: summarize(data) for w,data in windows.items()})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', required=True, choices=['main', 'independent', 'elite'])
    parser.add_argument('--scope', type=Path, default=Path('configs/analysis-scope.json'))
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--process', type=Path, action='append', default=[])
    for field in ('r5','r6','r7','r9','input'):
        parser.add_argument('--'+field, type=Path)
    args = parser.parse_args()
    required = {'main': ('r5','r6','r7','r9'), 'independent': ('input',), 'elite': ('r5','r6','input')}
    if any(getattr(args,k) is None for k in required[args.mode]):
        parser.error('missing input for ' + args.mode)
    run(args)
