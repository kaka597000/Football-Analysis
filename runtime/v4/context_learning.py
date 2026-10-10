"""Train comprehensive context from real, auditable frozen forecasts only."""
from datetime import datetime, timezone, timedelta
from main_model import VERSION, aware, digest, url, code_hash
from context import assess
from evidence import validate_context, context_rates
from competition_router import classify, validate_training, format_audit

CONTRACT = 'wcpp-main-source-backed-context-1'
POISSON_RECIPES = ('baseline','strength365','strength180','form180')


def baseline_contract(artifact):
    return digest({'version':VERSION,'code_hash':code_hash(),'config':artifact['config'],
                   'cohort':artifact['cohort'],'route_key':validate_training(artifact['history'])['route_key'],
                   'recipe':artifact['execution_recipe']})


def prepare(samples,artifact):
    if artifact['execution_recipe'] not in POISSON_RECIPES: raise ValueError('context validation supports Poisson recipes only')
    rows=[]; ids=set(); now=datetime.now(timezone.utc)
    for s in samples:
        f,r=s['forecast'],s['result']; fixture=f['input']; mid=fixture['match_id']
        if mid in ids: raise ValueError('duplicate real context sample')
        ids.add(mid)
        if f.get('version')!=VERSION or f.get('freeze_status')!='NEW_MAIN_RESEARCH_FREEZE': raise ValueError('requires actual main research freeze')
        if digest({k:v for k,v in f.items() if k!='record_hash'})!=f.get('record_hash'): raise ValueError('tampered training forecast')
        if f['baseline_contract']!=baseline_contract(artifact) or f['recipe']!=artifact['execution_recipe']:
            raise ValueError('incompatible baseline contract')
        if f.get('context_numeric_adjustment_applied'): raise ValueError('context targets must use unadjusted baseline forecasts')
        kickoff=aware(fixture['kickoff_at']); cutoff=aware(fixture['cutoff_at'])
        if not cutoff <= aware(f['generated_at']) <= aware(f['frozen_at']) < kickoff:
            raise ValueError('invalid training forecast chronology')
        if r['match_id']!=mid or r.get('score_basis')!='regulation_90' or not url(r.get('source_url')):
            raise ValueError('sourced 90-minute matching result required')
        if not kickoff+timedelta(minutes=90) <= aware(r['observed_at']) <= now: raise ValueError('invalid result timing')
        if any(type(r[k]) is not int or not 0<=r[k]<=30 for k in ('home_goals','away_goals')): raise ValueError('invalid target goals')
        review=assess(fixture)
        route=classify(fixture)
        if not route['complete'] or route['route_key'] != validate_training(artifact['history'])['route_key']:
            raise ValueError('context training competition route mismatch')
        if format_audit(fixture,route): raise ValueError('context training competition rules incomplete')
        if not review['complete'] or any(e['source_tier']!='L1' for e in review['evidence']):
            raise ValueError('complete official source-backed context required for numerical training')
        rows.append({'cohort':fixture['cohort'],'kickoff_at':fixture['kickoff_at'],'cutoff_at':fixture['cutoff_at'],
                     'baseline_observed_at':max((f['model_built_at'],f['model_observed_at']),key=aware),
                     'baseline_home':f['rates']['means'][0],'baseline_away':f['rates']['means'][1],
                     'result_observed_at':r['observed_at'],'home_goals':r['home_goals'],'away_goals':r['away_goals'],
                     'features':review['features'],'evidence':review['evidence']})
    if not rows: raise ValueError('no real frozen source-backed context samples available')
    return rows


def train_context(artifact,samples):
    rows=prepare(samples,artifact); names=sorted(rows[0]['features'])
    if any(sorted(r['features'])!=names for r in rows): raise ValueError('context schema mismatch')
    learned=validate_context(rows,names,min_rows=200)
    learned.update(feature_contract=CONTRACT,baseline_contract=baseline_contract(artifact))
    updated=dict(artifact,context_coefficients=learned,built_at=datetime.now(timezone.utc).isoformat())
    return updated


def adjusted_rates(artifact,review,lh,la,cutoff):
    coefficients=artifact.get('context_coefficients')
    if coefficients and artifact['execution_recipe'] not in POISSON_RECIPES:
        raise ValueError('context cannot replace non-Poisson or mixture distributions')
    if not coefficients or coefficients.get('validated') is not True:
        return lh,la,False,'Context coefficients unavailable or validation failed'
    if coefficients.get('feature_contract')!=CONTRACT or coefficients.get('baseline_contract')!=baseline_contract(artifact):
        raise ValueError('incompatible context artifact')
    if aware(coefficients['observed_at'])>aware(cutoff): raise ValueError('later-observed context artifact')
    if not review['complete'] or any(e['source_tier']!='L1' for e in review['evidence']):
        return lh,la,False,'Complete official context unavailable; retain unadjusted baseline'
    if set(coefficients['features'])!=set(review['features']): raise ValueError('context feature mismatch')
    lh,la=context_rates(lh,la,review['features'],coefficients,review['evidence'],cutoff)
    return lh,la,True,'Temporally validated source-backed context applied'
