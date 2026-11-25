"""Issue a new research prediction beside previously frozen R6 opinions on the same unstarted fixtures."""
import argparse
from datetime import date
import json
from pathlib import Path

import numpy as np

from research.analysis_freeze import DEFAULT_ROOT, aware
from . import all_scope_model as base
from . import all_scope_study as old
from . import continuation_scores as model
from . import goal_state_forward as ledger
from .goal_state_study import sha, write_new
from .kpi_validation import fixture_identity


def read(path):
    return json.loads(path.read_bytes())


def matrix(game,recipe):
    d=date.fromisoformat(game['date']).toordinal()
    state=model.load(recipe['state'])
    hl=old.half_life(recipe['competition'])
    base.advance(state['goals'],d,hl)
    prior,x,_=model.prepare(state,game,d,hl)
    p=recipe['policy']
    if p['mix']:
        hazards=model.probabilities(model.hazard_inputs(prior[None,:,:],np.array([x])),recipe['model'],p['arm'])
        fitted=model.reconstruct(prior[None,:,:],*hazards)[0]
        probabilities=(1-p['mix'])*prior+p['mix']*fitted
    else:
        probabilities=prior
    return probabilities


def predict(game,recipe):
    probabilities=matrix(game,recipe)
    return base.pick(probabilities,base.actual_line(game)),{
        'p_first':float(probabilities.max()),'p_three':float(np.partition(probabilities.ravel(),-3)[-3:].sum()),
        'wdl':[float(probabilities[mask].sum()) for mask in (base.H>base.A,base.H==base.A,base.H<base.A)]}


def issue(recipes,inputs,control,scope,protocol,output,*,freeze_root=DEFAULT_ROOT):
    ref=read(control/'receipts.json');src=read(inputs/'receipts.json')
    if set(ref['matches'])!=set(src['matches']):
        raise ValueError('inputs and control populations differ')
    codes=[x['code'] for x in read(scope)['competitions']]
    weights={c:read(recipes/c/'forward.json') for c in codes}
    hashes={c:sha(recipes/c/'forward.json') for c in codes}
    records=[];expired=[];now=aware(ledger._now())
    for mid,receipt in src['matches'].items():
        r=ledger._read(inputs,receipt)
        other=ledger._read(control,ref['matches'][mid])
        if fixture_identity(r['fixture'])!=fixture_identity(other['fixture']):
            raise ValueError('unaligned frozen input: '+mid)
        if aware(r['fixture']['kickoff_at'])<=now:
            expired.append(mid)
        else:
            records.append((r,other))
    track='research-prematch-continuation-20261123'
    ident=dict(competitions=codes,pairs={'vs_r6':['r6','candidate'],'vs_market':['market','candidate']},
        version=model.VERSION,protocol_sha256=sha(protocol),recipe_sha256=hashes,
        old_registration_sha256=ref['registration']['sha256'],already_started_at_registration=sorted(expired))
    plan=ledger.register([r['fixture'] for r,_ in records],ident,track,output,freeze_root=freeze_root)
    receipts={}
    for r,other in records:
        f=dict(r['fixture'],cutoff_at=ledger._now());code=f['cohort'];mid=f['match_id']
        orig=r['payload'];w=weights[code]
        if (w['competition']!=code or w['version']!=model.VERSION or
            date.fromisoformat(w['through'])>=aware(f['cutoff_at']).date() or
            aware(f['cutoff_at'])<max(aware(r['recorded_at']),aware(other['recorded_at']))):
            raise ValueError('inconsistent prediction time or artifact: '+mid)
        game=dict(id=mid,competition=code,stage=orig['model_stage'],date=aware(f['kickoff_at']).date().isoformat(),
            home=orig['model_teams'][0],away=orig['model_teams'][1],neutral=orig['neutral'],
            paired_market=orig['market_mode']=='paired',**orig['prices'])
        forecast,q=predict(game,w)
        picks=other['payload']['forecasts']
        if forecast['total_line']!=orig['line']:
            raise ValueError('the frozen line changed')
        payload=dict(forecasts=dict(candidate=forecast,r6=picks['agreement'],market=picks['market']),
            line=orig['line'],quality=q,training_through=w['through'],home_name=orig['home_name'],
            away_name=orig['away_name'],model_sha256=hashes[code],input_sha256=r['record_sha256'],
            r6_first_issue=other['record_sha256'])
        receipts[mid]=ledger.issue(plan,f,payload,track,output,freeze_root=freeze_root)
    write_new(output/'receipts.json',dict(registration=plan,matches=receipts))
    result=ledger.review(output,[],output,by_competition=True)
    write_new(output/'pending.json',result)
    return len(receipts),len(expired)


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    for field in ('recipes','inputs','control','scope','protocol','output'):
        ap.add_argument('--'+field,type=Path,required=True)
    a=ap.parse_args()
    print(issue(a.recipes,a.inputs,a.control,a.scope,a.protocol,a.output))
