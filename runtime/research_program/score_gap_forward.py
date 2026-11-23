"""Issue the registered conditional model beside two already frozen research controls."""
import argparse
from datetime import date
import json
from pathlib import Path

from research.analysis_freeze import DEFAULT_ROOT, aware
from . import all_scope_study as study
from . import conditional_scores as model
from . import goal_state_forward as ledger
from .goal_state_study import sha, write_new
from .kpi_validation import fixture_identity


def read(path):
    return json.loads(path.read_bytes())


def predict_fixture(fixture, archived, recipe):
    mid,code = fixture['match_id'],fixture['cohort']
    day = aware(fixture['kickoff_at']).date()
    g = dict(id=mid,competition=code,date=day.isoformat(),stage=archived['model_stage'],
             home=archived['model_teams'][0],away=archived['model_teams'][1],neutral=archived['neutral'],
             paired_market=archived['market_mode']=='paired',**archived['prices'])
    state = model.load(recipe['state'])
    hl = study.half_life(code)
    model.advance(state,[g],day.toordinal(),hl)
    p = model.components(state,g,day.toordinal(),hl)
    picks,quality = model.forecast(g,p,recipe['coefficients'])
    return picks, quality, p


def issue_all(models, previous, price_priority, scope, protocol, output, *, freeze_root=DEFAULT_ROOT):
    original = read(previous/'receipts.json')
    price = read(price_priority/'receipts.json')
    codes = [entry['code'] for entry in read(scope)['competitions']]
    if original['matches'].keys()!=price['matches'].keys():
        raise ValueError('the two archived controls have different populations')
    models_by_code = {c:read(models/c/'forward-model.json') for c in codes}
    hashes = {c:sha(models/c/'forward-model.json') for c in codes}
    track = 'research-score-gap-conditional-20261123'
    pairs = dict(vs_previous=['r5','conditional'],vs_market_priority=['market_priority','conditional'],
                 vs_market=['market','conditional'],vs_without_process=['no_process','conditional'])
    identity = dict(competitions=codes,pairs=pairs,model_version=model.VERSION,model_sha256=hashes,
                    protocol_sha256=sha(protocol),previous_registration=original['registration']['sha256'],
                    price_registration=price['registration']['sha256'])
    records = [ledger._read(previous,ref) for ref in original['matches'].values()]
    plan = ledger.register([r['fixture'] for r in records],identity,track,output,freeze_root=freeze_root)
    refs = {}
    for r in records:
        f = dict(r['fixture'])
        mid,code = f['match_id'],f['cohort']
        control = ledger._read(price_priority,price['matches'][mid])
        if fixture_identity(control['fixture'])!=fixture_identity(f):
            raise ValueError('price comparator fixture mismatch: '+mid)
        archived = r['payload']
        recipe = models_by_code[code]
        f['cutoff_at'] = ledger._now()
        if (recipe['version']!=model.VERSION or recipe['competition']!=code or
                date.fromisoformat(recipe['through']) >= aware(f['cutoff_at']).date() or
                aware(r['recorded_at'])>aware(f['cutoff_at']) or aware(control['recorded_at'])>aware(f['cutoff_at'])):
            raise ValueError('invalid historical or frozen input cutoff')
        picks,quality,p = predict_fixture(f,archived,recipe)
        picks['r5'] = archived['forecasts']['neighbor']
        picks['market_priority'] = control['payload']['forecasts']['market_priority']
        if picks['market'] != archived['forecasts']['market'] or archived['line']!=picks['conditional']['total_line']:
            raise ValueError('market input changed since initial freeze: '+mid)
        payload = dict(forecasts=picks,quality=quality,line=archived['line'],model_sha256=hashes[code],
            model_teams=archived['model_teams'],model_stage=archived['model_stage'],training_through=recipe['through'],
            previous_sha256=r['record_sha256'],price_control_sha256=control['record_sha256'],
            price_source=control['payload']['score_source'],market_basis=p['market']['kind'],
            prices=archived['prices'],market_evidence=archived['market_evidence'],
            home_name=archived['home_name'],away_name=archived['away_name'],process_exposure=p['process_exposure'])
        refs[mid] = ledger.issue(plan,f,payload,track,output,freeze_root=freeze_root)
    write_new(output/'receipts.json',dict(registration=plan,matches=refs))
    return ledger.review(output,[],output,by_competition=True)


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    for field in ('models','previous','price-priority','scope','protocol','output'):
        ap.add_argument('--'+field,required=True,type=Path)
    args=ap.parse_args()
    result=issue_all(args.models,args.previous,args.price_priority,args.scope,args.protocol,args.output)
    write_new(args.output/'pending.json',result)
