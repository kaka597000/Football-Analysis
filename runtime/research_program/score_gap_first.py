"""Apply the registered first-score decision to archived, already ordered pre-match opinions."""
import argparse
import json
from pathlib import Path

from research.analysis_freeze import DEFAULT_ROOT
from . import all_scope_study as study
from . import goal_state_forward as ledger
from .goal_state_study import read_lines,sha,write_new


def decisions(forecasts, quality):
    raw=forecasts['conditional']
    previous=forecasts['r5']['primary']
    first=raw['primary']
    price=(forecasts.get('price_pair_proxy') or {}).get('primary')
    confirmed=(price==first==forecasts['no_process']['primary'] and
               quality['conditional']['p1']>=.4*quality['conditional']['p3'])
    def ranked(primary):
        top3=[primary]+[s for s in raw['top3'] if s!=primary][:2]
        return dict(raw,primary=primary,top3=top3)
    return {'retained_first':ranked(previous),'agreement':ranked(first if confirmed else previous)}


def historical(directory):
    source=directory/'predictions.jsonl'
    rows=[]
    for r in read_lines(source):
        rows.append(dict(id=r['id'],window=r['window'],forecast=decisions(r['forecast'],r['quality'])))
    study.store_lines(directory/'first-policy.jsonl',rows)


def issue(original,protocol,output,*,freeze_root=DEFAULT_ROOT):
    index=json.loads((original/'receipts.json').read_bytes())
    original_plan=ledger._read(original,index['registration'])
    rows=[ledger._read(original,x) for x in index['matches'].values()]
    pairs={k:[k,'agreement'] for k in ('r5','conditional','retained_first','market','market_priority')}
    track='research-score-gap-agreement-20261123'
    identity=dict(competitions=original_plan['payload']['identity']['competitions'],pairs=pairs,
                  original_plan_sha256=index['registration']['sha256'],protocol_sha256=sha(protocol))
    plan=ledger.register([r['fixture'] for r in rows],identity,track,output,freeze_root=freeze_root)
    refs={}
    for r in rows:
        d=r['payload']
        f=dict(r['fixture'],cutoff_at=ledger._now())
        picks=dict(d['forecasts'],**decisions(d['forecasts'],d['quality']))
        payload=dict(line=d['line'],forecasts=picks,conditional_first_issue=r['record_sha256'],
                     model_sha256=d['model_sha256'],home_name=d['home_name'],away_name=d['away_name'])
        refs[f['match_id']]=ledger.issue(plan,f,payload,track,output,freeze_root=freeze_root)
    write_new(output/'receipts.json',dict(registration=plan,matches=refs))
    write_new(output/'pending.json',ledger.review(output,[],output,by_competition=True))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='action',required=True)
    h=sub.add_parser('historical');h.add_argument('directory',type=Path)
    f=sub.add_parser('issue')
    for name in ('original','protocol','output'):f.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args()
    if a.action=='historical':historical(a.directory)
    else:issue(a.original,a.protocol,a.output)
