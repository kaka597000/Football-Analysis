"""Capture real future fixture context for prospective evidence accumulation."""
import argparse
import json
import urllib.request
from datetime import datetime,timezone
from pathlib import Path
from model import aware

def snapshot(league,event,line=None):
    if league not in ['eng.1','uefa.nations','fifa.friendly','usa.1']:raise ValueError('unsupported league')
    if not str(event).isdigit():raise ValueError('invalid event ID')
    url=f'https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/summary?event={event}'
    with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'WCPP-research/0.2'}),timeout=30) as f:raw=json.load(f)
    now=datetime.now(timezone.utc);comp=raw['header']['competitions'][0]
    if comp['status']['type']['state']!='pre' or aware(comp['date'])<=now:raise ValueError('fixture no longer prematch')
    h=next(c for c in comp['competitors'] if c['homeAway']=='home');a=next(c for c in comp['competitors'] if c['homeAway']=='away')
    provider=(raw.get('pickcenter') or [{}])[0]
    market_line=provider.get('overUnder')
    chosen=line if line is not None else market_line
    if chosen is None:raise ValueError('no market total; supply explicit research --line')
    roster=raw.get('rosters',[])
    players=[p for r in roster for p in r.get('roster',r.get('athletes',[]))]
    fixture={'match_id':str(event),'cohort':'EPL' if league=='eng.1' else league,
        'home':h['team']['displayName'],'away':a['team']['displayName'],'neutral':bool(comp.get('neutralSite',False)),
        'kickoff_at':comp['date'],'cutoff_at':now.isoformat(),'phase':'PreMatch','total_line':float(chosen),
        'line_provenance':'explicit research line' if line is not None else 'ESPN quoting '+provider.get('provider',{}).get('name','unknown'),
        'identity_scope':'extended','identity_verified':False,'context_audit_complete':False,
        'sources':[{'source_url':url,'source_tier':'L2','query_time':now.isoformat(),'supports_field':['fixture','market_snapshot']}],
        'context_observation':{'players_returned':len(players),'official_lineup_verified':False,'rosters':roster},
        'market_observation':{'raw_quote':provider,'source_timestamp':None,'odds_timestamp_verified':False},
        'data_gaps':['official lineup not verified','injury and bench availability not verified','motivation/integrity audit incomplete','quote source timestamp unavailable']}
    return raw,fixture

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('league');p.add_argument('event');p.add_argument('--line',type=float);p.add_argument('--directory',required=True);a=p.parse_args()
    raw,fixture=snapshot(a.league,a.event,a.line);d=Path(a.directory);d.mkdir(parents=True,exist_ok=True)
    (d/'raw-summary.json').write_text(json.dumps(raw,ensure_ascii=False,indent=2))
    with (d/'fixture.json').open('x') as f:json.dump(fixture,f,ensure_ascii=False,indent=2)
    print(str(d/'fixture.json'))
