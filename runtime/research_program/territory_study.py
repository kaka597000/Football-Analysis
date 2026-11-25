"""Date-held comparison of prior xG, territorial pressure and the original R7 forecast."""

import argparse
from collections import Counter, defaultdict
import csv
from pathlib import Path
import tempfile

import numpy as np

from . import all_scope_model as base
from . import all_scope_study as panels
from . import continuation_forward as forward
from . import continuation_report as report
from . import continuation_scores as r7
from . import continuation_study as archive
from . import r10_shot_model as decision
from . import r8_score_study as control
from . import territory_hurdle as hurdle
from .goal_state_study import read_lines, sha, write_new
from .r9_score_study import verified_archive
from .territory_history import TerritoryHistory

VERSION = 'R14-territory-press-hurdle-1'
CODES = ('eng.1','esp.1','ger.1','ita.1','fra.1')


def extensions(root):
    games = list(read_lines(root/'fixture-panels.jsonl'))
    with (root/'market-rows.csv').open(encoding='utf-8-sig',newline='') as f:
        markets = list(csv.DictReader(f))
    lookup = {(r['competition'],r['id']):r for r in markets}
    if len(lookup) != len(markets) or {(g['competition'],g['id']) for g in games} != set(lookup):
        raise ValueError('R11 panel and original market rows differ')
    groups = defaultdict(list)
    for game in games:
        source = lookup[game['competition'],game['id']]
        if (any(source[k] != game[k] for k in ('date','home','away','source_sha256'))
                or any(int(source[k]) != game[k] for k in ('home_goals','away_goals'))):
            raise ValueError('mismatched supplemental source '+game['id'])
        def price(key):
            return float(source[key]) if source[key] else None
        odds = [price(key) for key in ('B365H','B365D','B365A','B365>2.5','B365<2.5')]
        if any(q is not None and (not np.isfinite(q) or q<=1) for q in odds):
            raise ValueError('invalid source decimal price')
        h2h = dict(zip(base.SIDES,odds[:3],strict=True)) if None not in odds[:3] else None
        total = dict(line=2.5,over=odds[3],under=odds[4]) if None not in odds[3:] else None
        groups[game['competition']].append(dict(game,h2h=h2h,total=total,paired_market=bool(h2h and total),
            bookmaker='Bet365',market_time_basis='archived_nonclosing_category_no_quote_clock'))
    return {k:sorted(v,key=lambda g:(g['date'],g['id'])) for k,v in groups.items()}


def record(game, window, prior, x, evidence, fits, doses, benchmark, market, r10):
    actual = [game['home_goals'],game['away_goals']]
    line = base.actual_line(game)
    local = base.pick(prior,line)
    adjusted = {name:hurdle.apply(prior,x,fit) for name,fit in fits.items()}
    chosen, heads = decision.decisions(prior,adjusted['full'],doses['full'],line,benchmark)
    xg, _ = decision.decisions(prior,adjusted['xg'],doses['xg'],line,benchmark)
    xg_matched, _ = decision.decisions(prior,adjusted['xg'],doses['full'],line,benchmark)
    local_chosen, _ = decision.decisions(prior,adjusted['full'],doses['full'],line,local)
    forecasts = dict(r7=benchmark,local_r7=local,local_candidate=local_chosen,candidate=chosen,market=market,
                     xg=xg,xg_matched_dose=xg_matched)
    for name,matrix in adjusted.items():
        forecasts['fixed_'+name] = benchmark if matrix is prior else base.pick(matrix,line)
    if r10 is not None:
        forecasts['r10'] = r10
    cells = [tuple(map(int,s.split('-'))) for s in chosen['top3']]
    quality = control.quality(heads['score'],actual)
    quality['p_first'] = float(heads['score'][cells[0]])
    quality['p_three'] = float(sum(heads['score'][c] for c in cells))
    quality['wdl'] = np.bincount(base.RESULT.ravel(),weights=heads['wdl'].ravel(),minlength=3).tolist()
    return dict(id=game['id'],cohort=game['competition'],window=window,stage=game['stage'],date=game['date'],
                home=game['home'],away=game['away'],actual=actual,line=line,source_sha256=game['source_sha256'],
                process=evidence,features=x.tolist(),forecast=forecasts,
                quality=dict(candidate=quality,local_r7=control.quality(prior,actual)))


def summarize(rows):
    result = report.result(rows)
    groups = defaultdict(list)
    for r in rows:
        groups[r['cohort']].append(r)
    for out, group in [(result['overall'],rows),*((result['competitions'][c],g) for c,g in groups.items())]:
        for arm in ('r7','r10','xg','xg_matched_dose'):
            out['comparisons'][arm] = report.paired(group,arm,'candidate')
        out['comparisons']['same_runtime'] = report.paired(group,'local_r7','local_candidate')
        out['comparisons']['fixed_increment'] = report.paired(group,'fixed_xg','fixed_full')
        audit = Counter()
        for r in group:
            x = np.asarray(r['features'])
            audit['nonzero_xg'] += int(np.any(x[:,:2]))
            audit['nonzero_territory'] += int(np.any(x[:,2:4]))
            audit['nonzero_press'] += int(np.any(x[:,4:]))
            before,_ = report.indicators(r,'r7')
            after,_ = report.indicators(r,'candidate')
            for i,name in enumerate(('first','three')):
                audit[name+'_gained'] += int(after[i] and not before[i])
                audit[name+'_lost'] += int(before[i] and not after[i])
        out['coverage_and_changes'] = dict(audit)
    return result


def replay(code, games, extra, observations, refs, settings, bounds, output, *, baseline=None):
    output.mkdir()
    all_games = games + extra
    if extra and extra[0]['date'] <= games[-1]['date']:
        raise ValueError('overlapping original and supplementary panel')
    if len({g['id'] for g in all_games}) != len(all_games):
        raise ValueError('duplicate target in '+code)
    history = TerritoryHistory(observations,code)
    x, metadata = history.walk(all_games)
    x = np.asarray(x)
    for game, detail in zip(all_games,metadata,strict=True):
        detail['target_fixture_audit'] = history.audit_fixture(game, detail)
    y = np.array([[g['home_goals'],g['away_goals']] for g in all_games])
    anchors, z, _ = r7.inputs(all_games,code)
    cut,test = (sum(g['date']<d for g in games) for d in bounds[:2])
    if not 0<cut<test<len(games) or len(refs)!=len(games)-test or x.shape!=(len(all_games),2,6):
        raise ValueError('misaligned fold or process panel')
    fits, doses, trials = {},{},{}
    priors = control.matrices(anchors[cut:len(games)],z[cut:len(games)],settings)
    val = priors[:test-cut]
    for arm in ('xg','full'):
        fits[arm] = hurdle.fit(anchors[:cut],x[:cut],y[:cut],xg_only=arm=='xg')
        alternatives = [hurdle.apply(m,features,fits[arm]) for m,features in zip(val,x[cut:test],strict=True)]
        doses[arm], trials[arm] = decision.choose(games[cut:test],val,alternatives)
    write_new(output/'fit.json',dict(version=VERSION,competition=code,bounds=list(bounds),train_n=cut,
              validation_n=test-cut,training_through=games[cut-1]['date'],validation_through=games[test-1]['date'],
              fitted=fits,doses=doses,trials=trials))
    records = []
    for i, ref in enumerate(refs,start=test):
        g = games[i]
        if (ref['cohort'],ref['id'],ref['date'],ref['stage'],ref['home'],ref['away'],ref['actual'],ref['line'],
                ref['source_sha256']) != (code,g['id'],g['date'],g['stage'],g['home'],g['away'],y[i].tolist(),
                                         base.actual_line(g),g['source_sha256']):
            raise ValueError('reference forecast mismatch: '+g['id'])
        source = ref['forecast']
        records.append(record(g,ref['window'],priors[i-cut],x[i],metadata[i],fits,doses,
                              source['r7'],source['market'],source['candidate']))
    if baseline is not None:
        recipe = control.read(baseline)
        for i,g in enumerate(extra,start=len(games)):
            m = forward.matrix(g,recipe)
            # Market-only scores derive from the same 1X2 and 2.5 input even when the R7 recipe adjusts them.
            raw, _, _, _ = base.baseline(g, np.full_like(m,1./m.size))
            market = archive.market_control(g,raw)
            records.append(record(g,'november_extension',m,x[i],metadata[i],fits,doses,
                                  base.pick(m,base.actual_line(g)),market,None))
        write_new(output/'forward.json',dict(version=VERSION,competition=code,through=all_games[-1]['date'],
                  baseline_sha256=sha(baseline),fit_sha256=sha(output/'fit.json'),doses=doses['full'],xg_doses=doses['xg'],
                  fitted=hurdle.fit(anchors,x,y,xg_only=False),xg_fitted=hurdle.fit(anchors,x,y,xg_only=True)))
    archive.put(output/'forecasts.jsonl.gz',records)
    return records


def run(r5,r7_dir,r10,r11,events,out,*,independent=None):
    events_digest = sha(events)
    observed = list(read_lines(events))
    out.mkdir(parents=True,exist_ok=False)
    for mode in ('main','independent') if independent else ('main',):
        (out/mode).mkdir()
    grouped = defaultdict(list)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        manifests = {p:control.read(p/'manifest.json') for p in (r5,r7_dir,r10,r11)}
        verified_archive(r11,manifests[r11],'extension.tar.gz',root/'ext')
        supplemental = extensions(root/'ext')
        verified_archive(r10,manifests[r10],'replay/main.tar.gz',root/'r10')
        if independent:
            verified_archive(r10,manifests[r10],'replay/independent.tar.gz',root/'r10')
        for code in CODES:
            name = 'models/'+code+'.tar.gz'
            verified_archive(r5,manifests[r5],name,root/code/'panel')
            verified_archive(r7_dir,manifests[r7_dir],name,root/code/'r7')
            panel,old = root/code/'panel/panel.jsonl',root/code/'r7/main'
            fit = control.read(old/'fit.json')
            if fit['source_panel_sha256']!=sha(panel) or fit['competition']!=code:
                raise ValueError('R7 source panel checksum or competition differs')
            ref = archive.read_gzip(root/'r10/main'/code/'forecasts.jsonl.gz')
            rows = replay(code,panels.check_panel(panel,code),supplemental[code],observed,ref,fit,fit['bounds'],
                          out/'main'/code,baseline=old/'forward.json')
            if independent:
                folder = root/'r10/independent'/code
                early = panels.check_panel(independent/'panels'/(code+'.jsonl'),code)
                rows += replay(code,early,[],observed,archive.read_gzip(folder/'forecasts.jsonl.gz'),
                               control.read(folder/'r7-control.json'),('2017-07-01','2018-07-01','2019-07-01'),
                               out/'independent'/code)
            for row in rows:
                grouped[row['window']].append(row)
    if sha(events) != events_digest:
        raise ValueError('source events changed during replay')
    write_new(out/'metrics.json',{w:summarize(rows) for w,rows in sorted(grouped.items())})
    write_new(out/'inputs.json',dict(events_sha256=events_digest,extension_manifest_sha256=sha(r11/'manifest.json'),
                                     source_events=len(observed),independent_requested=independent is not None))


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for field in ('r5','r7','r10','r11','events','out'):
        parser.add_argument('--'+field,type=Path,required=True)
    parser.add_argument('--independent',type=Path)
    a=parser.parse_args()
    run(a.r5,a.r7,a.r10,a.r11,a.events,a.out,independent=a.independent)
