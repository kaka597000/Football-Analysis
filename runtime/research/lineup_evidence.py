"""Lineup policy outside hash-locked numerical engines; never edits probabilities."""
from datetime import datetime
from urllib.parse import urlsplit,parse_qsl

def aware(s):
    t=datetime.fromisoformat(s.replace('Z','+00:00'))
    if t.tzinfo is None:raise ValueError('timezone required')
    return t

def assess(block,fixture):
    try:
        if block.get('status') not in ('documented','unknown'):raise ValueError('status')
        if not block.get('summary') or not block.get('evidence'):raise ValueError('evidence')
        for e in block['evidence']:
            u=urlsplit(e['source_url'])
            if (u.scheme!='https' or not u.hostname or u.username or u.password
                    or any(k.lower() in {'key','api_key','apikey','token','access_token'} for k,_ in parse_qsl(u.query))):raise ValueError('source')
            if e.get('source_tier') not in ('L1','L2','L3') or 'lineup' not in e.get('supports_field',[]):raise ValueError('scope')
            if aware(e['query_time'])>aware(fixture['cutoff_at']):raise ValueError('late')
            for k in ('known_at','source_published_at'):
                if e.get(k) and aware(e[k])>aware(e['query_time']):raise ValueError('knowledge')
        official=block.get('official_confirmed') is True
        if official:
            if (not any(e['source_tier']=='L1' for e in block['evidence'])
                    or len(block.get('starters',[]))!=11 or len(set(block['starters']))!=11):raise ValueError('official XI')
        elif (block.get('lineup_status') not in ('unannounced','projected')
              or not isinstance(block.get('scenario_analysis'),str) or not block['scenario_analysis'].strip()
              or not isinstance(block.get('uncertainty'),str) or not block['uncertainty'].strip()
              or block.get('material_selection_risk') is not False):raise ValueError('scenario/risk')
        return {'documented':True,'official_confirmed':official,'numeric_prediction_modified':False,
                'basis':'OFFICIAL_XI' if official else 'EXPLICIT_LINEUP_UNCERTAINTY_SCENARIO'}
    except (ValueError,KeyError,TypeError,AttributeError):
        return {'documented':False,'official_confirmed':False,'numeric_prediction_modified':False,'basis':'MISSING_OR_UNRESOLVED_LINEUP_EVIDENCE'}
