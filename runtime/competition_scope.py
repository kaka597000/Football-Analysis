"""One formal-analysis whitelist, separate from immutable legacy model identities."""
import json
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[1]/'configs/analysis-scope.json'

def policy():
    value=json.loads(CONFIG.read_text(encoding='utf-8'))
    codes=[r['code'] for r in value['competitions']]
    if len(codes)!=18 or len(set(codes))!=18:
        raise ValueError('exactly 18 unique competition identities required')
    return value

def permitted(item):
    if not isinstance(item,dict):return False
    cohort=item.get('cohort','')
    if not isinstance(cohort,str):return False
    if 'competition' in item and not isinstance(item['competition'],str):return False
    code=item.get('competition') or cohort.split(':',1)[0]
    if not isinstance(code,str):return False
    if cohort and code!=cohort.split(':',1)[0]:return False
    if code not in {r['code'] for r in policy()['competitions']}:return False
    if item.get('gender','men') not in ('men','male') or item.get('age','senior')!='senior':return False
    if item.get('score_basis','regulation_90')!='regulation_90':return False
    if code=='afc.champions' and ':ELITE:' not in cohort:return False
    if code=='fifa.world' and item.get('stage') in ('qualifying','qualification','friendly'):return False
    return True

def require(item):
    if not permitted(item):
        raise ValueError('outside 18-competition adult-men regulation-90 formal scope; historical assets retained')
    return item

def training_rows(rows):
    if not rows:raise ValueError('training history required')
    for row in rows:require(row)
    phased={r['code'] for r in policy()['competitions'] if r['requires_phase_identity']}
    if any(r['cohort'].split(':')[0] in phased and not r.get('stage') for r in rows):
        raise ValueError('explicit historical phase required before training')
    from research_program.phase_classification import classify
    classified=[classify(r) for r in rows]
    if any(not r['complete'] for r in classified):raise ValueError('unverified competition phase')
    identities={r['route_key'] for r in classified}
    if len(identities)!=1:raise ValueError('one competition cohort and explicit phase per training run')
    return rows

def inventory(registry):
    codes={r['code'] for r in policy()['competitions']}
    active=[k for k in registry['models'] if permitted({'cohort':k})]
    return {'scope_count':18,'retained_active_routes':active,
            'historical_only_routes':[k for k in registry['models'] if k not in active],
            'missing_competitions':sorted(codes-{k.split(':')[0] for k in active}),
            'registry_modified':False}
