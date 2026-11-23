"""Chronological experiment for a registered conditional score policy, with R5 controls."""
import argparse
from collections import defaultdict
from datetime import date
from itertools import groupby
import json
from pathlib import Path


from research.analysis_freeze import digest
from . import all_scope_model as base
from . import all_scope_neighbors as neighbors
from . import all_scope_study as study
from . import conditional_scores as model
from .goal_state_study import read_lines, sha, write_new


def index(rows):
    result = {r['id']:r for r in rows}
    if len(result)!=len(rows):
        raise ValueError('duplicate fixture identity')
    return result


def early_bounds(code):
    if code in study.CALENDAR:
        return ('2016-01-01','2017-01-01','2018-01-01')
    return ('2014-07-01','2016-07-01','2017-07-01')


def add_totals(rows, quotes):
    for game in rows:
        item = quotes.get(game['id'])
        if item is None:
            continue
        if (item['source_sha256'] != game['source_sha256'] or item['line']<=0
                or game.get('total') or not isinstance(item['same_book'],bool)):
            raise ValueError('conflicting historical total quote '+game['id'])
        game['total'] = {key:item[key] for key in ('line','over','under')}
        game['paired_market'] = bool(game.get('h2h') and item['same_book'])
        game['total_evidence'] = item


def refit_r5(rows, code, bands):
    """Reproduce both historical R5 selection layers using only the early training and selection dates."""
    start,cut,end = bands
    train = [r for r in rows if r['date']<start]
    val = [r for r in rows if start<=r['date']<cut]
    test = [r for r in rows if cut<=r['date']<end]
    states, examples = {},defaultdict(list)
    study.walk(train,code,states,{},fit_rows=examples)
    trees = {stage:base.fit_trees(x) for stage,x in examples.items()}
    validation = study.walk(val,code,states,trees)
    first, _ = study.choose(validation)
    forecasts = study.walk(test,code,states,trees,policy=first)
    stages,memories = {},{}
    neighbors.walk(train,code,stages,memories)
    selection = neighbors.walk(val,code,stages,memories,index(validation),original_policy=first)
    second, _ = study.choose(selection, arms=neighbors.PAIR)
    predictions = neighbors.walk(test,code,stages,memories,index(forecasts),policy=second)
    return index(predictions),dict(original=first,neighbors=second,fit=len(train),selection=len(val))


def archived_r5(folder):
    records = []
    for window in ('evaluation','recent'):
        records.extend(read_lines(folder/f'neighbor-{window}.jsonl'))
    return index(records)


def _weights(examples):
    return {key:model.fit(values).tolist() for key,values in examples.items()}


def run(panel, code, out, observations, *, previous=None, early=False, totals=None):
    rows = study.check_panel(panel,code)
    bands = early_bounds(code) if early else study.boundaries(code)
    start,cut,end = bands
    if totals:
        add_totals(rows,totals)
    if early:
        rows = [r for r in rows if r['date']<end]
        reference, control = refit_r5(rows, code, bands)
    else:
        reference, control = archived_r5(previous),None
    state = model.blank()
    examples = {'goals':[],'process':[]}
    hl = study.half_life(code)
    coefficients = None
    fit_n = None
    fit_through = None
    out.mkdir(parents=True,exist_ok=True)
    count = 0
    seen = set()
    with (out/'predictions.jsonl').open('x',encoding='utf8') as dest:
        for stamp, same_day in groupby(rows,key=lambda r:r['date']):
            day = date.fromisoformat(stamp).toordinal()
            if coefficients is None and stamp>=start:
                coefficients = _weights(examples)
                fit_n = len(examples['goals'])
            games = list(same_day)
            model.advance(state,games,day,hl)
            prepared = [(g,model.components(state,g,day,hl)) for g in games]
            for g,p in prepared:
                score = [g['home_goals'], g['away_goals']]
                if stamp>=cut:
                    old = reference[g['id']]
                    if (old['actual']!=score or old['date']!=stamp or old['stage']!=g['stage']
                            or old['home']!=g['home'] or old['away']!=g['away']
                            or old['line']!=base.actual_line(g)):
                        raise ValueError('comparison fixture mismatch '+g['id'])
                    picks,quality = model.forecast(g,p,coefficients,score=score)
                    if picks['market']!=old['forecast']['market']:
                        raise ValueError('independent market comparator differs '+g['id'])
                    picks['r5'] = old['forecast']['candidate']
                    result = dict(id=g['id'],cohort=code,stage=g['stage'],date=stamp,home=g['home'],
                        away=g['away'],actual=score,line=base.actual_line(g),
                        window='early' if early else 'evaluation' if stamp<end else 'recent',
                        market_basis=p['market']['kind'], forecast=picks, quality=quality,
                        local_games=p['local_games'],pooled_games=p['pooled_games'],
                        process_exposure=round(p['process_exposure'],5),source_sha256=g['source_sha256'])
                    dest.write(json.dumps(result,ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\n')
                    count += 1
                    seen.add(g['id'])
                if stamp<start or not early:
                    examples['goals'].append(model.example(p['structural'],p['goal_signal'],score))
                    examples['process'].append(model.example(p['process_anchor'],p['signal'],score))
            model.update(state,prepared,observations,day,hl)
            if stamp<start:
                fit_through=stamp
    if set(reference)!=seen:
        raise ValueError('comparison lost or added fixtures')
    write_new(out/'fit.json',dict(version=model.VERSION,competition=code,bands=list(bands),panel_sha256=sha(panel),
        feature_names=model.NAMES,penalty=model.RIDGE,bounds=[-model.BOUND,model.BOUND],coefficients=coefficients,
        training_matches=fit_n,training_through=fit_through,forecast_count=count,r5_control=control,
        process_fixture_digest=digest([observations[g['id']] for g in rows if g['id'] in observations])))
    if not early:
        write_new(out/'forward-model.json',dict(version=model.VERSION,competition=code,through=rows[-1]['date'],
            training_matches=len(rows),fit_sha256=sha(out/'fit.json'),coefficients=_weights(examples),state=model.dump(state)))


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--panel',type=Path,required=True)
    ap.add_argument('--competition',required=True)
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--process',type=Path,required=True)
    ap.add_argument('--r5-study',type=Path)
    ap.add_argument('--early',action='store_true')
    ap.add_argument('--totals',type=Path)
    args=ap.parse_args()
    obs=index([r for r in read_lines(args.process) if r['competition']==args.competition])
    quotes=index(read_lines(args.totals)) if args.totals else None
    run(args.panel,args.competition,args.out,obs,previous=args.r5_study,early=args.early,totals=quotes)
