"""Read regulation goals from the first two completed halves of a retained knockout summary."""
import json
from urllib.parse import urlsplit
from research.analysis_freeze import aware


def verify(row,evidence,path):
    url=urlsplit(evidence['url'])
    if url.scheme!='https' or url.hostname not in {'site.api.espn.com','site.web.api.espn.com'}:
        raise ValueError('unsupported knockout result source')
    body=json.loads(path.read_bytes())
    header=body['header']
    regulation=body.get('format',{}).get('regulation',{})
    if (header['league']['slug']!=row['cohort'] or 'espn:'+str(header['id'])!=row['match_id']
            or len(header['competitions'])!=1 or regulation.get('periods')!=2
            or regulation.get('slug')!='half' or regulation.get('clock')!=2700):
        raise ValueError('knockout match or regulation format mismatch')
    game=header['competitions'][0]
    status=game['status']['type']
    if (aware(game['date'])!=aware(row['kickoff_at']) or status.get('completed') is not True
            or status['name'] not in {'STATUS_FINAL_PEN','STATUS_FINAL_AET','STATUS_FULL_TIME_EXTRA_TIME','STATUS_FULL_TIME_PEN'}):
        raise ValueError('completed knockout match required')
    teams={t['homeAway']:t for t in game['competitors']}
    if len(game['competitors'])!=2 or set(teams)!={'home','away'}:
        raise ValueError('incomplete knockout participants')
    for side,t in teams.items():
        halves=t.get('linescores') or []
        if 'espn:'+str(t['team']['id'])!=row[side] or len(halves)<3:
            raise ValueError('knockout team or period scores missing')
        goals=0
        for period,h in enumerate(halves[:2],1):
            s=h.get('displayValue')
            if str(h.get('period',period))!=str(period) or not isinstance(s,str) or not s.isdecimal():
                raise ValueError('unidentified regulation half')
            goals+=int(s)
        if goals!=row[side+'_goals']:
            raise ValueError('reported score differs from regulation halves')
