"""Verify K League 1 league-stage regulation goals against a preserved official schedule response."""
from datetime import datetime
import json
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from research.analysis_freeze import aware


def regulation(row,evidence,path):
    url=urlsplit(evidence['url'])
    if (url.scheme!='https' or url.hostname not in ('www.kleague.com','kleague.com')
            or url.path!='/getScheduleList.do' or row['cohort']!='kor.1'):
        raise ValueError('unrecognized K League result source')
    raw=json.loads(path.read_bytes())
    ko=aware(row['kickoff_at']).astimezone(ZoneInfo('Asia/Seoul'))
    # gameId restarts each season. Limit the lookup to K1 and the kickoff year.
    games=[g for g in raw['data']['scheduleList'] if g.get('year')==ko.year and
           g.get('leagueId')==1 and 'kleague:'+str(g['gameId'])==row['match_id']]
    if str(raw['resultCode'])!='200' or len(games)!=1:
        raise ValueError('ambiguous official K1 result')
    g=games[0]
    local=datetime.strptime(g['gameDate']+' '+g['gameTime'],'%Y.%m.%d %H:%M').replace(tzinfo=ko.tzinfo)
    if (local!=ko or g['codeName']!=row['stage'] or not 1<=g['roundId']<=38
            or g['gameStatus']!='FE' or g['endYn']!='Y' or g['codeName'] not in ('정규라운드','정규리그','파이널A','파이널B')):
        raise ValueError('K1 source does not confirm the scheduled finished league match')
    for side in ('home','away'):
        score=g[side+'Goal']
        if ('kleague:'+g[side+'Team']!=row[side] or type(score) is not int or score!=row[side+'_goals']):
            raise ValueError('K1 team or full-time score mismatch')
