"""Optional authorized Japanese feed. Keys only enter HTTPS Authorization headers."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import time
import urllib.parse
import urllib.request
from registry import season_for
from store import now, digest

LEAGUES={3492701972:'jpn.2',3594495926:'jpn.centennial.2_3'}

def fetch_japan(store,raw_dir):
    key=os.environ.get('WCPP_5DOLLAR_API_KEY')
    if not key:return [{'provider':'5dollar','error':'ENV_BINDING_MISSING'}]
    logs=[]
    def get(path,code):
        url='https://api.5dollarfootballapi.com/v1'+path;observed=now()
        log={'url':url,'observed_at':observed,'provider':'5dollar','code':code};sid=None
        try:
            with urllib.request.urlopen(urllib.request.Request(url,headers={'Authorization':'Bearer '+key,'User-Agent':'WCPP-research'}),timeout=25) as r:
                content=r.read();log['http_status']=r.status
            data=json.loads(content.decode().replace(key,'[REDACTED]'))
            if path=='/status':data.get('data',{}).pop('key',None)
            if data.get('success')!=1:raise ValueError('API success flag failed')
            safe=json.dumps(data,ensure_ascii=False).encode();sha=hashlib.sha256(safe).hexdigest()
            p=Path(raw_dir)/(sha+'.json');p.write_bytes(safe);sid=store.source(p,'5dollar',url,observed)
            log.update(source_id=sid,rows=len(data.get('data',[])))
        except Exception as e:
            log['error']=type(e).__name__+': '+str(e).replace(key,'[REDACTED]');data=None
        store.db.execute('INSERT OR IGNORE INTO fetch_log VALUES(?,?,?,?,?,?,?)',(digest([url,observed]),url,'null',log.get('http_status'),observed,sid,log.get('error')))
        store.db.commit();logs.append(log);return data,sid,observed
    status,_,_=get('/status',None)
    if not status:return logs
    for league,code in LEAGUES.items():
        page=1
        while page<=30:
            q=urllib.parse.urlencode(dict(start_time=1767225600,end_time=int(dt.datetime.now(dt.timezone.utc).timestamp()),status='all',per_page=100,page=page))
            data,sid,observed=get(f'/leagues/{league}/fixtures?'+q,code)
            if not data:break
            for r in data.get('data',[]):
                kickoff=r.get('kickoff_utc');date=kickoff[:10] if kickoff else None
                if not date:store.issue('MISSING_DATE',sid,r.get('id'),r);continue
                ids=[]
                for side in ['home','away']:
                    t=r['teams'][side];generic=store.team('JPN',t['name']);pid='5dollar:'+str(t['id'])
                    # Reuse exact known identity; otherwise anchor a new provider-specific ID.
                    if generic.startswith('name:'):
                        store.db.execute('INSERT OR IGNORE INTO teams VALUES(?,?,?)',(pid,'JPN',t['name']))
                        store.merge(generic,pid,'5Dollar explicit team '+str(t['id']));generic=pid
                    ids.append(generic)
                complete=r.get('status')=='finished' and r.get('status_code')=='full'
                goals=r.get('goals') or {};h=goals.get('home');a=goals.get('away')
                # The centennial tournament can end with penalties; raw full totals are not
                # certified 90-minute scores. Keep those records outside training.
                certified=complete and code=='jpn.2' and isinstance(h,int) and isinstance(a,int)
                store.add(sid,dict(source_key='5dollar:'+str(r['id']),competition=code,season=season_for(code,date),date=date,kickoff_utc=kickoff,
                                   home_id=ids[0],away_id=ids[1],orientation='home_away',status='completed' if complete else r.get('status','unknown'),
                                   h90=h if certified else None,a90=a if certified else None,quality='single_source',score_scope='regulation_90' if certified else 'unknown',
                                   stage='regular' if code=='jpn.2' else 'centennial',provider='5dollar',observed_at=observed,raw=r))
            store.db.commit()
            if not data.get('pagination',{}).get('has_more'):break
            page+=1;time.sleep(3.1)
        season=season_for(code,dt.date.today().isoformat())
        total=store.db.execute("SELECT count(DISTINCT source_key) FROM observations WHERE provider='5dollar' AND competition=? AND season=? AND status='completed'",(code,season)).fetchone()[0]
        store.db.execute('INSERT OR REPLACE INTO coverage VALUES(?,?,?,?,?,?,?)',(code,season,'5dollar',total,380 if code=='jpn.2' else None,'IN_PROGRESS' if code=='jpn.2' else 'SOURCE_LIST_IMPORTED','Paginated API results; centennial displayed scores not automatically regulation-certified'))
        if code=='jpn.2' and total:
            store.db.execute("UPDATE coverage SET status='REPLACED_BY_API',detail='Current Japanese season is 2026/27, supplied by 5Dollar rather than this empty calendar-year page' WHERE code='jpn.2' AND season='2026' AND provider='betexplorer' AND completed=0")
        store.db.commit()
    return logs
