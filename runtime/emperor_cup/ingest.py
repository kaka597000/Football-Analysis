"""JFA Emperor's Cup extension database. Canonical database is never opened for write."""
import argparse
import concurrent.futures
import hashlib
import json
import re
import sqlite3
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

ROOT=Path(__file__).resolve().parents[2] / 'data' / 'emperor-cup'
REPORT=Path(__file__).resolve().parents[2] / 'reports' / 'emperor-cup-database'

def normalize(value):
    return re.sub(r'\s+',' ',unicodedata.normalize('NFKC',value)).strip()

def sha(value):return hashlib.sha256(value).hexdigest()

class Node:
    def __init__(self,tag='',attrs=()):self.tag=tag;self.attrs=dict(attrs);self.children=[]
    def text(self):return normalize(' '.join(c.text() if isinstance(c,Node) else c for c in self.children))
    def nodes(self,tag=None,cls=None,id=None):
        found=[]
        for c in self.children:
            if not isinstance(c,Node):continue
            if (tag is None or c.tag==tag) and (cls is None or cls in c.attrs.get('class','').split()) and (id is None or c.attrs.get('id')==id):found.append(c)
            found.extend(c.nodes(tag,cls,id))
        return found

class Tree(HTMLParser):
    VOID={'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}
    def __init__(self,source):
        super().__init__(convert_charrefs=True);self.root=Node();self.stack=[self.root];self.feed(source)
    def handle_starttag(self,tag,attrs):
        n=Node(tag,attrs);self.stack[-1].children.append(n)
        if tag not in self.VOID:self.stack.append(n)
    def handle_startendtag(self,tag,attrs):
        self.handle_starttag(tag,attrs)
        if tag not in self.VOID:self.handle_endtag(tag)
    def handle_endtag(self,tag):
        for i in range(len(self.stack)-1,0,-1):
            if self.stack[i].tag==tag:self.stack=self.stack[:i];break
    def handle_data(self,data):self.stack[-1].children.append(data)

def single(nodes,label):
    if len(nodes)!=1:raise ValueError(f'{label}: expected one element, got {len(nodes)}')
    return nodes[0]

def parse(raw,year,number,url,observed):
    root=Tree(raw.decode('utf-8',errors='strict')).root
    schedule=single(root.nodes(cls='text-schedule'),'schedule').text()
    pattern=r'\[(\d+)\]\s*(.*?)\s*(\d{4})年(\d{2})月(\d{2})日\s*(\d{2}):(\d{2})\s*KickOff\s*(.*)'
    match=re.fullmatch(pattern,schedule)
    if not match:
        pending=re.fullmatch(r'\[(\d+)\]\s*(.*?)\s*KickOff',schedule)
        if pending and int(pending[1])==number:
            return {'slot_only':True,'year':year,'number':number,'stage':pending[2],
                'known_teams':[n.text() for n in root.nodes(cls='flag') if n.text()],
                'status':'FUTURE_SLOT_DATE_OR_OPPONENT_UNANNOUNCED','source_url':url,'observed_at':observed}
        raise ValueError('match identity/schedule mismatch')
    if int(match[1])!=number:raise ValueError('match identity/schedule mismatch')
    if int(match[3]) not in (year,year+1):raise ValueError('edition/date mismatch')
    kickoff=datetime(*map(int,[match[3],match[4],match[5],match[6],match[7]]),tzinfo=timezone(timedelta(hours=9)))
    board=single(root.nodes(id='score-board-header'),'scoreboard')
    flags=board.nodes(cls='flag')
    if len(flags)!=2:raise ValueError('two listed teams required')
    teams=[n.text() for n in flags]
    if not all(teams) or teams[0]==teams[1]:raise ValueError('invalid teams')
    totals=[n.text() for n in board.nodes(cls='total-score')]
    detail=single(board.nodes(cls='score-detail'),'score detail')
    periods={}
    for li in detail.nodes(tag='li'):
        spans=[s.text() for s in li.nodes(tag='span')]
        if len(spans)!=3:raise ValueError('invalid period score')
        if spans[1] in periods:raise ValueError('duplicate period')
        if spans[1] not in ['前半','後半','延前','延後','PK']:raise ValueError('unknown period label')
        if not spans[0] and not spans[2]:
            periods[spans[1]]=None
            continue
        if not spans[0].isdigit() or not spans[2].isdigit():raise ValueError('invalid period score')
        periods[spans[1]]=[int(spans[0]),int(spans[2])]
    status=' '.join(n.text() for n in root.nodes(cls='full-time'))
    official_links=root.nodes(cls='official-record')
    pdf=next((urljoin(url,a.attrs['href']) for n in official_links for a in n.nodes(tag='a') if '.pdf' in a.attrs.get('href','')),None)
    current=datetime.fromisoformat(observed)
    status_kind='SCHEDULED' if current<kickoff else 'LIVE_OR_AWAITING_RESULT'
    reg=None;display=None;extra=None;penalty=periods.get('PK')
    if len(totals)==2 and all(t.isdigit() for t in totals):display=list(map(int,totals))
    # Completed authoritative record + both halves + clock sanity. Never infer
    # regulation goals by subtracting penalties from a displayed final score.
    if pdf and all(periods.get(p) is not None for p in ('前半','後半')) and current>=kickoff+timedelta(hours=2):
        if '試合前' in status or '前半終了' in status:raise ValueError('final record contradicts live status')
        reg=[periods['前半'][i]+periods['後半'][i] for i in (0,1)]
        extra=[sum((periods.get(p) or [0,0])[i] for p in ('延前','延後')) for i in (0,1)]
        if ('延前' in periods)!=('延後' in periods):raise ValueError('incomplete extra time periods')
        if display!=[reg[i]+extra[i] for i in (0,1)]:raise ValueError('period/total score contradiction')
        if penalty and (reg[0]+extra[0]!=reg[1]+extra[1]):raise ValueError('penalties with non-drawn played score')
        status_kind='FINAL_VERIFIED'
    tiers=[n.text() for n in root.nodes(cls='team-name')[:2]]
    roster=[];tables=root.nodes(tag='table',cls='match-result')
    if tables:
        role='listed_players';before=[];after=[]
        for tr in tables[0].nodes(tag='tr'):
            if '控え選手' in tr.text():role='bench';continue
            cells=[n.text() for n in tr.nodes(tag='td')]
            if len(cells)==6 and cells[0] in ('GK','DF','MF','FW') and cells[3] in ('GK','DF','MF','FW'):
                (before if role=='listed_players' else after).append(cells)
        starters=len(before)==11 and bool(after)
        for phase,rows in [('starter' if starters else 'registered',before),('bench',after)]:
            for cells in rows:
                for side,off in [('first',0),('second',3)]:
                    roster.append(dict(side=side,role=phase,position=cells[off],shirt=cells[off+1],name=cells[off+2]))
    return {'year':year,'number':number,'match_id':f'JFA-EC-{year}-{number:02d}',
        'stage':match[2],'kickoff_at':kickoff.isoformat(),'kickoff_utc8':kickoff.astimezone(timezone(timedelta(hours=8))).isoformat(),
        'venue':match[8],'first':teams[0],'second':teams[1],'tiers':tiers,'status':status_kind,
        'source_status':status,'periods':periods,'regulation':reg,'extra':extra,'penalty':penalty,
        'display_final':display,'official_record_url':pdf,'source_url':url,'observed_at':observed,
        'first_known_at':None,'roster':roster,'physical_home_verified':False,
        'orientation':'JFA_LISTED_ORDER_NOT_AUTOMATIC_HOME_ADVANTAGE'}

SCHEMA='''
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS editions(year INTEGER PRIMARY KEY,source_url TEXT NOT NULL,expected_slots INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS teams(team_id TEXT PRIMARY KEY,official_name TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS snapshots(snapshot_id TEXT PRIMARY KEY,source_url TEXT NOT NULL,observed_at TEXT NOT NULL,sha256 TEXT NOT NULL,raw_path TEXT NOT NULL,source_tier TEXT NOT NULL CHECK(source_tier='L1'));
CREATE TABLE IF NOT EXISTS scheduled_slots(edition INTEGER NOT NULL REFERENCES editions(year),match_number INTEGER NOT NULL,stage TEXT NOT NULL,status TEXT NOT NULL,known_teams_json TEXT NOT NULL,latest_snapshot TEXT NOT NULL REFERENCES snapshots(snapshot_id),PRIMARY KEY(edition,match_number));
CREATE TABLE IF NOT EXISTS matches(match_id TEXT PRIMARY KEY,edition INTEGER NOT NULL REFERENCES editions(year),match_number INTEGER NOT NULL,stage TEXT NOT NULL,kickoff_at TEXT NOT NULL,venue TEXT NOT NULL,first_team TEXT NOT NULL REFERENCES teams(team_id),second_team TEXT NOT NULL REFERENCES teams(team_id),orientation TEXT NOT NULL,physical_home_verified INTEGER NOT NULL DEFAULT 0,status TEXT NOT NULL,latest_snapshot TEXT NOT NULL REFERENCES snapshots(snapshot_id),UNIQUE(edition,match_number));
CREATE TABLE IF NOT EXISTS observations(snapshot_id TEXT PRIMARY KEY REFERENCES snapshots(snapshot_id),match_id TEXT NOT NULL REFERENCES matches(match_id),parsed_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS result_observations(snapshot_id TEXT PRIMARY KEY REFERENCES snapshots(snapshot_id),match_id TEXT NOT NULL REFERENCES matches(match_id),regulation_first INTEGER NOT NULL,regulation_second INTEGER NOT NULL,extra_first INTEGER NOT NULL,extra_second INTEGER NOT NULL,penalty_first INTEGER,penalty_second INTEGER,display_first INTEGER NOT NULL,display_second INTEGER NOT NULL,official_record_url TEXT NOT NULL,score_basis TEXT NOT NULL CHECK(score_basis='regulation_90'));
CREATE TABLE IF NOT EXISTS roster_observations(snapshot_id TEXT NOT NULL REFERENCES snapshots(snapshot_id),side TEXT NOT NULL,role TEXT NOT NULL,position TEXT NOT NULL,shirt TEXT NOT NULL,name TEXT NOT NULL,PRIMARY KEY(snapshot_id,side,role,shirt,name));
CREATE TABLE IF NOT EXISTS fetch_log(run_id TEXT NOT NULL,edition INTEGER NOT NULL,match_number INTEGER NOT NULL,source_url TEXT NOT NULL,observed_at TEXT NOT NULL,http_status INTEGER,error TEXT,PRIMARY KEY(run_id,edition,match_number));
CREATE TABLE IF NOT EXISTS quarantine(run_id TEXT NOT NULL,edition INTEGER NOT NULL,match_number INTEGER NOT NULL,reason TEXT NOT NULL,raw_path TEXT,PRIMARY KEY(run_id,edition,match_number));
CREATE TABLE IF NOT EXISTS quarantine_resolutions(run_id TEXT NOT NULL,edition INTEGER NOT NULL,match_number INTEGER NOT NULL,resolved_at TEXT NOT NULL,resolution TEXT NOT NULL,PRIMARY KEY(run_id,edition,match_number));
CREATE VIEW IF NOT EXISTS training_90 AS SELECT m.match_id,m.edition,m.stage,m.kickoff_at,m.venue,t1.official_name AS first_team,t2.official_name AS second_team,r.regulation_first,r.regulation_second,r.extra_first,r.extra_second,r.penalty_first,r.penalty_second,s.source_url,s.observed_at,s.sha256,m.orientation,m.physical_home_verified FROM matches m JOIN result_observations r ON r.snapshot_id=m.latest_snapshot JOIN snapshots s ON s.snapshot_id=r.snapshot_id JOIN teams t1 ON t1.team_id=m.first_team JOIN teams t2 ON t2.team_id=m.second_team WHERE m.status='FINAL_VERIFIED';
'''

def ingest(connection,parsed,raw_path,raw):
    p=parsed;fingerprint=sha(raw);sid=sha((p['source_url']+'|'+p['observed_at']+'|'+fingerprint).encode())
    connection.execute('INSERT OR IGNORE INTO editions VALUES(?,?,?)',(p['year'],f'https://www.jfa.jp/match/emperorscup_{p["year"]}/schedule_result/',87))
    connection.execute('INSERT OR IGNORE INTO snapshots VALUES(?,?,?,?,?,?)',(sid,p['source_url'],p['observed_at'],fingerprint,str(raw_path),'L1'))
    if p.get('slot_only'):
        connection.execute('INSERT INTO scheduled_slots VALUES(?,?,?,?,?,?) ON CONFLICT(edition,match_number) DO UPDATE SET stage=excluded.stage,status=excluded.status,known_teams_json=excluded.known_teams_json,latest_snapshot=excluded.latest_snapshot',
            (p['year'],p['number'],p['stage'],p['status'],json.dumps(p['known_teams'],ensure_ascii=False),sid))
        return
    connection.execute('DELETE FROM scheduled_slots WHERE edition=? AND match_number=?',(p['year'],p['number']))
    first=sha(p['first'].encode());second=sha(p['second'].encode())
    for identity,name in [(first,p['first']),(second,p['second'])]:connection.execute('INSERT OR IGNORE INTO teams VALUES(?,?)',(identity,name))
    values=(p['match_id'],p['year'],p['number'],p['stage'],p['kickoff_at'],p['venue'],first,second,p['orientation'],0,p['status'],sid)
    connection.execute('''INSERT INTO matches VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(match_id) DO UPDATE SET stage=excluded.stage,kickoff_at=excluded.kickoff_at,venue=excluded.venue,first_team=excluded.first_team,second_team=excluded.second_team,status=excluded.status,latest_snapshot=excluded.latest_snapshot''',values)
    connection.execute('INSERT OR IGNORE INTO observations VALUES(?,?,?)',(sid,p['match_id'],json.dumps(p,ensure_ascii=False)))
    if p['regulation'] is not None:
        penalties=p['penalty'] or [None,None]
        connection.execute('INSERT OR IGNORE INTO result_observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            (sid,p['match_id'],*p['regulation'],*p['extra'],*penalties,*p['display_final'],p['official_record_url'],'regulation_90'))
    for person in p['roster']:
        connection.execute('INSERT OR IGNORE INTO roster_observations VALUES(?,?,?,?,?,?)',(sid,person['side'],person['role'],person['position'],person['shirt'],person['name']))

def fetch(job):
    year,n=job;url=f'https://www.jfa.jp/match/emperorscup_{year}/match_page/m{n}.html'
    observed=datetime.now(timezone.utc).isoformat()
    try:
        with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'WCPP-research/0.2'}),timeout=25) as response:
            raw=response.read();actual=response.geturl();status=response.status
        observed=datetime.now(timezone.utc).isoformat()
        if actual.split('#')[0]!=url:raise ValueError('redirected to a different match URL')
        path=ROOT/'raw'/f'{year}-m{n}-{sha(raw)[:16]}.html';path.write_bytes(raw)
        try:parsed=parse(raw,year,n,url,observed)
        except Exception as exc:return dict(year=year,n=n,url=url,observed=observed,status=status,raw_path=str(path),quarantine=str(exc))
        return dict(year=year,n=n,url=url,observed=observed,status=status,path=path,raw=raw,parsed=parsed)
    except Exception as exc:
        return dict(year=year,n=n,url=url,observed=observed,status=getattr(exc,'code',None),error=str(exc))

def audit(connection,run_id):
    connection.row_factory=sqlite3.Row
    integrity=[r[0] for r in connection.execute('PRAGMA integrity_check')];foreign=[dict(r) for r in connection.execute('PRAGMA foreign_key_check')]
    stats=[dict(r) for r in connection.execute('SELECT edition,status,count(*) AS n FROM matches GROUP BY edition,status ORDER BY edition,status')]
    quarantine=[dict(r) for r in connection.execute('SELECT q.* FROM quarantine q LEFT JOIN quarantine_resolutions r USING(run_id,edition,match_number) WHERE r.run_id IS NULL')]
    failures=[dict(r) for r in connection.execute('SELECT edition,match_number,http_status,error FROM fetch_log WHERE run_id=? AND error IS NOT NULL',(run_id,))]
    conflicts=[dict(r) for r in connection.execute('SELECT match_id FROM result_observations GROUP BY match_id HAVING count(DISTINCT regulation_first||":"||regulation_second)>1')]
    coverage=[]
    for edition,expected in connection.execute('SELECT year,expected_slots FROM editions ORDER BY year'):
        imported={r[0] for r in connection.execute('SELECT match_number FROM matches WHERE edition=?',(edition,))}
        slots={r[0] for r in connection.execute('SELECT match_number FROM scheduled_slots WHERE edition=?',(edition,))}
        coverage.append({'edition':edition,'expected_numbered_slots':expected,'real_fixtures':len(imported),
            'future_tbd_slots':len(slots),'missing_numbered_slots':sorted(set(range(1,expected+1))-imported-slots)})
    paths=[dict(r) for r in connection.execute('SELECT sha256,raw_path FROM snapshots')]
    hash_errors=[p for p in paths if sha(Path(p['raw_path']).read_bytes())!=p['sha256']]
    if integrity!=['ok'] or foreign or hash_errors:raise ValueError('database/raw audit failed')
    return {'run_id':run_id,'integrity':integrity,'foreign_key_errors':foreign,'status_counts':stats,
        'quarantine':quarantine,'fetch_failures':failures,'result_conflicts':conflicts,'raw_hash_errors':hash_errors,
        'edition_coverage':coverage,
        'matches':connection.execute('SELECT count(*) FROM matches').fetchone()[0],
        'future_slots':connection.execute('SELECT count(*) FROM scheduled_slots').fetchone()[0],
        'verified_90':connection.execute('SELECT count(*) FROM training_90').fetchone()[0],
        'extra_time_matches':connection.execute('SELECT count(*) FROM training_90 WHERE extra_first+extra_second>0 OR penalty_first IS NOT NULL').fetchone()[0],
        'shootout_matches':connection.execute('SELECT count(*) FROM training_90 WHERE penalty_first IS NOT NULL').fetchone()[0],
        'roster_rows':connection.execute('SELECT count(*) FROM roster_observations').fetchone()[0],
        'source_tier':'L1_JFA_SINGLE_SOURCE','formal_kpi_added':0,
        'coverage_note':'87 official numbered slots per edition probed; missing future pages/fetch failures do not count as imported fixtures',
        'first_known_at_note':'Unknown historical first availability. observed_at is actual current fetch time.',
        'orientation_note':'First/second are JFA listed order, not verified physical home/away'}

def repair_quarantine(connection):
    rows=connection.execute('''SELECT q.run_id,q.edition,q.match_number,q.raw_path,f.source_url,f.observed_at FROM quarantine q JOIN fetch_log f USING(run_id,edition,match_number) LEFT JOIN quarantine_resolutions r USING(run_id,edition,match_number) WHERE r.run_id IS NULL''').fetchall()
    repaired=0
    for run,year,n,path,url,observed in rows:
        raw=Path(path).read_bytes()
        try:parsed=parse(raw,year,n,url,observed)
        except ValueError:continue
        with connection:
            ingest(connection,parsed,path,raw)
            connection.execute('INSERT INTO quarantine_resolutions VALUES(?,?,?,?,?)',(run,year,n,datetime.now(timezone.utc).isoformat(),'REPARSED_ORIGINAL_SNAPSHOT_WITH_CORRECT_LIVE_OR_FUTURE_SLOT_HANDLING'))
        repaired+=1
    return repaired

def export(connection):
    rows=[]
    for r in connection.execute('SELECT * FROM training_90 ORDER BY kickoff_at,match_id'):
        rows.append({'match_id':r['match_id'],'cohort':'JPN_EMPEROR_CUP','edition':r['edition'],'stage':r['stage'],
            'date':r['kickoff_at'][:10],'kickoff_at':r['kickoff_at'],'first_team':r['first_team'],'second_team':r['second_team'],
            'first_goals_90':r['regulation_first'],'second_goals_90':r['regulation_second'],'venue':r['venue'],
            'score_basis':'regulation_90','source_url':r['source_url'],'observed_at':r['observed_at'],'first_known_at':None,
            'raw_sha256':r['sha256'],'orientation':r['orientation'],'physical_home_verified':False,
            'training_status':'RAW_90_VERIFIED_REQUIRES_TEAM_ALIAS_AND_VENUE_AUDIT'})
    (ROOT/'verified-90-staging.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))

def main():
    p=argparse.ArgumentParser();p.add_argument('--years',nargs='+',type=int,default=[2024,2025,2026]);p.add_argument('--workers',type=int,default=4);p.add_argument('--matches',nargs='+',type=int);p.add_argument('--repair-only',action='store_true');a=p.parse_args()
    if any(y not in range(2000,2027) for y in a.years):raise ValueError('invalid edition')
    if a.matches and any(n not in range(1,88) for n in a.matches):raise ValueError('invalid match number')
    ROOT.mkdir(parents=True,exist_ok=True);(ROOT/'raw').mkdir(exist_ok=True);REPORT.mkdir(parents=True,exist_ok=True)
    db=ROOT/'WCPP_天皇杯扩展库.sqlite';connection=sqlite3.connect(db);connection.executescript(SCHEMA)
    repaired=repair_quarantine(connection)
    run_id=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');jobs=[] if a.repair_only else [(year,n) for year in a.years for n in (a.matches or range(1,88))]
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(a.workers,6)) as pool:
        for result in pool.map(fetch,jobs):
            row=result
            with connection:
                connection.execute('INSERT INTO fetch_log VALUES(?,?,?,?,?,?,?)',(run_id,row['year'],row['n'],row['url'],row['observed'],row['status'],row.get('error')))
                if row.get('quarantine'):
                    connection.execute('INSERT INTO quarantine VALUES(?,?,?,?,?)',(run_id,row['year'],row['n'],row['quarantine'],row['raw_path']))
                elif 'parsed' in row:ingest(connection,row['parsed'],row['path'],row['raw'])
            if row['n'] in (24,56,72,87):print(json.dumps({'edition':row['year'],'through_match':row['n'],'error':row.get('error'),'quarantine':row.get('quarantine')},ensure_ascii=False),flush=True)
    report=audit(connection,run_id);report['reparsed_quarantine_rows']=repaired;export(connection);connection.close()
    report.update({'database':str(db),'database_sha256':sha(db.read_bytes()),'database_bytes':db.stat().st_size})
    (REPORT/(run_id+'-audit.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2))
    (REPORT/'latest-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
