"""Time-isolated R11 replay of named-company Asian handicaps on top of WDL and totals."""
import argparse
import csv
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from itertools import groupby
from pathlib import Path
import tempfile

import numpy as np

from . import all_scope_model as base
from . import all_scope_study as panels
from . import continuation_report as report
from . import continuation_scores as r7
from . import continuation_study as archive
from . import handicap_margin as model
from . import r8_score_study as replay
from .goal_state_study import read_lines, sha, write_new
from .r9_score_study import OLDER_CODES, verified_archive
from .r9_team_shape import actual_rank

OLD_FIELDS=('regular_1x2_B365H','regular_1x2_B365D','regular_1x2_B365A','regular_total25_over_B365',
            'regular_total25_under_B365','regular_AH_home_handicap_AHh','regular_AH_home_price_B365AHH','regular_AH_away_price_B365AHA')
NEW_FIELDS=('B365H','B365D','B365A','B365>2.5','B365<2.5','AHh','B365AHH','B365AHA')
DOSES=(0.,.5,1.)


def market_rows(path,fields):
    data={}
    with path.open(encoding='utf-8-sig',newline='') as f:
        for row in csv.DictReader(f):
            values=[float(row[k]) if row[k].strip() else None for k in fields]
            if any(v is not None and (not np.isfinite(v) or (i!=5 and v<=1)) for i,v in enumerate(values)):
                raise ValueError('invalid source odds: '+row['id'])
            if values[5] is not None and not float(4*values[5]).is_integer():
                values[5] = None
            key=(row['competition'],row['id'])
            if key in data:
                raise ValueError('duplicate quote: '+row['id'])
            data[key]=(row,values)
    return data


def inputs(game,quotes):
    pair=quotes.get((game['competition'],game['id']))
    if pair is None:
        return game,None
    source,q=pair
    if (any(source[k]!=game[k] for k in ('id','date','home','away','source_sha256'))
            or any(int(source[k])!=game[k] for k in ('home_goals','away_goals'))):
        raise ValueError('CSV identity conflict: '+game['id'])
    if None in q[:5]:
        return game,None
    g=dict(game,h2h=dict(zip(('home','draw','away'),q[:3])),total=dict(line=2.5,over=q[3],under=q[4]),
           paired_market=True,bookmaker='Bet365',market_time_basis='archived_nonclosing_category_no_quote_clock')
    return g,q


def alternatives(prior,q):
    if q is None:
        return prior,prior,None,False
    control=model.two_market(prior,q[:3],q[3:5])
    if None in q[5:]:
        return control,control,None,False
    full,theta,active=model.handicap_update(control,q[5],q[6:])
    return control,full,theta,active


def blend(control,full,dose):
    if dose==0 or full is control:return control
    if dose==1:return full
    return (1-dose)*control+dose*full


def tune(rows,priors,quotes):
    trials=[dict(dose=d,first=0,three=0,loss=0.,games=0) for d in DOSES]
    for g,prior,q in zip(rows,priors,quotes,strict=True):
        if q is None or None in q[5:]:continue
        ctrl,full,_,_=alternatives(prior,q)
        y=(g['home_goals'],g['away_goals'])
        for t in trials:
            p=blend(ctrl,full,t['dose']);rank=actual_rank(p,y)
            t['first']+=rank==1;t['three']+=rank<=3;t['loss']-=float(np.log(p[y]));t['games']+=1
    return min(trials,key=lambda t:(-t['first'],-t['three'],t['loss'],t['dose']))['dose'],trials


def extension_priors(rows,recipe):
    state=r7.load(recipe['state'])
    half=panels.half_life(recipe['competition'])
    anchors=[];features=[]
    for d,group in groupby(rows,key=lambda g:g['date']):
        if d<=recipe['through']:raise ValueError('overlapping R7 forward cutoff')
        games=list(group);day=date.fromisoformat(d).toordinal();base.advance(state['goals'],day,half)
        batch=[r7.prepare(state,g,day,half) for g in games]
        anchors.extend(x[0] for x in batch);features.extend(x[1] for x in batch)
        r7.update(state,games,[x[2] for x in batch],day,half)
    matrices=replay.matrices(np.asarray(anchors),np.asarray(features),
                             {'selection':recipe['policy'],'training':recipe['model']})
    return matrices,r7.dump(state)


def evaluate(rows,priors,quotes,dose,window,published):
    out=[]
    for g,p,q in zip(rows,priors,quotes,strict=True):
        y=(g['home_goals'],g['away_goals']);line=base.actual_line(g)
        ctrl,full,theta,active=alternatives(p,q)
        m=blend(ctrl,full,dose);old=base.pick(ctrl,line);f=base.pick(m,line);fixed=base.pick(full,line)
        if q is not None:
            if any(new[k]!=old[k] for new in (f,fixed) for k in ('wdl','total_side')):
                raise ValueError('held WDL/2.5 direction changed at '+g['id'])
        picks=dict(candidate=f,same_book_control=old,fixed_full=fixed,snapshot_r7=base.pick(p,line))
        picks['market'] = (base.pick(base._full_market(tuple(q[:3]),2.5,tuple(q[3:5])),line)
                           if q is not None else dict(primary=None,top3=[],wdl=None,total_side=None,total_line=None))
        original_line=None
        if published is not None:
            r=published[g['id']]
            if any(g[k]!=r[k] for k in ('date','home','away','source_sha256')) or list(y)!=r['actual']:
                raise ValueError('published fixture mismatch')
            original_line=r['line']
            for a,target in [('r7','published_r7'),('candidate','published_r10'),('market','published_market')]:
                forecast=dict(r['forecast'][a])
                if r['line']!=line:
                    forecast.update(total_side=None,total_line=None)
                picks[target]=forecast
        out.append(dict(id=g['id'],cohort=g['competition'],date=g['date'],home=g['home'],away=g['away'],
            actual=list(y),line=line,published_line=original_line,source_sha256=g['source_sha256'],window=window,
            quote_complete=q is not None and None not in q[5:],handicap=q[5] if q else None,informative=active,
            theta=theta,forecast=picks,control_rank=actual_rank(ctrl,y),candidate_rank=actual_rank(m,y),
            quality={'same_book_control':replay.quality(ctrl,y),'candidate':replay.quality(m,y)}))
    return out


def summary(rows):
    s=report.result(rows)
    for group,t in [(rows,s['overall']),*(([r for r in rows if r['cohort']==c],v) for c,v in s['competitions'].items())]:
        t['paired']={arm:report.paired(group,arm,'candidate') for arm in t['scores'] if arm!='candidate'}
        t['movement']=dict(matched=sum(r['quote_complete'] for r in group),informative=sum(r['informative'] for r in group),
            from_seven_or_later=sum(r['control_rank']>=7 and r['candidate_rank']<=3 for r in group),
            gained_first=sum(r['control_rank']!=1 and r['candidate_rank']==1 for r in group),
            lost_first=sum(r['control_rank']==1 and r['candidate_rank']!=1 for r in group))
    return s


def run(intake,extension,r7_dir,r10_dir,out):
    old=market_rows(intake/'r11_nonclosing_primary.csv',OLD_FIELDS)
    new=market_rows(extension/'market-rows.csv',NEW_FIELDS)
    additions=defaultdict(list)
    for g in read_lines(extension/'fixture-panels.jsonl'):additions[g['competition']].append(g)
    out.mkdir(parents=True,exist_ok=False)
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp)
        verified_archive(r10_dir,replay.read(r10_dir/'manifest.json'),'replay/main.tar.gz',root/'r10')
        def competition(code):
            dest=out/code;dest.mkdir()
            panel=intake/(code+'.panel.jsonl')
            payload=root/code
            verified_archive(r7_dir,replay.read(r7_dir/'manifest.json'),'models/'+code+'.tar.gz',payload)
            settings=replay.read(payload/'main/fit.json');recipe=replay.read(payload/'main/forward.json')
            if sha(panel)!=settings['source_panel_sha256']:raise ValueError('panel bytes or archive differ: '+code)
            rows=panels.check_panel(panel,code)
            games,quotes=zip(*(inputs(g,old) for g in rows))
            anchors,x,_=r7.inputs(games,code)
            bounds=settings['bounds'];i=sum(g['date']<bounds[0] for g in games);j=sum(g['date']<bounds[1] for g in games)
            priors=replay.matrices(anchors[i:],x[i:],settings)
            dose,trials=tune(games[i:j],priors[:j-i],quotes[i:j])
            pub={r['id']:r for r in archive.read_gzip(root/'r10/main'/code/'forecasts.jsonl.gz')}
            if set(pub)!={g['id'] for g in games[j:]}:raise ValueError('published fixture universe changed')
            split=sum(g['date']<bounds[2] for g in games)
            records=evaluate(games[j:split],priors[j-i:split-i],quotes[j:split],dose,'evaluation',pub)
            records+=evaluate(games[split:],priors[split-i:],quotes[split:],dose,'recent',pub)
            extension_games=sorted(additions[code],key=lambda g:(g['date'],g['id']))
            state,through=recipe['state'],recipe['through']
            if extension_games:
                extra,extra_quotes=zip(*(inputs(g,new) for g in extension_games))
                ep,state=extension_priors(extra,recipe)
                records+=evaluate(extra,ep,extra_quotes,dose,'extension',None)
                through=extra[-1]['date']
            archive.put(dest/'forecasts.jsonl.gz',records)
            write_new(dest/'fit.json',dict(competition=code,dose=dose,validation=trials,bounds=bounds,
                panel_sha256=sha(panel),r7_fit_sha256=sha(payload/'main/fit.json'),r7_forward_sha256=sha(payload/'main/forward.json')))
            write_new(dest/'forward.json',dict(version='R11-same-book-handicap-margin-1',competition=code,
                dose=dose,through=through,state=state,r7_forward_sha256=sha(payload/'main/forward.json'),
                fit_sha256=sha(dest/'fit.json')))
            return records
        windows=defaultdict(list)
        with ThreadPoolExecutor(max_workers=2) as pool:
            for rows in pool.map(competition,OLDER_CODES):
                for r in rows:windows[r['window']].append(r)
    write_new(out/'metrics.json',{k:summary(v) for k,v in windows.items()})
    write_new(out/'matched-metrics.json',{k:summary([r for r in v if r['quote_complete']]) for k,v in windows.items()
                                         if any(r['quote_complete'] for r in v)})


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for field in ('intake','extension','r7_dir','r10_dir','out'):
        parser.add_argument('--'+field.replace('_','-'),type=Path,required=True)
    run(**vars(parser.parse_args()))
