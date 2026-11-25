"""Train first-score reliability on earlier games and evaluate exact three-fixture sweeps."""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
from itertools import groupby
from pathlib import Path
import tempfile

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit, logit

from research.analysis_freeze import DEFAULT_ROOT, aware
from . import continuation_report as report
from . import continuation_study as archive
from . import goal_state_forward as ledger
from . import ranked_score_study as ranking
from .goal_state_study import sha, write_new
from .kpi_validation import _actual
from .r8_score_study import read
from .r9_score_study import verified_archive

VERSION='R12-first-score-reliability-1'
TRACK='research-r12-selected-sweep'
FEATURES=('intercept','top_three_concentration','both_score','draw','goal_total','direction_disagrees',
          'market_agrees','market_unavailable','prior_shots')


def point(mid,cohort,day,picks,quality,ready):
    c = picks['candidate']
    h,a = map(int,c['primary'].split('-'))
    first,three = quality['p_first'],quality['p_three']
    if not (0<first<1 and first<=three<=1 and three<=3*first+1e-9):
        raise ValueError('inconsistent displayed score probabilities: '+mid)
    direction='home' if h>a else 'draw' if h==a else 'away'
    market=picks['market'].get('primary')
    features=[1.,6*(first/three-1/3),int(min(h,a)>0),int(h==a),(min(h+a,5)-2)/2,
              int(direction!=c['wdl']),int(market==c['primary']),int(not market),int(ready)]
    return dict(id=mid,cohort=cohort,date=day,primary=c['primary'],top3=c['top3'],raw=first,x=features)


def historical(row):
    v=point(row['id'],row['cohort'],row['date'],row['forecast'],row['quality']['candidate'],row['shot_ready'])
    return dict(v,actual='-'.join(map(str,row['actual'])),window=row['window'])


def fit(rows):
    zeros=np.zeros(len(FEATURES))
    if not rows:return zeros.tolist()
    x=np.asarray([v['x'] for v in rows])
    offset=logit([v['raw'] for v in rows])
    y=np.asarray([v['primary']==v['actual'] for v in rows],float)
    def loss(b):
        z=offset+x@b
        return float((np.logaddexp(0,z)-y*z).sum()+12*(b@b)), x.T@(expit(z)-y)+24*b
    opt=minimize(loss,zeros,jac=True,method='L-BFGS-B',bounds=[(-1.5,1.5)]*len(zeros))
    if not opt.success:raise ValueError('reliability fit failed: '+str(opt.message))
    return opt.x.tolist()


def probability(row,coef):
    return float(expit(logit(row['raw'])+np.dot(row['x'],coef)))


def ordered(rows, field):
    return sorted(rows,key=lambda v:(-v[field],v.get('kickoff',''),v['id']))[:3]


def evaluate_group(rows,field):
    selected=ordered(rows,field)
    scored=ranking.sweep([dict(v,match_id=v['id']) for v in selected],
                         {v['id']:v['actual'] for v in selected})
    return dict(ids=[v['id'] for v in selected],**scored)


def summarize(batches):
    rows=defaultdict(list)
    for b in batches:rows[b['date']].append(b)
    out={}
    for arm in ('control','candidate'):
        total={k:sum(v[arm][k] for v in batches) for k in
               ('planned_slots','occupied','settled','primary_hits','displayed_three_hits','complete_batches','primary_sweeps','displayed_three_sweeps')}
        out[arm]=total
    for label,h,n in [('sweep','primary_sweeps','complete_batches'),('seat','primary_hits','settled')]:
        by_day=np.array([[sum(b['candidate'][h]-b['control'][h] for b in group),
                          sum(b['control'][n] for b in group)] for _,group in sorted(rows.items())],dtype=int)
        ci=None;gains=losses=0
        for b in batches:
            change=b['candidate'][h]-b['control'][h];gains+=max(0,change);losses+=max(0,-change)
        if len(by_day) and by_day[:,1].sum():
            boot=report.bootstrap_days(by_day)
            positive=boot[:,1]>0
            ci=(100*np.quantile(boot[positive,0]/boot[positive,1],[.025,.975])).tolist()
        out[label+'_change']=dict(net=gains-losses,gains=gains,losses=losses,delta_pp_ci95=ci)
    return dict(batch_dates=len(batches),candidate_pool=sum(b['n'] for b in batches),
                changed_selections=sum(set(b['candidate']['ids'])!=set(b['control']['ids']) for b in batches),**out)


def run(data,protocol,out):
    if read(protocol)['version']!=VERSION:raise ValueError('wrong study protocol')
    out.mkdir(parents=True,exist_ok=False)
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);manifest=read(data/'manifest.json')
        future={};overall={}
        for mode in ('main','independent','elite'):
            verified_archive(data,manifest,'replay/'+mode+'.tar.gz',root/mode)
            groups={};all_batches=[]
            for path in sorted((root/mode/mode).glob('*/forecasts.jsonl.gz')):
                code=path.parent.name;rows=[historical(r) for r in archive.read_gzip(path)]
                if mode=='main':early=lambda v:v['window']=='evaluation'
                elif mode=='independent':early=lambda v:v['date']<'2019-01-01'
                else:early=lambda v:v['window']=='elite_known'
                train=[v for v in rows if early(v)];test=[v for v in rows if not early(v)]
                if train and test and max(v['date'] for v in train)>=min(v['date'] for v in test):
                    raise ValueError('overlapping chronological groups: '+code)
                coef=fit(train);dest=out/mode/code;dest.mkdir(parents=True)
                through=max((v['date'] for v in train),default=None)
                write_new(dest/'fit.json',dict(features=FEATURES,coef=coef,fit_games=len(train),through=through,
                          source_forecasts_sha256=sha(path),protocol_sha256=sha(protocol)))
                for v in test:v['adjusted']=probability(v,coef)
                archive.put(dest/'pool.jsonl.gz',test)
                batches=[]
                for d,items in groupby(sorted(test,key=lambda v:(v['date'],v['id'])),key=lambda v:v['date']):
                    pool=list(items)
                    if len({v['id'] for v in pool})!=len(pool):raise ValueError('duplicate dated fixture')
                    batches.append(dict(date=d,cohort=code,n=len(pool),control=evaluate_group(pool,'raw'),
                                        candidate=evaluate_group(pool,'adjusted')))
                archive.put(dest/'batches.jsonl.gz',batches);groups[code]=summarize(batches);all_batches+=batches
                if mode=='main' or mode=='elite':
                    future[code]=dict(version=VERSION,competition=code,source_mode=mode,training_games=len(rows),
                                      through=max(v['date'] for v in rows) if rows else None,coef=fit(rows),features=FEATURES)
            overall[mode]=dict(overall=summarize(all_batches),competitions=groups)
        write_new(out/'metrics.json',overall)
        write_new(out/'forward-weights.json',future)


def issue(source,original_rank,out,weights,protocol,*,freeze_root=DEFAULT_ROOT):
    if read(protocol)['version'] != VERSION:
        raise ValueError('wrong ranking protocol')
    original,fixtures,r10=ranking.population(source)
    old=ledger._read(original_rank,read(original_rank/'plan.json'))
    if old['payload']['identity']['source_registration']!=original['record_sha256']:
        raise ValueError('ranked comparison must use the initial R10 slate')
    recipes=read(weights);refs=read(source/'receipts.json')['matches']
    groups=defaultdict(list)
    for mid,r in refs.items():
        rec=ledger._read(source,r);f=rec['fixture'];v=rec['payload'];code=f['cohort']
        day=aware(f['kickoff_at']).date().isoformat()
        recipe=recipes[code]
        if recipe['version']!=VERSION or recipe['through'] and recipe['through']>=day:
            raise ValueError('ranking trained on target date')
        row=point(mid,code,day,v['forecasts'],v['quality'],v['shot_ready'])
        groups[code].append(dict(row,kickoff=aware(f['kickoff_at']).timestamp(),adjusted=probability(row,recipe['coef']),
                                 home=v['home_name'],away=v['away_name'],first_issue=r))
    selected={}
    for code,old_choices in old['payload']['identity']['selected'].items():
        if [r['id'] for r in ordered(groups[code],'raw')]!=[r['match_id'] for r in old_choices]:
            raise ValueError('raw R10 selection identity changed')
        selected[code]=[dict(match_id=v['id'],home=v['home'],away=v['away'],primary=v['primary'],top3=v['top3'],
                       raw=v['raw'],adjusted=v['adjusted'],first_issue=v['first_issue'])
                       for v in ordered(groups[code],'adjusted')]
    identity=dict(version=VERSION,weights_sha256=sha(weights),protocol_sha256=sha(protocol),r10_ranking_sha256=old['record_sha256'],
        source_registration=original['record_sha256'],choices=selected,formal_stage_certification=False)
    plan=ledger.register(list(fixtures.values()),identity,TRACK,out,freeze_root=freeze_root)
    if aware(plan['recorded_at'])<aware(old['recorded_at']):raise ValueError('ranked plan chronology')
    index=out/'plan.json'
    if index.exists():
        if read(index)!=plan:raise ValueError('conflicting ranking registration')
    else:write_new(index,plan)
    return plan


def forward_review(source,original_rank,out,results,evidence):
    original,fixtures,raw=ranking.population(source)
    prior=ledger._read(original_rank,read(original_rank/'plan.json'))
    signed=ledger._read(out,read(out/'plan.json'))
    ident=signed['payload']['identity']
    if (signed['track']!=TRACK+'-registration' or ident['source_registration']!=original['record_sha256']
            or ident['r10_ranking_sha256']!=prior['record_sha256'] or signed['payload']['fixtures']!=original['payload']['fixtures']
            or set(ident['choices'])!=set(prior['payload']['identity']['selected'])):
        raise ValueError('incorrect first-issue pool or reference ranking')
    actual={}
    for result in results:
        mid=result['match_id']
        if mid in actual or mid not in fixtures:raise ValueError('duplicate or out-of-scope result')
        h,a=_actual(result,fixtures[mid],evidence,datetime.now(timezone.utc))
        actual[mid]=f'{h}-{a}'
    refs=read(source/'receipts.json')['matches']
    counts={}
    for code,picks in ident['choices'].items():
        old=prior['payload']['identity']['selected'][code]
        if old!=raw.get(code,[]) or len(old)!=len(picks) or len({r['match_id'] for r in picks})!=len(picks):
            raise ValueError('ranked slots must match the original population')
        for r in picks:
            mid=r['match_id']
            if r['first_issue']!=refs[mid] or fixtures[mid]['cohort']!=code:raise ValueError('incorrect source issue')
            pick=ledger._read(source,refs[mid])['payload']['forecasts']['candidate']
            if r['primary']!=pick['primary'] or r['top3']!=pick['top3']:raise ValueError('original scores changed')
        counts[code]=dict(registered=sum(f['cohort']==code for f in fixtures.values()),
                          raw=ranking.sweep(old,actual),calibrated=ranking.sweep(picks,actual))
    return dict(version=VERSION,plan=signed['freeze_id'],settled_population=len(actual),competitions=counts,
                formal_stage_certification=False)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    cmd=parser.add_subparsers(dest='command',required=True)
    for name,fields in [('study',('data','protocol','out')),('issue',('source','original_rank','out','weights','protocol'))]:
        sub=cmd.add_parser(name)
        for f in fields:sub.add_argument('--'+f.replace('_','-'),required=True,type=Path)
    check=cmd.add_parser('review')
    for f in ('source','original_rank','out','report','results','evidence'):
        check.add_argument('--'+f.replace('_','-'),type=Path,required=f not in ('results','evidence'))
    args=vars(parser.parse_args());choice=args.pop('command')
    if choice=='study':run(**args)
    elif choice=='issue':print(issue(**args)['id'])
    else:
        path=args.pop('report')
        if args['results'] and not args['evidence']:parser.error('retained results need their evidence root')
        args['results']=read(args['results']) if args['results'] else []
        write_new(path,forward_review(**args))
