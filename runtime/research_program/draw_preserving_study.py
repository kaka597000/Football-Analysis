"""Replay R22 draw-preserving score grids on the locked R21 five-league cohort."""

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import tempfile

import numpy as np

from . import all_scope_model as base
from . import continuation_forward as r7_forward
from . import continuation_scores as r7
from . import continuation_study as archive
from . import draw_preserving as model
from . import paired_kpi_inference as paired
from . import r8_score_study as r7_archive
from . import score_dependence_study as r21
from .goal_state_study import read_lines, sha, write_new


ROOT = Path(__file__).resolve().parents[2]
PROTOCOL = ROOT / 'research/20261125-r22-draw-preserving/protocol.json'
PROTOCOL_SHA = '71ffd263c1d1835ef008d594f39e6e6112c0902b2b872887ab080fb443ea594c'
R21_INPUTS_SHA = '4426c16417eab543aedd54001357c846f10a6569fe9084a12333e260f611487d'
EVENTS_SHA = '63f853739198298cbbde9dfce2cb02c582cb9dccb4070215c51453325f553bd5'
BOUNDS = ('2024-07-01', '2025-07-01', '2026-07-01')
WINDOWS = ('validation_2024_25', 'evaluation', 'recent', 'november_extension')
CODES = ('eng.1', 'esp.1', 'ger.1', 'ita.1', 'fra.1')
SCORES = ('candidate', 'projected_r21_dose', 'unprotected_matched')
BOOTSTRAP_DRAWS = 5000
BOOTSTRAP_SEED = 20261125


def _read(path):
    return json.loads(path.read_bytes())


def _manifest_ok(folder, expected):
    path = folder / 'manifest.json'
    if sha(path) != expected:
        raise ValueError('locked archive manifest SHA mismatch: ' + str(path))
    return _read(path)


def _exact_r21_inputs(r21_data, r21_historical):
    package = r21_data / 'workspace-updates/20261125-r21-score-dependence'
    ref = package / 'replay/reference/inputs.json'
    repeat = package / 'replay/repeat/inputs.json'
    if sha(ref) != R21_INPUTS_SHA or sha(repeat) != R21_INPUTS_SHA or ref.read_bytes() != repeat.read_bytes():
        raise ValueError('R21 reference/repeat inputs do not match the locked SHA')
    local = r21_historical / 'inputs.json'
    if sha(local) != R21_INPUTS_SHA or local.read_bytes() != ref.read_bytes():
        raise ValueError('materialized R21 historical inputs differ from the locked reference')
    source_lock = _read(package / 'source-lock.json')
    if source_lock.get('protocol_sha256') != r21.PROTOCOL_SHA:
        raise ValueError('R21 source lock protocol differs')
    return package, _read(ref), source_lock


def _selection(priors, projected, games, originals):
    if not (len(priors) == len(projected) == len(games) == len(originals)) or not games:
        raise ValueError('2024/25 dose-selection arrays are empty or misaligned')
    trials = []
    for dose in model.DOSES:
        first = three = 0
        loss = 0.0
        for g, prior, full, original in zip(games, priors, projected, originals, strict=True):
            forecast, grids = model.choices(prior, full, dose, 0.0, original, base.actual_line(g))
            p = forecast['candidate']
            actual = (g['home_goals'], g['away_goals'])
            score = f'{actual[0]}-{actual[1]}'
            first += p['primary'] == score
            three += score in p['top3']
            loss -= float(np.log(max(1e-300, grids['candidate'][actual])))
        trials.append(dict(dose=dose, first_hits=int(first), top3_hits=int(three),
                           negative_log_likelihood=float(loss), validation_games=len(games)))
    selected = min(trials, key=lambda row: (-row['first_hits'], -row['top3_hits'],
                                           row['negative_log_likelihood'], row['dose']))
    return selected['dose'], trials


def _quality(grid, forecast, actual):
    coords = [tuple(map(int, score.split('-'))) for score in forecast['top3']]
    return dict(p_observed=float(grid[tuple(actual)]), p_first=float(grid[coords[0]]),
                p_three=float(sum(grid[c] for c in coords)),
                p_ordered=[float(grid[c]) for c in coords],
                wdl_masses=[float(grid[base.H > base.A].sum()), float(grid[base.H == base.A].sum()),
                            float(grid[base.H < base.A].sum())])


def _record(game, prior, full, dose, r21_dose, original, r21_original, market, window, kickoff,
            feature, process, reference=None):
    forecasts, grids = model.choices(prior, full, dose, r21_dose, original, base.actual_line(game))
    actual = [game['home_goals'], game['away_goals']]
    quality = {name: _quality(grids[name], forecasts[name], actual) for name in model.FORECASTS}
    quality['r7'] = _quality(prior, original, actual)
    forecasts['r21'] = r21_original
    forecasts['market'] = market
    if reference is not None:
        if (reference['forecast']['r7'] != original or reference['forecast']['market'] != market
                or reference['forecast']['candidate'] != r21_original):
            raise ValueError('R21/R7/market original forecast differs: '+game['id'])
        r21_window = {'evaluation_2025_26':'evaluation',
                      'recent_2026_27':'recent'}.get(window, window)
        for key in ('id','cohort','stage','date','home','away','actual','line','source_sha256','window'):
            expected = dict(id=game['id'],cohort=game['competition'],stage=game['stage'],date=game['date'],
                            home=game['home'],away=game['away'],actual=actual,line=base.actual_line(game),
                            source_sha256=game['source_sha256'],window=r21_window)[key]
            if reference.get(key) != expected:
                raise ValueError('R21 archived identity, score, line or window differs: '+game['id'])
        if not np.allclose(np.asarray(reference['features']), feature, rtol=0, atol=1e-12):
            raise ValueError('R21 archived prematch features differ: '+game['id'])
        if reference.get('kickoff') != kickoff:
            raise ValueError('R21 archived source kickoff differs: '+game['id'])
        if reference.get('prices') != dict(h2h=game.get('h2h'),total=game.get('total')):
            raise ValueError('R21 archived original prices differ: '+game['id'])
        old_quality = reference['quality']['candidate']
        new_quality = _quality(_mixture(prior,full,r21_dose),r21_original,actual)
        if any(not np.isclose(old_quality[key], new_quality[key], rtol=1e-10, atol=1e-12)
               for key in ('p_observed','p_first','p_three')) or not np.allclose(
                   old_quality['p_ordered'],new_quality['p_ordered'],rtol=1e-10,atol=1e-12):
            raise ValueError('R21 archived contextual score quality differs: '+game['id'])
        r7q = reference['quality']['r7']
        if not np.isclose(r7q['p_observed'], quality['r7']['p_observed'], rtol=1e-10, atol=1e-12):
            raise ValueError('R21 archived R7 observed-cell quality differs: '+game['id'])
        quality['r21'] = reference['quality']['candidate']
        quality['market'] = reference['quality']['market']
    else:
        quality['r21'] = _quality(_mixture(prior, full, r21_dose), r21_original, actual)
        quality['market'] = None
    return dict(id=game['id'], cohort=game['competition'], stage=game['stage'], date=game['date'],
                home=game['home'], away=game['away'], actual=actual, line=base.actual_line(game),
                source_sha256=game['source_sha256'], window=window, kickoff=kickoff,
                forecast=dict(r7=original, **forecasts), quality=quality, features=feature.tolist(),
                process=process, prices=dict(h2h=game.get('h2h'),total=game.get('total')),
                bookmaker=game.get('bookmaker'),market_time_basis=game.get('market_time_basis'))


def _mixture(prior, full, dose):
    return prior if dose == 0 else (1-dose)*prior + dose*full


def _rank_batches(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row['cohort'], row['window'], row['date']].append(row)
    batches = []
    for (code, window, day), group in sorted(grouped.items()):
        def ko(row):
            return row['kickoff']['source_time'], row['id']
        ranked = sorted(group, key=lambda row: (-row['quality']['candidate']['p_first'], *ko(row)))
        for rank, row in enumerate(ranked, 1):
            row['batch_rank'] = rank
            row['batch_size'] = len(ranked)
            row['seat'] = rank if rank <= 3 else None
        selected, scores = [], {}
        for seat, row in enumerate(ranked[:3], 1):
            pick = row['forecast']['candidate']
            _, outcomes, settlement = paired._decisions(pick, row['actual'], row['line'])
            scores[row['id']] = '-'.join(map(str, row['actual']))
            selected.append(dict(match_id=row['id'], seat=seat, **pick, actual=row['actual'],
                                 outcomes=outcomes, total_settlement=settlement,
                                 first_probability=row['quality']['candidate']['p_first']))
        swept = r21.sweep(selected, scores)
        batches.append(dict(cohort=code, window=window, date=day, batch_size=len(ranked),
                            ordered_ids=[row['id'] for row in ranked], selected=selected,
                            short=len(ranked) < 3, primary_sweep=bool(swept['primary_sweeps'])
                            if len(ranked) >= 3 else None, **swept))
    return batches


def _seat_summary(batches):
    full = [row for row in batches if not row['short']]
    short = [row for row in batches if row['short']]
    per_seat = {}
    for seat in (1, 2, 3):
        occupied = [b['selected'][seat-1] for b in batches if len(b['selected']) >= seat]
        metrics = {}
        for metric in paired.METRICS:
            hits = [r['outcomes'][metric] for r in occupied if r['outcomes'][metric] is not None]
            metrics[metric] = dict(hits=sum(hits), eligible=len(hits))
        per_seat[str(seat)] = dict(occupied=len(occupied), metrics=metrics)
    return dict(batches=len(batches), full_batches=len(full), short_batches=len(short),
                planned_slots=3*len(batches), occupied_slots=sum(len(b['selected']) for b in batches),
                three_first_sweeps=sum(bool(b['primary_sweep']) for b in full),
                three_score_sweeps=sum(b['displayed_three_sweeps'] for b in full),
                short_batch_sizes=dict(sorted(Counter(str(b['batch_size']) for b in short).items())),
                per_seat=per_seat)


def _compare_group(rows):
    result = {}
    pairs = (('r7','r21'),('r7','market'),('r7','candidate'),
             ('r7','projected_r21_dose'),('r7','unprotected_matched'),
             ('r21','candidate'),('market','candidate'),
             ('projected_r21_dose','unprotected_matched'))
    for baseline, arm in pairs:
        value = paired.compare(rows, baseline=baseline, candidate=arm,
                               draws=BOOTSTRAP_DRAWS, seed=BOOTSTRAP_SEED)
        result[baseline+'_vs_'+arm] = dict(tracks=value['tracks'], paired=value['paired'], bootstrap=value['bootstrap'])
    return result


def summarize(rows, batches):
    draws = [r for r in rows if r['actual'][0] == r['actual'][1]]
    non_draws = [r for r in rows if r['actual'][0] != r['actual'][1]]
    outcomes = {'draw': dict(population=len(draws), comparisons=_compare_group(draws)) if draws else dict(population=0, comparisons={}),
                'non_draw': dict(population=len(non_draws), comparisons=_compare_group(non_draws)) if non_draws else dict(population=0, comparisons={})}
    return dict(population=len(rows), comparisons=_compare_group(rows), outcome_strata=outcomes,
                three_seat_sweep=_seat_summary(batches))


def _verify_archive_bindings(r5, r7, r10, r11, locked_inputs):
    expected = locked_inputs['archive_manifest_sha256']
    manifests = {
        'r5': _manifest_ok(r5, expected['r5']),
        'r7': _manifest_ok(r7, expected['r7']),
        'r10': _manifest_ok(r10, expected['r10']),
        'r11': _manifest_ok(r11, expected['r11']),
    }
    return manifests


def run(*, r5, r7_root, r10, r11, r21_data, r21_historical, events, out):
    """Rebuild one complete main or independent-repeat study without modifying inputs."""
    if out.exists():
        raise FileExistsError(out)
    if sha(PROTOCOL) != PROTOCOL_SHA:
        raise ValueError('R22 protocol SHA mismatch')
    spec = _read(PROTOCOL)
    if spec.get('version') != model.VERSION or tuple(spec.get('competitions', ())) != CODES:
        raise ValueError('R22 protocol model version or competition list differs')
    package, r21_inputs, _ = _exact_r21_inputs(r21_data, r21_historical)
    if sha(events) != EVENTS_SHA or EVENTS_SHA != r21_inputs['events_sha256']:
        raise ValueError('R14 Understat events differ from the R21 locked source')
    manifests = _verify_archive_bindings(r5, r7_root, r10, r11, r21_inputs)
    if sha(r11/'extension.tar.gz') != sha(package/'inputs/r11/extension.tar.gz'):
        raise ValueError('supplied --r11 extension differs from the R21 locked input')
    understat_events = list(read_lines(events))
    r21_manifest = _read(package/'manifest.json')
    all_records, all_batches = [], []
    inputs = dict(version=model.VERSION, protocol_sha256=PROTOCOL_SHA,
                  r21_inputs_sha256=R21_INPUTS_SHA,
                  sources=dict(r21_reference_inputs=dict(file='replay/reference/inputs.json',sha256=R21_INPUTS_SHA),
                               r21_repeat_inputs=dict(file='replay/repeat/inputs.json',sha256=sha(package/'replay/repeat/inputs.json')),
                               r21_source_lock=dict(file='source-lock.json',sha256=sha(package/'source-lock.json')),
                               r14_understat_events=dict(file='events.jsonl',sha256=EVENTS_SHA)),
                  archive_manifest_sha256={key: sha(folder/'manifest.json') for key,folder in
                      (('r5',r5),('r7',r7_root),('r10',r10),('r11',r11))}, competitions={})
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out.parent) as td:
        work = Path(td)
        stage = work/'study'
        stage.mkdir()
        ext = work/'r11'
        r21.verified_archive(r11, manifests['r11'], 'extension.tar.gz', ext)
        extra_by_code = r21.extensions(ext)
        extension_clocks = r21.extension_clocks(ext, extra_by_code)
        r10_work = work/'r10'
        r21.verified_archive(r10, manifests['r10'], 'replay/main.tar.gz', r10_work)
        checked_source_archives = set()
        for code in CODES:
            slot = work/code
            old_r5, old_r7 = slot/'r5', slot/'r7'
            r21.verified_archive(r5, manifests['r5'], 'models/'+code+'.tar.gz', old_r5)
            r21.verified_archive(r7_root, manifests['r7'], 'models/'+code+'.tar.gz', old_r7)
            panel = old_r5/'panel.jsonl'
            games = r21.panels.check_panel(panel, code)
            extras = extra_by_code[code]
            full_games = games + extras
            if (not extras or [(g['date'],g['id']) for g in full_games] !=
                    sorted((g['date'],g['id']) for g in full_games)
                    or len({g['id'] for g in full_games}) != len(full_games)):
                raise ValueError('R21 fixture population is overlapping or unordered: '+code)
            cut, validation_end, evaluation_end = [sum(g['date'] < bound for g in games) for bound in BOUNDS]
            if not 0 < cut < validation_end < evaluation_end < len(games):
                raise ValueError('registered chronological windows are incomplete: '+code)

            setting_path, recipe_path = old_r7/'main/fit.json', old_r7/'main/forward.json'
            settings, recipe = _read(setting_path), _read(recipe_path)
            stored_fit = package/'replay/reference/main'/code/'fit.json'
            repeat_fit = package/'replay/repeat/main'/code/'fit.json'
            expected_fit_sha = r21_manifest['files']['replay/reference/main/'+code+'/fit.json']
            if sha(stored_fit) != expected_fit_sha or stored_fit.read_bytes() != repeat_fit.read_bytes():
                raise ValueError('R21 contextual fit differs from locked reference/repeat: '+code)
            old_fit = _read(stored_fit)
            if (settings.get('competition') != code or settings.get('source_panel_sha256') != sha(panel)
                    or recipe.get('competition') != code or recipe.get('fit_sha256') != sha(setting_path)
                    or old_fit.get('competition') != code or old_fit.get('version') != r21.model.VERSION
                    or old_fit.get('training_through') != games[cut-1]['date']
                    or old_fit.get('validation_through') != games[validation_end-1]['date']):
                raise ValueError('R7 or original R21 fit identity/date boundaries differ: '+code)
            contextual = old_fit.get('fitted',{}).get('context')
            original_r21_dose = old_fit.get('doses',{}).get('context',{}).get('score')
            if not contextual or original_r21_dose not in model.DOSES:
                raise ValueError('original R21 contextual fit/dose is missing or unsupported: '+code)

            raw_priors, r7_features, _ = r7.inputs(games, code)
            priors = r7_archive.matrices(raw_priors[cut:], r7_features[cut:], settings)
            if len(priors) != len(games[cut:]):
                raise ValueError('chronological R7 priors do not align')
            design, exposure = r21.model.Design(understat_events, code).walk(full_games)
            r21.check_design(full_games, design, exposure)
            target_x = design[cut:len(games)]
            full_context = np.asarray([r21.model.matrix(p,x,contextual)
                                       for p,x in zip(priors,target_x,strict=True)])
            validation_games = games[cut:validation_end]
            validation_prior = priors[:validation_end-cut]
            validation_originals = [base.pick(p,base.actual_line(g))
                                    for g,p in zip(validation_games,validation_prior,strict=True)]
            # Pass raw R21 contextual grids; choices() applies the draw projection exactly once.
            dose, dose_trials = _selection(validation_prior,full_context[:validation_end-cut],
                                           validation_games,validation_originals)

            # The held-out test starts at 2025-07, matching R21's evaluation/recent windows.
            held = games[validation_end:]
            held_ids = [g['id'] for g in held]
            r5_rows = [dict(row,window=window) for window in ('evaluation','recent')
                       for row in read_lines(old_r5/'study'/(window+'.jsonl'))]
            r7_rows = archive.read_gzip(old_r7/'main/forecasts.jsonl.gz')
            r10_rows = archive.read_gzip(r10_work/'main'/code/'forecasts.jsonl.gz')
            r5_ref,r7_ref,r10_ref = (r21.index_rows(part,held_ids)
                                     for part in (r5_rows,r7_rows,r10_rows))
            r21_forecast = package/'replay/reference/main'/code/'forecasts.jsonl.gz'
            r21_repeat_forecast = package/'replay/repeat/main'/code/'forecasts.jsonl.gz'
            expected_forecast_sha = r21_manifest['files']['replay/reference/main/'+code+'/forecasts.jsonl.gz']
            if (sha(r21_forecast) != expected_forecast_sha
                    or r21_forecast.read_bytes() != r21_repeat_forecast.read_bytes()):
                raise ValueError('R21 reference/repeat forecasts differ: '+code)
            r21_rows = archive.read_gzip(r21_forecast)
            r21_ids = held_ids+[g['id'] for g in extras]
            r21_ref = r21.index_rows(r21_rows,r21_ids)
            clocks = r21.original_clocks(games[cut:],r5,manifests['r5'],
                         _read(r5/'evidence/source-index.json'),checked_source_archives)
            clocks.update(extension_clocks)

            validation_rows = []
            for j,g in enumerate(validation_games):
                prior,full = validation_prior[j],full_context[j]
                original = validation_originals[j]
                market_prior,_,_,_ = base.baseline(g,np.full_like(prior,1.0/prior.size))
                market = r21.archive.market_control(g,market_prior)
                r21_original,_ = r21.decision.decisions(prior,full,old_fit['doses']['context'],
                                                       base.actual_line(g),original)
                validation_rows.append(_record(g,prior,full,dose,original_r21_dose,original,
                    r21_original,market,WINDOWS[0],clocks[g['id']],design[cut+j],exposure[cut+j]))

            test_rows = []
            for pos,g in enumerate(games[validation_end:],validation_end):
                window = WINDOWS[1] if g['date'] < BOUNDS[2] else WINDOWS[2]
                r21_window = 'evaluation' if window == WINDOWS[1] else 'recent'
                original,_,_ = r21.original_controls(g,r21_window,r5_ref[g['id']],r7_ref[g['id']],r10_ref[g['id']])
                reference = r21_ref[g['id']]
                if reference.get('window') != r21_window:
                    raise ValueError('R21 archived window label differs: '+g['id'])
                ref_grid = _mixture(priors[pos-cut],full_context[pos-cut],original_r21_dose)
                ref_quality = reference['quality']['candidate']
                ref_pick = reference['forecast']['candidate']
                recomputed = _quality(ref_grid,ref_pick,[g['home_goals'],g['away_goals']])
                if any(not np.isclose(ref_quality[k],recomputed[k],rtol=1e-10,atol=1e-12)
                       for k in ('p_observed','p_first','p_three')) or not np.allclose(
                           ref_quality['p_ordered'],recomputed['p_ordered'],rtol=1e-10,atol=1e-12):
                    raise ValueError('R21 archived per-cell score quality differs: '+g['id'])
                test_rows.append(_record(g,priors[pos-cut],full_context[pos-cut],dose,original_r21_dose,
                    original,ref_pick,reference['forecast']['market'],window,clocks[g['id']],design[pos],
                    exposure[pos],reference=reference))

            extension_rows = []
            for ext_j,g in enumerate(extras):
                prior = r7_forward.matrix(g,recipe)
                pos = len(games)+ext_j
                x,detail = design[pos],exposure[pos]
                full = r21.model.matrix(prior,x,contextual)
                reference = r21_ref[g['id']]
                if reference.get('window') != WINDOWS[3]:
                    raise ValueError('R21 extension window label differs: '+g['id'])
                extension_rows.append(_record(g,prior,full,dose,original_r21_dose,
                    reference['forecast']['r7'],reference['forecast']['candidate'],
                    reference['forecast']['market'],WINDOWS[3],clocks[g['id']],x,detail,reference=reference))

            out_rows = test_rows+extension_rows
            out_batches = _rank_batches(out_rows)
            validation_batches = _rank_batches(validation_rows)
            study_dir = stage/'main'/code
            study_dir.mkdir(parents=True)
            archive.put(study_dir/'validation.jsonl.gz',validation_rows)
            archive.put(study_dir/'validation-seats.jsonl.gz',validation_batches)
            archive.put(study_dir/'forecasts.jsonl.gz',out_rows)
            archive.put(study_dir/'seats.jsonl.gz',out_batches)
            archive.put(study_dir/'exposure.jsonl.gz',(
                dict(id=g['id'],competition=code,date=g['date'],features=x.tolist(),process=meta)
                for g,x,meta in zip(full_games,design,exposure,strict=True)))
            validation_through = validation_games[-1]['date']
            write_new(study_dir/'fit.json',dict(competition=code,dose=dose,version=model.VERSION,
                validation_through=validation_through,r21_fit_sha256=expected_fit_sha,
                r21_original_score_dose=original_r21_dose,validation_games=len(validation_games),
                validation_trials=dose_trials,
                selection='first hits, then three-score hits, then exact negative log likelihood, then lower dose'))
            windows = {}
            for window in WINDOWS:
                selected = validation_rows if window == WINDOWS[0] else [r for r in out_rows if r['window']==window]
                batches = validation_batches if window == WINDOWS[0] else [b for b in out_batches if b['window']==window]
                windows[window] = summarize(selected,batches) if selected else dict(
                    population=0,comparisons={},outcome_strata={},three_seat_sweep=_seat_summary([]))
            write_new(study_dir/'metrics.json',dict(competition=code,dose=dose,windows=windows))
            inputs['competitions'][code] = dict(
                panel_sha256=sha(panel),r7_fit_sha256=sha(setting_path),r7_recipe_sha256=sha(recipe_path),
                r21_fit_sha256=expected_fit_sha,r21_repeat_fit_sha256=sha(repeat_fit),
                r21_forecasts_sha256=expected_forecast_sha,r21_repeat_forecasts_sha256=sha(r21_repeat_forecast),
                r5_manifest_sha256=sha(r5/'manifest.json'),r7_forecasts_sha256=sha(old_r7/'main/forecasts.jsonl.gz'),
                r10_forecasts_sha256=sha(r10_work/'main'/code/'forecasts.jsonl.gz'),
                r5_evaluation_sha256=sha(old_r5/'study/evaluation.jsonl'),
                r5_recent_sha256=sha(old_r5/'study/recent.jsonl'),base_fixtures=len(games),
                validation_fixtures=len(validation_games),
                evaluation_fixtures=sum(r['window']==WINDOWS[1] for r in test_rows),
                recent_fixtures=sum(r['window']==WINDOWS[2] for r in test_rows),
                extension_fixtures=len(extension_rows))
            all_records.extend(validation_rows+out_rows)
            all_batches.extend(validation_batches+out_batches)
            print(code,'dose',dose,'validation',len(validation_rows),'replay',len(out_rows),flush=True)

        inputs['fixtures_sha256'] = hashlib.sha256('\n'.join(sorted(
            r['id']+':'+r['source_sha256'] for r in all_records)).encode()).hexdigest()
        write_new(stage/'inputs.json',inputs)
        metrics={'version':model.VERSION,'protocol_sha256':PROTOCOL_SHA,'r21_inputs_sha256':R21_INPUTS_SHA,
                 'formal_kpi_eligible':False,'competitions':{},'overall':{}}
        for code in CODES:
            metrics['competitions'][code]=_read(stage/'main'/code/'metrics.json')['windows']
        for window in WINDOWS:
            group=[r for r in all_records if r['window']==window]
            batches=[b for b in all_batches if b['window']==window]
            metrics['overall'][window] = summarize(group,batches) if group else dict(
                population=0,comparisons={},outcome_strata={},three_seat_sweep=_seat_summary([]))
        write_new(stage/'metrics.json',metrics)
        (stage/'README.txt').write_text(
            'R22 draw-preserving historical study\n'
            f'Protocol SHA-256: {PROTOCOL_SHA}\nR21 inputs SHA-256: {R21_INPUTS_SHA}\n'
            'Research-only chronological replay. R21 contextual coefficients remain fixed; only projected score dose is selected on 2024/25.\n',
            encoding='utf-8')
        stage.rename(out)
    return metrics
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('r5','r7_root','r10','r11','r21_data','r21_historical','events','out'):
        parser.add_argument('--'+key.replace('_','-'),required=True,type=Path)
    a=parser.parse_args()
    run(**{key:getattr(a,key) for key in ('r5','r7_root','r10','r11','r21_data','r21_historical','events','out')})


if __name__=='__main__':
    main()
