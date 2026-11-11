"""Build a separate auditable League Cup inventory; never modifies the unified DB."""
from datetime import datetime,timezone
import hashlib,json,sqlite3
from pathlib import Path
from urllib.parse import urlparse
from .evidence import CODE,source,parse_list,verify_row,phase,sha

def dump(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,allow_nan=False)
def key(x):return hashlib.sha256(dump(x).encode()).hexdigest()
def team(name):return 'betexplorer-name:'+key(name)[:20]

def build(manifest,out):
    out=Path(out);out.parent.mkdir(parents=True,exist_ok=True)
    with out.open('xb'):pass
    c=sqlite3.connect(out);c.row_factory=sqlite3.Row
    c.executescript('''
    PRAGMA foreign_keys=ON;
    CREATE TABLE sources(source_id TEXT PRIMARY KEY,raw_path TEXT NOT NULL,raw_sha256 TEXT NOT NULL,source_url TEXT NOT NULL,query_time TEXT,metadata_json TEXT NOT NULL);
    CREATE TABLE teams(team_id TEXT PRIMARY KEY,name TEXT NOT NULL);
    CREATE TABLE matches(match_id TEXT PRIMARY KEY,event_url TEXT UNIQUE,season TEXT NOT NULL,date TEXT NOT NULL,home_id TEXT NOT NULL REFERENCES teams,away_id TEXT NOT NULL REFERENCES teams,
      stage TEXT,era TEXT NOT NULL,leg INTEGER,tie_format TEXT,neutral INTEGER CHECK(neutral IN(0,1)),
      home_90 INTEGER CHECK(home_90 BETWEEN 0 AND 40),away_90 INTEGER CHECK(away_90 BETWEEN 0 AND 40),home_et INTEGER,away_et INTEGER,home_pen INTEGER,away_pen INTEGER,
      regulation_verified INTEGER NOT NULL,source_verified INTEGER NOT NULL,research_eligible INTEGER NOT NULL,reason TEXT NOT NULL,payload_json TEXT NOT NULL,
      CHECK(home_id<>away_id),CHECK(research_eligible=0 OR (regulation_verified=1 AND source_verified=1 AND stage IS NOT NULL AND home_90 IS NOT NULL AND away_90 IS NOT NULL)));
    CREATE TABLE observations(observation_id TEXT PRIMARY KEY,match_id TEXT NOT NULL REFERENCES matches,source_id TEXT NOT NULL REFERENCES sources,payload_json TEXT NOT NULL);
    CREATE TABLE quarantine(issue_id TEXT PRIMARY KEY,event_url TEXT,reason TEXT NOT NULL,input_json TEXT NOT NULL);
    CREATE TRIGGER source_no_update BEFORE UPDATE ON sources BEGIN SELECT RAISE(ABORT,'append-only sources');END;
    CREATE TRIGGER source_no_delete BEFORE DELETE ON sources BEGIN SELECT RAISE(ABORT,'append-only sources');END;
    CREATE TRIGGER obs_no_update BEFORE UPDATE ON observations BEGIN SELECT RAISE(ABORT,'append-only observations');END;
    CREATE TRIGGER obs_no_delete BEFORE DELETE ON observations BEGIN SELECT RAISE(ABORT,'append-only observations');END;
    PRAGMA user_version=1;
    ''')
    captured={};details={};events={};issues=[]
    for s in manifest['sources']:
        try:
            text,year=source(s);sid=key(s)
            c.execute('INSERT INTO sources VALUES(?,?,?,?,?,?)',(sid,s['raw_path'],s['raw_sha256'],s['source_url'],s.get('query_time'),dump(s)))
            captured[sid]=s
            if urlparse(s['source_url']).path.endswith('/results/'):
                for fact in parse_list(text,s['source_url']):events.setdefault(fact['event_url'],[]).append((fact,sid))
            else:details.setdefault(s['source_url'].rstrip('/'),[]).append(s)
        except (ValueError,TypeError,KeyError,OSError) as e:issues.append((None,str(e),s))
    # Legacy rows are inventory only if no retained listing can substantiate them.
    for old in manifest.get('inventory',[]):
        if old.get('event_url') and old['event_url'] not in events:
            events[old['event_url']]=[({**old,'legacy_inventory':True},None)]
    for url,observations in events.items():
        candidates=[]
        for fact,sid in observations:
            row={**fact,'competition':CODE,'match_id':'betexplorer:'+fact.get('provider_event_id',key(url)[:16]),
                 'gender':'men','age':'senior','score_basis':'regulation_90','neutral':None}
            row.update(home=team(row['home_name']),away=team(row['away_name']))
            if sid is not None:row['evidence']=captured[sid]
            period_sources=details.get(url.rstrip('/'),[]) if fact.get('home_90') is None else []
            for detail in period_sources or [None]:
                variant=dict(row)
                if detail is not None:variant['period_evidence']=detail
                try:
                    f=verify_row(variant);variant.update(f);variant['verified']=True;reason='VERIFIED_REGULATION_AND_PHASE;VENUE_UNKNOWN'
                except (ValueError,TypeError,KeyError,OSError) as e:
                    variant['verified']=False;reason=str(e);issues.append((url,reason,variant))
                variant['reason']=reason;candidates.append((variant,sid))
        good=[(r,s) for r,s in candidates if r['verified']]
        identities={(r['date'],r['home_name'],r['away_name'],r['home_90'],r['away_90'],
                     r.get('home_et'),r.get('away_et'),r.get('home_pen'),r.get('away_pen'),r['stage'],r['leg']) for r,_ in good}
        conflict=len(identities)>1
        selected,sid=(good or candidates)[0]
        valid=selected['verified'] and not conflict
        if conflict:issues.append((url,'SOURCE_RESULT_OR_PHASE_CONFLICT',selected))
        row={**selected,'era':'edition-'+str(selected['season']),'research_eligible':valid,'neutral':None}
        if not valid:
            row.update(home_90=None,away_90=None,home_et=None,away_et=None,home_pen=None,away_pen=None)
            row['reason']='SOURCE_RESULT_OR_PHASE_CONFLICT' if conflict else selected['reason']
        for side in ('home','away'):c.execute('INSERT OR IGNORE INTO teams VALUES(?,?)',(row[side],row[side+'_name']))
        c.execute('INSERT INTO matches VALUES('+','.join('?' for _ in range(22))+')',(
            row['match_id'],url,str(row['season']),row['date'],row['home'],row['away'],row.get('stage'),row['era'],row.get('leg'),row.get('tie_format'),None,
            row.get('home_90'),row.get('away_90'),row.get('home_et'),row.get('away_et'),row.get('home_pen'),row.get('away_pen'),int(valid),int(sid is not None),int(valid),row['reason'],dump(row)))
        for observed,source_id in candidates:
            if source_id:c.execute('INSERT OR IGNORE INTO observations VALUES(?,?,?,?)',(key([row['match_id'],source_id,observed]),row['match_id'],source_id,dump(observed)))
    for url,reason,row in issues:c.execute('INSERT OR IGNORE INTO quarantine VALUES(?,?,?,?)',(key([url,reason,row]),url,reason,dump(row)))
    c.commit()
    if c.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('foreign key error')
    c.close();return stats(out)

def connect(db):
    c=sqlite3.connect('file:'+str(Path(db).resolve())+'?mode=ro',uri=True);c.row_factory=sqlite3.Row;return c

def stats(db):
    c=connect(db)
    try:
        return {'database':str(Path(db).resolve()),'counts':{t:c.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in ('matches','teams','sources','observations','quarantine')},
                'stages':[dict(x) for x in c.execute('SELECT era,stage,tie_format,leg,research_eligible,count(*) n FROM matches GROUP BY 1,2,3,4,5')],
                'integrity_check':c.execute('PRAGMA integrity_check').fetchone()[0],
                'coverage':'PARTIAL_SOURCE_LIST_NOT_INDEPENDENTLY_CERTIFIED','historical_asof_certified':False}
    finally:c.close()

def rows(db):
    c=connect(db)
    try:
        result=[]
        for item in c.execute('SELECT payload_json FROM matches WHERE research_eligible=1 ORDER BY date,match_id'):
            r=json.loads(item[0]);verify_row(r)
            r.update(cohort=':'.join(map(str,(CODE,r['era'],r['stage'],r['tie_format'],r.get('leg') or 0))),
                     home_goals=r['home_90'],away_goals=r['away_90'],observed_at=r['evidence'].get('query_time'),
                     first_known_at=None)
            result.append(r)
        return result
    finally:c.close()
