"""Convert an actually captured ESPN prematch snapshot to the classifier contract."""
from datetime import datetime
from classifier import aware,fair

def from_espn(fixture,capture):
 if capture.get('http_status')!=200:raise ValueError('successful saved source required')
 d=capture.get('response') or {};cs=d.get('header',{}).get('competitions',[])
 if len(cs)!=1:raise ValueError('unique fixture source required')
 c=cs[0];teams={x['homeAway']:x.get('team',{}).get('id') for x in c.get('competitors',[])}
 if 'espn:'+str(c.get('id'))!=fixture['match_id'] or any('espn:'+str(teams.get(s))!=fixture[s] for s in ('home','away')):raise ValueError('quote fixture identity mismatch')
 if aware(c['date'])!=aware(fixture['kickoff_at']) or c.get('status',{}).get('type',{}).get('state')!='pre':raise ValueError('prematch source required')
 if aware(capture['query_time'])>aware(fixture['cutoff_at']):raise ValueError('postcutoff capture')
 picks=d.get('pickcenter') or []
 if not picks:raise ValueError('no actual 1X2 bookmaker quote')
 x=picks[0]
 def decimal(v):
  if type(v) not in (float,int) or v==0:raise ValueError('invalid american odds')
  return 1+v/100 if v>0 else 1+100/abs(v)
 odds=[decimal(x.get('homeTeamOdds',{}).get('moneyLine')),decimal(x.get('drawOdds',{}).get('moneyLine')),decimal(x.get('awayTeamOdds',{}).get('moneyLine'))];fair(odds)
 return dict(match_id=fixture['match_id'],market='1x2',verified=True,source_url=capture['url'],query_time=capture['query_time'],quoted_at=None,bookmaker=x['provider']['name'],home_odds=odds[0],draw_odds=odds[1],away_odds=odds[2],source='actual captured ESPN pickcenter; bookmaker quote timestamp unavailable')
