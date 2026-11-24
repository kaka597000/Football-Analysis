"""Export the same original freeze and label evidence from the pinned, read-only #69 auditor."""

import argparse
from collections import defaultdict
from datetime import date
import json
from importlib.util import find_spec
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

from research.analysis_freeze import aware
from research_program.market_baseline import predict as market_baseline

PROTOCOL=json.loads((Path(__file__).resolve().parent/'protocol.json').read_bytes())
LEAGUES=set(PROTOCOL['domestic_sources'].values())


def verify_auditor():
    # Resolve the explicit research environment before loading the pinned prior auditor.
    specs = [find_spec(name) for name in ('archive', 'replay')]
    if any(spec is None or spec.origin is None for spec in specs):
        raise ValueError('add the pinned audit checkout to the research PYTHONPATH')
    a, r = [Path(spec.origin).resolve() for spec in specs]
    source = a.parents[2]
    module = source/'research/20261124-prior-dismissals'
    if a.parent != module or r.parent != module:
        raise ValueError('wrong audit module loaded')
    sha=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
    if sha!=PROTOCOL['read_only_audit_source']:
        raise ValueError('wrong audit commit')
    subprocess.run(['git','-C',str(source),'diff','--quiet','HEAD','--','runtime',str(module)],check=True)


def full_source_market(archives,f,audit):
    if archives.quote_line(f) is None:
        return None
    model=market_baseline(f)
    if model['status']!='AVAILABLE':
        return None
    snap=model['quote_snapshot']
    source=snap['source_sha256']
    raw=json.loads(audit.checked(archives.assets.extractfile(f'raw/open-pregame/{source}.json').read(),source))
    prices = retained_h2h(raw,snap,f['kickoff_at'])
    if prices is None:
        return None
    return dict(h2h=prices,total=dict(snap['total']['prices'],line=snap['total']['line']))


def retained_h2h(raw,snap,kickoff):
    games=[e for e in raw if e['id']==snap['provider_match_id'] and aware(e['commence_time'])==aware(kickoff)]
    if len(games)!=1:
        return None
    game=games[0]
    names={game['home_team']:'home',game['away_team']:'away','Draw':'draw'}
    good=[]
    for b in game['bookmakers']:
        if b['key']!=snap['bookmaker']:
            continue
        for m in b['markets']:
            if m['key']!='h2h' or aware(m.get('last_update') or b['last_update'])!=aware(snap['h2h']['quoted_at']):
                continue
            outcomes=m['outcomes']
            native={names.get(o['name']):o['price'] for o in outcomes}
            if len(outcomes)==3 and set(native)=={'home','draw','away'} and native==snap['h2h']['prices']:
                good.append(native)
    if len(good)!=1:
        return None
    return good[0]


def six_games(rows, home, away, day, audit):
    prior=sorted((r for r in rows if r['day']<day),key=lambda r:r['day'])
    chosen={}
    for club in (home,away):
        for r in [g for g in prior if g['home']==club or g['away']==club][-6:]:
            raw=r['raw']
            half_fields=[str(raw.get(k,'')).strip() for k in ('HTHG','HTAG')]
            ht=list(map(int,half_fields)) if all(v.isascii() and v.isdecimal() for v in half_fields) else None
            key=(r['day'].isoformat(),r['home'],r['away'])
            chosen[key]=[*key,ht,audit.goals(raw)]
    return [chosen[k] for k in sorted(chosen)]


def inputs(archives,fixtures,directory,audit,evidence):
    aliases=evidence.prior_aliases(archives,min(aware(r['cutoff_at']) for r in fixtures),directory)
    records=[]
    for r in fixtures:
        league=r['cohort']
        day=aware(r['kickoff_at']).date()
        identity=[league,r['home'],r['away'],r['kickoff_at']]
        item=dict(id=r['match_id'],league=league,window='clocked',day=day.isoformat(),
                  cutoff=r['cutoff_at'],identity=identity,freeze_sha256=r['freeze_reference']['sha256'],
                  market=None,feature_status='missing_verified_market')
        records.append(item)
        if r['match_id'] in archives.r7_ids or audit.identity(r) in archives.r7_keys:
            item['feature_status']='excluded_R7'
            continue
        if r['stage']!='regular':
            item['feature_status']='outside_regular_phase'
            continue
        f=archives.original(r)
        if f is None:
            item['feature_status']='missing_bound_original'
            continue
        item['market']=full_source_market(archives,f,audit)
        if item['market'] is None:
            continue
        if league not in LEAGUES:
            item['feature_status']='no_trained_league_source'
            continue
        season,asof=archives.season(league,2026)
        if asof is None or asof>=aware(f['cutoff_at']):
            item['feature_status']='no_capture_before_cutoff'
            continue
        prior=[g for g in season if g['day']<day]
        clubs={g[k] for g in prior for k in ('home','away')}
        selected=[audit.resolve_club({f[k+'_name'],*aliases[league,f[k]]},clubs) for k in ('home','away')]
        if None in selected or selected[0]==selected[1]:
            item['feature_status']='unresolved_club'
            continue
        item.update(feature_status='eligible_inputs',clubs=selected,prior_games=six_games(prior,*selected,day,audit))
    return records


def labels(archives,fixtures,directory,audit,evidence):
    table=defaultdict(list)
    for r in map(json.loads,audit.blob(archives.repo,audit.PIN,'normalized-results.jsonl').splitlines()):
        table[r['match_id']].append(r)
    records=[]
    for f in fixtures:
        candidates=[r for r in table[f['match_id']] if audit.identity(r)==audit.identity(f)]
        score=None
        reason='ambiguous_or_absent'
        if f['stage']!='regular':
            reason='outside_regular_phase'
        elif len(candidates)==1:
            r=candidates[0]
            try:
                evidence.verified_summary(archives.repo,audit.PIN,r,directory)
            except ValueError as exc:
                reason=str(exc)
            else:
                score=[r['home_goals'],r['away_goals']]
                reason='verified_regulation_90'
        records.append(dict(id=f['match_id'],freeze_sha256=f['freeze_reference']['sha256'],
                            identity=[f['cohort'],f['home'],f['away'],f['kickoff_at']],score=score,reason=reason))
    return records


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=['inputs','labels'])
    p.add_argument('--data-repo',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    args=p.parse_args()
    verify_auditor()
    import archive as audit
    import replay as evidence
    archives=audit.Archives(args.data_repo)
    fixtures=archives.targets()
    with TemporaryDirectory() as tmp:
        records=(inputs if args.mode=='inputs' else labels)(archives,fixtures,Path(tmp),audit,evidence)
    data=b''.join(evidence.packed(r) for r in sorted(records,key=lambda r:r['id']))
    args.out.parent.mkdir(parents=True,exist_ok=True)
    evidence.put(args.out,data)
    print(args.mode,len(records),flush=True)
