"""Read-only source adapters. Result revisions never overwrite evidence."""
import concurrent.futures
import csv
import datetime as dt
import io
import html
import json
import re
import sqlite3
import urllib.request
from pathlib import Path
from registry import LEAGUES, competition, season_for, expected_fd
from store import digest, now

ESPN_UNAVAILABLE={'kor.1','kor.2','kor.cup','jpn.2','jpn.league_cup','jpn.emperor','jpn.centennial.1','jpn.centennial.2_3'}

def utc(value):
    if not value:return None
    return dt.datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(dt.timezone.utc).isoformat()

def espn(store, data, sid, code, observed):
    events=data.get('events',[]) if 'events' in data else [data['header']] if 'header' in data else []
    for event in events:
        event_code=code
        event_slug=event.get('season',{}).get('slug','')
        if code=='jpn.1' and ('100-year' in event_slug or (event.get('date','')<'2026-07-01' and event.get('date','').startswith('2026') and event_slug in ('placement-playoffs','championship'))):event_code='jpn.centennial.1'
        for c in event.get('competitions',[]):
            teams=c.get('competitors',[])
            if len(teams)!=2:continue
            home=next((x for x in teams if x.get('homeAway')=='home'),None)
            away=next((x for x in teams if x.get('homeAway')=='away'),None)
            if not home or not away:continue
            status=c.get('status',event.get('status',{}));typ=status.get('type',{})
            kickoff=utc(c.get('date') or event.get('date'))
            if not kickoff:store.issue('MISSING_DATE',sid,event.get('id'),event);continue
            date=kickoff[:10]
            ids=[]
            for team in (home,away):
                t=team['team'];names=[t.get(k) for k in ['displayName','name','shortDisplayName'] if t.get(k)]
                ids.append(store.team(LEAGUES[code][1],names[0],t['id'],names[1:],f'ESPN team {t["id"]}, source {sid}'))
            completed=bool(typ.get('completed')) and kickoff<now()
            hs=ass=None;basis='unknown';extras=[None]*4
            if completed and typ.get('name')=='STATUS_FULL_TIME' and status.get('period',2)<=2:
                try:hs,ass=int(home['score']),int(away['score']);basis='regulation_90'
                except (ValueError,KeyError,TypeError):pass
            elif completed:
                # Never use the displayed final score for AET or shootouts.
                lines=[x.get('linescores',[]) for x in (home,away)]
                if all(len(x)>=2 and all('value' in s for s in x[:2]) for x in lines):
                    hs,ass=[sum(int(s['value']) for s in x[:2]) for x in lines];basis='regulation_90'
                else:store.issue('REGULATION_UNRESOLVED',sid,event.get('id'),{'status':typ,'competition':code})
            declared=None
            slug=event.get('season',{}).get('slug','')
            match=re.match(r'(20\d{2})-(\d{2})-',slug)
            if match:declared=match.group(1)+'/'+match.group(2)
            stage=slug if not match else 'regular'
            if 'relegation' in slug:stage='relegation_playoff'
            store.add(sid,dict(source_key=f'espn:{event["id"]}',competition=event_code,season=season_for(event_code,date,declared),
                              date=date,kickoff_utc=kickoff,home_id=ids[0],away_id=ids[1],orientation='home_away',
                              status='completed' if completed else 'live' if typ.get('state')=='in' else 'scheduled',
                              h90=hs,a90=ass,quality='single_source',score_scope=basis,provider='espn',observed_at=observed,
                              stage=stage,raw={'event_id':event['id'],'season':event.get('season'),'competition':c}))

def football_data(store, content, sid, code, start, observed):
    rows=list(csv.DictReader(io.StringIO(content.decode('utf-8-sig'))));completed=0;seen=set()
    for row in rows:
        if not row.get('HomeTeam') or not row.get('AwayTeam'):continue
        try:
            date=dt.datetime.strptime(row['Date'],'%d/%m/%Y' if len(row['Date'])==10 else '%d/%m/%y').date().isoformat()
            hs,ass=int(row['FTHG']),int(row['FTAG'])
            if date>dt.datetime.now(dt.timezone.utc).date().isoformat():continue
        except (ValueError,KeyError,TypeError):continue
        scope=LEAGUES[code][1];h=store.team(scope,row['HomeTeam']);a=store.team(scope,row['AwayTeam'])
        key=(date,h,a)
        if key in seen:store.issue('DUPLICATE_PROVIDER_FIXTURE',sid,key,row);continue
        seen.add(key);completed+=1
        store.add(sid,dict(source_key='fd:'+digest([code,start,key]),competition=code,season=f'{start}/{str(start+1)[-2:]}',
                           date=date,home_id=h,away_id=a,orientation='home_away',status='completed',h90=hs,a90=ass,
                           quality='single_source',score_scope='regulation_90',provider='football-data',stage='regular',observed_at=observed,raw=row))
    expected=expected_fd(code,start)
    end=dt.date(start+1,7,1);historical=dt.date.today()>=end
    state='CARDINALITY_COMPLETE' if completed==expected else 'IN_PROGRESS' if not historical else 'GAP'
    store.db.execute('INSERT OR REPLACE INTO coverage VALUES(?,?,?,?,?,?,?)',
                     (code,f'{start}/{str(start+1)[-2:]}','football-data',completed,expected,state,'Distinct completed fixtures; cardinality check, not independent full schedule certification'))

def kleague(store,data,sid,observed):
    value=data.get('data',{});rows=value.get('scheduleList',[]) or value.get('all',[])
    for row in rows:
        league=int(row.get('leagueId',0))
        if league not in (1,2):continue
        code=f'kor.{league}';date=row['gameDate'].replace('.','-');year=int(row.get('year') or date[:4])
        ids=[]
        for side in ['home','away']:
            pid='kleague:'+row[side+'Team'];name=row.get(side+'TeamName') or row[side+'Team']
            store.db.execute('INSERT OR IGNORE INTO teams VALUES(?,?,?)',(pid,'KOR',name))
            generic=store.team('KOR',name,aliases=[row[side+'Team']],evidence=f'K League official {pid}')
            if generic!=pid and not generic.startswith('espn:'):store.merge(generic,pid,'official K League team code')
            ids.append(pid)
        kickoff=utc(date+'T'+row.get('gameTime','00:00')+':00+09:00') if row.get('gameTime') else None
        complete=row.get('endYn')=='Y' and row.get('gameStatus')=='FE'
        phase={'Play Off':'promotion_playoff','파이널A':'split_A','파이널B':'split_B'}.get(row.get('codeName'),'regular')
        store.add(sid,dict(source_key=f'kleague:{year}:{league}:{row["gameId"]}',competition=code,season=str(year),date=date,
                           kickoff_utc=kickoff,home_id=ids[0],away_id=ids[1],orientation='home_away',status='completed' if complete else 'scheduled',
                           h90=int(row['homeGoal']) if complete else None,a90=int(row['awayGoal']) if complete else None,
                           quality='official',score_scope='regulation_90' if complete else 'unknown',stage=phase,provider='kleague',observed_at=observed,raw=row))

def canonical(store,path):
    c=sqlite3.connect(Path(path).as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON')
    sid=store.source(path,'canonical')
    teammap={}
    for row in c.execute('SELECT * FROM teams'):
        code=competition(row['competition_scope'])
        if not code:continue
        aliases=[a[0] for a in c.execute('SELECT alias FROM team_aliases WHERE team_id=?',(row['team_id'],))]
        teammap[row['team_id']]=store.team(LEAGUES[code][1],row['canonical_name'],aliases=aliases,evidence='Canonical stored alias mapping')
    for row in c.execute('SELECT * FROM historical_matches_v1'):
        r=dict(row);code=competition(r['competition'])
        if not code:store.issue('UNKNOWN_COMPETITION',sid,r['history_id'],r['competition']);continue
        if not r['source_date']:store.issue('MISSING_DATE',sid,r['history_id'],r);continue
        scope=LEAGUES[code][1]
        if r['quality'] in ('date_requires_verification','administrative_result','regulation_unresolved'):
            store.issue('LEGACY_QUALITY',sid,r['history_id'],{'quality':r['quality'],'url':r['source_url']})
        store.add(sid,dict(source_key='canonical:history:'+r['history_id'],competition=code,season=season_for(code,r['source_date'],r['season']),
                           date=r['source_date'],home_id=store.team(scope,r['home']),away_id=store.team(scope,r['away']),orientation='home_away',
                           status='completed',h90=r['regulation_home'],a90=r['regulation_away'],quality=r['quality'],score_scope='regulation_90' if r['regulation_home'] is not None and r['regulation_away'] is not None else 'unknown',
                           provider='canonical',observed_at=r['queried_at'],raw=r))
    for row in c.execute('SELECT * FROM matches'):
        r=dict(row);code=competition(r['competition'])
        if not code or r['home_id'] not in teammap or r['away_id'] not in teammap or not r['source_date']:
            store.issue('LEGACY_IDENTITY_PENDING',sid,r['match_id'],r);continue
        results=list(c.execute('SELECT * FROM results WHERE match_id=? ORDER BY revision',(r['match_id'],)))
        if not results:results=[None]
        for result in results:
            res=dict(result) if result else {};basis='regulation_90' if res.get('regulation_home') is not None and res.get('regulation_away') is not None else 'unknown'
            store.add(sid,dict(source_key='canonical:match:'+r['match_id'],competition=code,season=season_for(code,r['source_date'],r['season']),date=r['source_date'],kickoff_utc=r['kickoff_utc'],
                               home_id=teammap[r['home_id']],away_id=teammap[r['away_id']],orientation='home_away',status='completed' if result else 'scheduled',
                               h90=res.get('regulation_home'),a90=res.get('regulation_away'),extra_h=res.get('extra_home'),extra_a=res.get('extra_away'),pen_h=res.get('pen_home'),pen_a=res.get('pen_away'),
                               quality='single_source',score_scope=basis,provider='canonical',observed_at=res.get('known_at'),raw={'match':r,'result':res}))
    c.close()

def emperor(store,path):
    c=sqlite3.connect(Path(path).as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON')
    sid=store.source(path,'jfa')
    for r in map(dict,c.execute('SELECT * FROM training_90')):
        kickoff=utc(r['kickoff_at']);date=kickoff[:10]
        store.add(sid,dict(source_key='jfa:'+r['match_id'],competition='jpn.emperor',season=str(r['edition']),date=date,kickoff_utc=kickoff,
                           home_id=store.team('JPN',r['first_team']),away_id=store.team('JPN',r['second_team']),orientation='listed_first_second',status='completed',
                           h90=r['regulation_first'],a90=r['regulation_second'],extra_h=r['extra_first'],extra_a=r['extra_second'],pen_h=r['penalty_first'],pen_a=r['penalty_second'],
                           quality='official',score_scope='regulation_90',provider='jfa',observed_at=r['observed_at'],raw=r))
    c.close()

def local_files(store,root):
    """Import recognized snapshots/datasets only; never parse predictions as results."""
    root=Path(root)
    logs={}
    for path in (root/'reports').rglob('*log.json'):
        try:
            d=json.loads(path.read_text())
            if isinstance(d,list):
                for r in d:
                    if isinstance(r,dict) and r.get('sha256'):logs[r['sha256']]=r
        except (ValueError,OSError):pass
    for base in [root/'data/v2',root/'reports']:
        for p in sorted(base.rglob('*')):
            if not p.is_file() or p.suffix not in ('.json','.raw') or not any(x in p.name for x in ['history','training','summary','-result.raw','recent.json','full-','kor-','jpn-']):continue
            if any(x in p.name for x in ['artifact','prediction','parsed','audit','log']):continue
            try:d=json.loads(p.read_text())
            except (ValueError,UnicodeError,OSError):continue
            log=logs.get(__import__('hashlib').sha256(p.read_bytes()).hexdigest(),{})
            observed=log.get('observed_at') or log.get('query_time');url=log.get('url')
            if isinstance(d,dict) and ('events' in d or 'header' in d):
                league=(d.get('leagues') or [{}])[0].get('slug') or d.get('header',{}).get('league',{}).get('slug')
                if not league and url:
                    m=re.search('/soccer/([^/]+)/',url);league=m.group(1) if m else None
                code=competition(league)
                if code:espn(store,d,store.source(p,'espn',url,observed),code,observed)
            elif isinstance(d,dict) and isinstance(d.get('data'),dict) and ('scheduleList' in d['data'] or 'all' in d['data']):
                kleague(store,d,store.source(p,'kleague',url,observed),observed)
            elif isinstance(d,list) and d and isinstance(d[0],dict) and 'home_goals' in d[0] and 'cohort' in d[0]:
                sid=store.source(p,'normalized')
                for r in d:
                    code=competition(r.get('cohort'));date=r.get('date')
                    if not code or not date:continue
                    scope=LEAGUES[code][1]
                    store.add(sid,dict(source_key=r.get('match_id'),competition=code,season=season_for(code,date),date=date,kickoff_utc=utc(r.get('kickoff_at')),
                                       home_id=store.team(scope,r['home']),away_id=store.team(scope,r['away']),orientation='home_away',status='completed',
                                       h90=r['home_goals'],a90=r['away_goals'],quality='single_source',score_scope=r.get('score_basis','unknown'),provider='normalized',observed_at=r.get('observed_at'),raw=r))
    # The six EPL CSVs and their JSON are alternate representations of one source, not separate proof.
    audit=root/'data/v2/collection-audit.json'
    if audit.exists():
        for log in json.loads(audit.read_text()):
            if log['name'].startswith('epl-'):
                p=root/'data/v2'/f'{log["name"]}.csv'
                if p.exists():
                    start=2000+int(log['name'].split('-')[1][:2]);sid=store.source(p,'football-data',log['url'],log['observed_at'])
                    football_data(store,p.read_bytes(),sid,'eng.1',start,log['observed_at'])

def download(job,raw_dir):
    url,provider,code,year,body=job
    observed=now();request_id=digest([url,body,observed]);log=dict(request_id=request_id,url=url,body=body,provider=provider,code=code,year=year,observed_at=observed)
    try:
        headers={'User-Agent':'Mozilla/5.0','Content-Type':'application/json','Referer':'https://www.kleague.com/schedule.do'}
        with urllib.request.urlopen(urllib.request.Request(url,data=json.dumps(body).encode() if body else None,headers=headers),timeout=30) as resp:
            content=resp.read();log['http_status']=resp.status
        if provider=='football-data':
            if b'HomeTeam' not in content[:1000]:raise ValueError('not a football-data CSV')
        elif provider!='betexplorer':
            data=json.loads(content)
            if provider=='kleague' and (data.get('resultCode')!='200' or 'scheduleList' not in data.get('data',{})):raise ValueError('K League response failed contract')
            if provider=='espn' and not data.get('leagues'):raise ValueError('ESPN league identity missing')
        sha=__import__('hashlib').sha256(content).hexdigest()
        path=Path(raw_dir)/(sha+('.csv' if provider=='football-data' else '.json'));path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(content)
        log.update(path=str(path),bytes=len(content),observed_at=now())
    except Exception as e:log['error']=type(e).__name__+': '+str(e)
    return log

def betexplorer(store,content,sid,code,year,observed,url):
    text=content.decode('utf-8',errors='replace')
    clean=lambda s:html.unescape(re.sub('<[^>]+>','',s)).strip()
    count=0;stage=None
    for tr in re.findall(r'<tr\b[^>]*>(.*?)</tr>',text,re.S):
        if '<th' in tr:
            header=clean(tr)
            if code=='jpn.2':stage='promotion_playoff' if any(x in header.lower() for x in ['final','play','semi']) else 'regular'
            else:stage='cup'
        cells=re.findall(r'<td\b[^>]*>(.*?)</td>',tr,re.S)
        if len(cells)<3 or 'in-match' not in cells[0]:continue
        names=clean(cells[0]).split(' - ')
        if len(names)!=2:continue
        score=clean(cells[1]);dates=clean(cells[-1]);m=re.fullmatch(r'(\d+):(\d+)(.*)',score)
        try:date=dt.datetime.strptime(dates,'%d.%m.%Y').date().isoformat()
        except ValueError:continue
        if not m or date>dt.date.today().isoformat():continue
        href=re.search(r'href="([^"]+)"',cells[0]);link='https://www.betexplorer.com'+href.group(1) if href else url
        extra=bool(m.group(3).strip());quality='regulation_unresolved' if extra else 'single_source'
        h=store.team(LEAGUES[code][1],names[0]);a=store.team(LEAGUES[code][1],names[1])
        if extra:store.issue('REGULATION_UNRESOLVED',sid,link,{'display':score,'url':link})
        store.add(sid,dict(source_key='betexplorer:'+link,competition=code,season=str(year),date=date,home_id=h,away_id=a,
                           orientation='home_away',status='completed',h90=None if extra else int(m.group(1)),a90=None if extra else int(m.group(2)),
                           quality=quality,score_scope='unknown' if extra else 'regulation_90',stage=stage,provider='betexplorer',observed_at=observed,raw={'url':link,'display_score':score,'date':dates,'home':names[0],'away':names[1]}));count+=1
    store.db.execute('INSERT OR REPLACE INTO coverage VALUES(?,?,?,?,?,?,?)',(code,str(year),'betexplorer',count,None,'SOURCE_LIST_IMPORTED' if count else 'GAP','Source list count; AET/PEN isolated and completeness not independently certified'))

def supplemental(store,raw_dir,years,workers=4):
    targets={'jpn.2':('japan','j2-league'),'jpn.league_cup':('japan','ybc-levain-cup'),'kor.cup':('south-korea','korean-cup')}
    jobs=[(f'https://www.betexplorer.com/football/{country}/{name}-{year}/results/','betexplorer',code,year,None) for code,(country,name) in targets.items() for year in years]
    logs=[]
    for depth in range(2):
      extra=[]
      with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for log in pool.map(lambda j:download(j,raw_dir),jobs):
            sid=None
            if 'error' not in log:
                sid=store.source(log['path'],'betexplorer',log['url'],log['observed_at'])
                betexplorer(store,Path(log['path']).read_bytes(),sid,log['code'],log['year'],log['observed_at'],log['url'])
                if depth==0:
                    text=Path(log['path']).read_text()
                    stages=set(re.findall(r'href="\?stage=([A-Za-z0-9]+)"',text))
                    extra.extend((log['url']+'?stage='+stage,'betexplorer',log['code'],log['year'],None) for stage in stages)
            store.db.execute('INSERT OR IGNORE INTO fetch_log VALUES(?,?,?,?,?,?,?)',(log['request_id'],log['url'],'null',log.get('http_status'),log['observed_at'],sid,log.get('error')))
            store.db.commit();logs.append(log)
      jobs=extra
    for code in targets:
        for year in years:
            count=store.db.execute("SELECT count(DISTINCT source_key) FROM observations WHERE provider='betexplorer' AND competition=? AND season=?",(code,str(year))).fetchone()[0]
            store.db.execute("UPDATE coverage SET completed=?,status=?,detail=? WHERE code=? AND season=? AND provider='betexplorer'",(count,'SOURCE_LIST_IMPORTED' if count else 'GAP','Distinct source fixtures across all discovered stage tabs; AET/PEN isolated, completeness not independently certified',code,str(year)))
    store.db.commit()
    return logs

def repair_cached(store):
    rows=list(store.db.execute("SELECT * FROM sources WHERE path LIKE '%/unified/raw/%'"))
    for r in rows:
        m=re.search('/soccer/([^/]+)/',r['url'] or '')
        if m and Path(r['path']).exists():espn(store,json.loads(Path(r['path']).read_text()),r['source_id'],m.group(1),r['observed_at'])
        elif r['provider']=='football-data':
            match=re.search(r'/mmz4281/(\d{4})/([^/]+).csv',r['url'])
            code=next((c for c,info in LEAGUES.items() if info[3]==match.group(2)),None) if match else None
            if code:football_data(store,Path(r['path']).read_bytes(),r['source_id'],code,2000+int(match.group(1)[:2]),r['observed_at'])
        elif r['provider']=='kleague':kleague(store,json.loads(Path(r['path']).read_text()),r['source_id'],r['observed_at'])
        elif r['provider']=='betexplorer' and '/results/' in r['url']:
            m=re.search(r'/(j2-league|ybc-levain-cup|korean-cup)-(\d{4})/',r['url'])
            code={'j2-league':'jpn.2','ybc-levain-cup':'jpn.league_cup','korean-cup':'kor.cup'}.get(m.group(1)) if m else None
            if code:betexplorer(store,Path(r['path']).read_bytes(),r['source_id'],code,int(m.group(2)),r['observed_at'],r['url'])

def period_score(text,url,second_leg=False):
    """Betexplorer period increments, independently of its synthetic penalty display score."""
    canonical=re.search(r'<link[^>]*rel="canonical"[^>]*href="([^"]+)"',text)
    if not canonical or canonical.group(1).rstrip('/')!=url.rstrip('/'):raise ValueError('canonical fixture identity mismatch')
    finished=re.search(r'<input[^>]*id="isFinished"[^>]*>',text)
    if not finished or 'value="1"' not in finished.group(0):raise ValueError('finished mark missing')
    field=lambda name:html.unescape(re.sub('<[^>]+>','',re.search(r'id="'+name+r'"[^>]*>(.*?)</',text,re.S).group(1))).strip()
    phase=field('js-eventstage');partial=field('js-partial');display=field('js-score')
    if not re.fullmatch(r'\([0-9:,\s]+\)',partial):raise ValueError('unsupported period notation')
    periods=[tuple(map(int,x)) for x in re.findall(r'(\d+):(\d+)',partial)]
    if phase=='After Extra Time' and len(periods) in (3,4):
        final=tuple(sum(x[i] for x in periods) for i in (0,1));shown=tuple(map(int,display.split(':')))
        if final!=shown:raise ValueError('periods disagree with AET total')
        extra=periods[2:];pen=(None,None)
    elif phase=='After Penalties' and len(periods) in (3,4,5):
        extra=periods[2:-1];pen=periods[-1]
        tied=tuple(sum(x[i] for x in periods[:-1]) for i in (0,1));shown=tuple(map(int,display.split(':')))
        if (tied[0]!=tied[1] and not second_leg) or pen[0]==pen[1] or (shown[0]>shown[1])!=(pen[0]>pen[1]):raise ValueError('shootout consistency failed')
    else:raise ValueError('unsupported match phase')
    return dict(h90=periods[0][0]+periods[1][0],a90=periods[0][1]+periods[1][1],
                extra_h=sum(x[0] for x in extra),extra_a=sum(x[1] for x in extra),pen_h=pen[0],pen_a=pen[1],periods=periods,phase=phase)

def resolve_cup_periods(store,raw_dir,workers=4):
    candidates={}
    for r in store.db.execute("SELECT * FROM observations WHERE provider='betexplorer' AND score_scope='unknown'"):
        url=json.loads(r['raw_json']).get('url')
        if url:candidates[url]=dict(r)
    needed={}
    for url,r in candidates.items():
        if not store.db.execute("SELECT 1 FROM observations WHERE provider='betexplorer' AND source_key=? AND score_scope='regulation_90'",(r['source_key'],)).fetchone():needed[url]=r
    # A missing period record is a source blocker; do not repeatedly retry it unchanged.
    from store import timestamp
    for url in list(needed):
        failed=store.db.execute("SELECT observed_at FROM fetch_log WHERE url=? AND error LIKE 'PERIOD_PARSE%' ORDER BY observed_at DESC LIMIT 1",(url,)).fetchone()
        if failed and dt.datetime.now(dt.timezone.utc).timestamp()-timestamp(failed[0])<86400:
            needed.pop(url)
    jobs=[(u,'betexplorer',r['competition'],int(r['season'][:4]),None) for u,r in needed.items()]
    logs=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for log in pool.map(lambda j:download(j,raw_dir),jobs):
            sid=None
            if 'error' not in log:
                sid=store.source(log['path'],'betexplorer',log['url'],log['observed_at'])
                try:
                    r=needed[log['url']]
                    source=store.db.execute('SELECT path FROM sources WHERE source_id=?',(r['source_id'],)).fetchone()
                    listing=Path(source['path']).read_text() if source and Path(source['path']).exists() else ''
                    i=listing.find(log['url'].rstrip('/').split('/')[-1]);heads=re.findall(r'<tr><th.*?</tr>',listing[:i],re.S) if i>=0 else []
                    second_leg=bool(heads and 'Second leg' in heads[-1])
                    parsed=period_score(Path(log['path']).read_text(),log['url'],second_leg=second_leg)
                    r.update({k:v for k,v in parsed.items() if k not in ('phase','periods')});r.update(score_scope='regulation_90',quality='period_verified',observed_at=log['observed_at'],raw=parsed)
                    store.add(sid,r)
                    store.db.execute("UPDATE quarantine SET resolved=1 WHERE kind='PERIOD_PARSE_FAILURE' AND row_key=?",(log['url'],))
                except Exception as e:
                    log['error']='PERIOD_PARSE '+type(e).__name__+': '+str(e);store.issue('PERIOD_PARSE_FAILURE',sid,log['url'],log['error'])
            store.db.execute('INSERT OR IGNORE INTO fetch_log VALUES(?,?,?,?,?,?,?)',(log['request_id'],log['url'],'null',log.get('http_status'),log['observed_at'],sid,log.get('error')))
            store.db.commit();logs.append(log)
    return logs

def fetch(store,raw_dir,years,backfill=True,workers=6):
    jobs=[]
    for code,(_,_,_,fd) in LEAGUES.items():
        if fd:
            starts=range(2020,dt.datetime.now().year+1) if backfill else [dt.datetime.now().year-1,dt.datetime.now().year]
            for start in starts:
                key=f'{str(start)[-2:]}{str(start+1)[-2:]}'
                jobs.append((f'https://www.football-data.co.uk/mmz4281/{key}/{fd}.csv','football-data',code,start,None))
        if code not in ESPN_UNAVAILABLE:
            for year in years:
                jobs.append((f'https://site.api.espn.com/apis/site/v2/sports/soccer/{code}/scoreboard?dates={year}&limit=1000','espn',code,year,None))
    # Official Korean API supports monthly schedule queries, not ESPN's unavailable leagues.
    for year in years:
        for league in (1,2):
            for month in range(1,13):
                if year==dt.datetime.now().year and month>dt.datetime.now().month+1:continue
                jobs.append(('https://www.kleague.com/getScheduleList.do','kleague',f'kor.{league}',year,
                             {'year':str(year),'leagueId':str(league),'meetSeq':str(league),'month':f'{month:02d}','teamId':''}))
    logs=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for log in pool.map(lambda j:download(j,raw_dir),jobs):
            logs.append(log);sid=None
            if 'error' not in log:
                path=Path(log['path']);sid=store.source(path,log['provider'],log['url'],log['observed_at'])
                try:
                    if log['provider']=='football-data':football_data(store,path.read_bytes(),sid,log['code'],log['year'],log['observed_at'])
                    elif log['provider']=='espn':
                        d=json.loads(path.read_text());espn(store,d,sid,log['code'],log['observed_at'])
                        if len(d.get('events',[]))>=1000:store.issue('POTENTIAL_PROVIDER_TRUNCATION',sid,log['code'],{'year':log['year'],'count':len(d['events'])})
                    else:kleague(store,json.loads(path.read_text()),sid,log['observed_at'])
                except Exception as e:log['error']='IMPORT '+type(e).__name__+': '+str(e);store.issue('IMPORT_FAILURE',sid,log['code'],log['error'])
            store.db.execute('INSERT OR IGNORE INTO fetch_log VALUES(?,?,?,?,?,?,?)',
                             (log['request_id'],log['url'],json.dumps(log['body']),log.get('http_status'),log['observed_at'],sid,log.get('error')))
            store.db.commit()
            if len(logs)%20==0:print(f'Fetched {len(logs)}/{len(jobs)}, failures={sum("error" in x for x in logs)}',flush=True)
    return logs
