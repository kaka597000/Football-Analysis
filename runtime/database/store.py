"""Append-only evidence and deterministic current-result projections."""
import datetime as dt
import hashlib
import json
import sqlite3
from pathlib import Path
from registry import LEAGUES, normalize_name, season_for

def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()

def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

def timestamp(value):
    try:return dt.datetime.fromisoformat(value.replace('Z','+00:00')).timestamp() if value else 0
    except (ValueError,TypeError):return 0

SCHEMA = '''
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS competitions(code TEXT PRIMARY KEY,name TEXT,scope TEXT,season_kind TEXT);
CREATE TABLE IF NOT EXISTS sources(source_id TEXT PRIMARY KEY,path TEXT,url TEXT,sha256 TEXT,observed_at TEXT,imported_at TEXT NOT NULL,provider TEXT);
CREATE TABLE IF NOT EXISTS teams(team_id TEXT PRIMARY KEY,scope TEXT NOT NULL,name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS aliases(scope TEXT,norm TEXT,alias TEXT,team_id TEXT REFERENCES teams,evidence TEXT,PRIMARY KEY(scope,norm));
CREATE TABLE IF NOT EXISTS team_redirects(old_id TEXT PRIMARY KEY,new_id TEXT REFERENCES teams,evidence TEXT);
CREATE TABLE IF NOT EXISTS ambiguous_aliases(scope TEXT,norm TEXT,provider_ids TEXT,PRIMARY KEY(scope,norm));
CREATE TABLE IF NOT EXISTS observations(obs_id TEXT PRIMARY KEY,source_id TEXT REFERENCES sources,source_key TEXT,competition TEXT REFERENCES competitions,season TEXT,date TEXT,kickoff_utc TEXT,home_id TEXT REFERENCES teams,away_id TEXT REFERENCES teams,orientation TEXT,status TEXT,h90 INTEGER,a90 INTEGER,extra_h INTEGER,extra_a INTEGER,pen_h INTEGER,pen_a INTEGER,quality TEXT,score_scope TEXT,provider TEXT,observed_at TEXT,first_known_at TEXT,raw_json TEXT);
CREATE INDEX IF NOT EXISTS obs_fixture ON observations(competition,date,home_id,away_id);
CREATE INDEX IF NOT EXISTS obs_provider_revision ON observations(provider,source_key,observed_at);
CREATE TABLE IF NOT EXISTS quarantine(issue_id TEXT PRIMARY KEY,kind TEXT,source_id TEXT,row_key TEXT,detail TEXT,resolved INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS fetch_log(request_id TEXT PRIMARY KEY,url TEXT,body TEXT,http_status INTEGER,observed_at TEXT,source_id TEXT,error TEXT);
CREATE TABLE IF NOT EXISTS coverage(code TEXT,season TEXT,provider TEXT,completed INTEGER,expected INTEGER,status TEXT,detail TEXT,PRIMARY KEY(code,season,provider));
CREATE TABLE IF NOT EXISTS matches(match_id TEXT PRIMARY KEY,competition TEXT,season TEXT,date TEXT,kickoff_utc TEXT,home_id TEXT,away_id TEXT,orientation TEXT,status TEXT,h90 INTEGER,a90 INTEGER,result_status TEXT,evidence_count INTEGER,training_eligible INTEGER);
CREATE TABLE IF NOT EXISTS match_evidence(match_id TEXT,obs_id TEXT,PRIMARY KEY(match_id,obs_id));
CREATE TABLE IF NOT EXISTS runs(run_id TEXT PRIMARY KEY,started_at TEXT,finished_at TEXT,summary TEXT);
CREATE TABLE IF NOT EXISTS resolutions(match_id TEXT PRIMARY KEY,h90 INTEGER,a90 INTEGER,rule TEXT,evidence_json TEXT,recorded_at TEXT);
CREATE VIEW IF NOT EXISTS training_90 AS SELECT m.*,h.name home,a.name away FROM matches m JOIN teams h ON h.team_id=m.home_id JOIN teams a ON a.team_id=m.away_id WHERE training_eligible=1;
'''

class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        for table in ['observations','matches']:
            if 'stage' not in [r[1] for r in self.db.execute('PRAGMA table_info('+table+')')]:
                self.db.execute('ALTER TABLE '+table+' ADD COLUMN stage TEXT')
        for code, (name, scope, kind, _) in LEAGUES.items():
            self.db.execute('INSERT OR IGNORE INTO competitions VALUES(?,?,?,?)',(code,name,scope,kind))

    def issue(self, kind, source, key, detail):
        self.db.execute('INSERT OR IGNORE INTO quarantine(issue_id,kind,source_id,row_key,detail) VALUES(?,?,?,?,?)',
                        (digest([kind,source,key,detail]),kind,source,str(key),json.dumps(detail,ensure_ascii=False)))

    def source(self, path, provider, url=None, observed=None):
        path = Path(path)
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        sid = digest([provider,sha,url,observed])
        self.db.execute('INSERT OR IGNORE INTO sources VALUES(?,?,?,?,?,?,?)',
                        (sid,str(path),url,sha,observed,now(),provider))
        return sid

    def resolve(self, tid):
        seen=set()
        while tid not in seen:
            seen.add(tid)
            row=self.db.execute('SELECT new_id FROM team_redirects WHERE old_id=?',(tid,)).fetchone()
            if not row: return tid
            tid=row[0]
        raise ValueError('team redirect cycle')

    def team(self, scope, name, provider_id=None, aliases=(), evidence='source name'):
        names = [name,*aliases]
        anchored = self.db.execute('SELECT team_id FROM aliases WHERE scope=? AND norm=?', (scope,normalize_name(name))).fetchone()
        ambiguous=self.db.execute('SELECT 1 FROM ambiguous_aliases WHERE scope=? AND norm=?',(scope,normalize_name(name))).fetchone()
        if ambiguous and provider_id is None:
            tid='unresolved-name:'+digest([scope,normalize_name(name)])[:24]
            self.db.execute('INSERT OR IGNORE INTO teams VALUES(?,?,?)',(tid,scope,name))
            self.issue('AMBIGUOUS_NAME_REQUIRES_PROVIDER_ID',None,name,{'scope':scope})
            return tid
        tid = 'espn:'+str(provider_id) if provider_id is not None else anchored[0] if anchored else 'name:'+digest([scope,normalize_name(name)])[:24]
        if anchored and provider_id is not None and self.resolve(anchored[0])!=self.resolve(tid):
            other=self.resolve(anchored[0])
            if other.startswith('espn:'):
                self.issue('ALIAS_COLLISION',None,name,{'scope':scope,'ids':[other,tid]})
            else:
                self.db.execute('INSERT OR IGNORE INTO teams VALUES(?,?,?)',(tid,scope,name))
                self.merge(other,tid,evidence)
        self.db.execute('INSERT OR IGNORE INTO teams VALUES(?,?,?)',(tid,scope,name))
        tid=self.resolve(tid)
        for alias in names:
            norm=normalize_name(alias)
            if self.db.execute('SELECT 1 FROM ambiguous_aliases WHERE scope=? AND norm=?',(scope,norm)).fetchone():continue
            row=self.db.execute('SELECT team_id FROM aliases WHERE scope=? AND norm=?',(scope,norm)).fetchone()
            if row and self.resolve(row[0])!=tid:
                other=self.resolve(row[0])
                if provider_id is not None and other.startswith('name:'):
                    self.merge(other,tid,evidence+'; explicit provider alias '+alias)
                else:
                    self.issue('ALIAS_COLLISION',None,alias,{'scope':scope,'ids':[other,tid]})
                    if other.startswith('espn:') and tid.startswith('espn:'):
                        self.db.execute('INSERT OR REPLACE INTO ambiguous_aliases VALUES(?,?,?)',(scope,norm,json.dumps([other,tid])))
                        self.db.execute('DELETE FROM aliases WHERE scope=? AND norm=?',(scope,norm))
            else:
                self.db.execute('INSERT OR IGNORE INTO aliases VALUES(?,?,?,?,?)',(scope,norm,alias,tid,evidence))
        return tid

    def merge(self, old, new, evidence):
        old,new=self.resolve(old),self.resolve(new)
        if old==new:return
        if old.startswith('espn:') and new.startswith('espn:'):
            raise ValueError('cannot merge distinct provider IDs')
        self.db.execute('INSERT INTO team_redirects VALUES(?,?,?)',(old,new,evidence))
        self.db.execute('UPDATE aliases SET team_id=? WHERE team_id=?',(new,old))

    def add(self, sid, row):
        row=dict(row)
        code=row['competition']
        if code not in LEAGUES:
            self.issue('UNKNOWN_COMPETITION',sid,row.get('source_key'),row);return
        if row.get('h90') is not None and (not isinstance(row['h90'],int) or not isinstance(row.get('a90'),int) or min(row['h90'],row['a90'])<0):
            self.issue('INVALID_SCORE',sid,row.get('source_key'),row);return
        columns=['source_key','competition','season','date','kickoff_utc','home_id','away_id','orientation','status','h90','a90','extra_h','extra_a','pen_h','pen_a','quality','score_scope','provider','observed_at','first_known_at','stage']
        prior=self.db.execute('SELECT * FROM observations WHERE provider=? AND source_key=? ORDER BY observed_at DESC LIMIT 1',(row.get('provider'),row.get('source_key'))).fetchone()
        if prior:
            if prior['status']=='completed' and prior['score_scope']=='regulation_90' and row.get('status')=='completed' and row.get('score_scope')=='unknown':
                return  # A coarser listing is not a retraction of a confirmed period record.
            stable=[k for k in columns if k not in ('observed_at','first_known_at')]
            if all(prior[k]==row.get(k) for k in stable) and (prior['observed_at'] or not row.get('observed_at')):
                return
        oid=digest([sid,row])
        self.db.execute('INSERT OR IGNORE INTO observations(obs_id,source_id,'+','.join(columns)+',raw_json) VALUES('+','.join('?' for _ in range(24))+')',
                        [oid,sid]+[row.get(k) for k in columns]+[json.dumps(row.get('raw',row),ensure_ascii=False)])

    def bridge(self):
        """Alias proof: >=2 distinct dates with same competition, anchored opponent and orientation.
        Never use scores, fuzzy spelling or same-date unique match alone as identity evidence.
        """
        for _ in range(4):
            rows=list(self.db.execute('SELECT competition,date,home_id,away_id,provider FROM observations WHERE orientation="home_away"'))
            buckets={}
            for r in rows:
                h,a=self.resolve(r['home_id']),self.resolve(r['away_id'])
                for side,known,unknown in [('home',a,h),('away',h,a)]:
                    buckets.setdefault((r['competition'],r['date'],side,known),set()).add((unknown,r['provider']))
            votes={}
            for (code,date,side,known),values in buckets.items():
                ids={x[0] for x in values}
                if len(ids)!=2:continue
                anchor=[i for i in ids if i.startswith(('espn:','kleague:','5dollar:'))]
                legacy=[i for i in ids if i.startswith('name:')]
                if len(anchor)==len(legacy)==1 and len({v[1] for v in values})>=2:
                    votes.setdefault((legacy[0],anchor[0]),set()).add(date)
            choices={}
            for (old,new),dates in votes.items():
                if len(dates)>=2:choices.setdefault(old,[]).append((new,dates))
            merged=0
            for old,options in choices.items():
                if len(options)==1:
                    new,dates=options[0]
                    self.merge(old,new,json.dumps({'rule':'two_fixture_anchor','dates':sorted(dates)}));merged+=1
                else:self.issue('AMBIGUOUS_TEAM_BRIDGE',None,old,[x[0] for x in options])
            if not merged:break
        # Korean official IDs vs Latin-name archives: compare complete observed calendar
        # fingerprints, not score outcomes. A unique >=8-date subset is required.
        signatures={}
        for row in self.db.execute("SELECT competition,date,home_id,away_id,quality FROM observations WHERE competition IN ('kor.1','kor.2') AND orientation='home_away'"):
            if row['quality'] in ('date_requires_verification','administrative_result'):continue
            for side in ['home','away']:
                tid=self.resolve(row[side+'_id'])
                signatures.setdefault(tid,set()).add((row['competition'],row['date'],side))
        official={tid:slots for tid,slots in signatures.items() if tid.startswith('kleague:')}
        for tid,slots in signatures.items():
            if not tid.startswith('name:') or len(slots)<8:continue
            targets=[pid for pid,calendar in official.items() if slots<=calendar]
            if len(targets)==1:
                self.merge(tid,targets[0],json.dumps({'rule':'unique_official_calendar_subset','slots':len(slots),'no_scores_used':True}))

    def curated_aliases(self):
        path=Path(__file__).with_name('aliases.json')
        for entry in json.loads(path.read_text()) if path.exists() else []:
            if not self.db.execute('SELECT 1 FROM teams WHERE team_id=?',(entry['target'],)).fetchone():continue
            target=self.resolve(entry['target'])
            for alias in entry['aliases']:
                row=self.db.execute('SELECT team_id FROM aliases WHERE scope=? AND norm=?',(entry['scope'],normalize_name(alias))).fetchone()
                if row:
                    other=self.resolve(row[0])
                    if other!=target:
                        if not other.startswith('name:'):
                            self.issue('CURATED_ALIAS_COLLISION',None,alias,{'target':target,'existing':other});continue
                        self.merge(other,target,entry['evidence'])
                else:self.db.execute('INSERT INTO aliases VALUES(?,?,?,?,?)',(entry['scope'],normalize_name(alias),alias,target,entry['evidence']))

    def project(self):
        self.db.execute("UPDATE quarantine SET resolved=1 WHERE kind='RESULT_CONFLICT'")
        self.db.execute('DELETE FROM match_evidence');self.db.execute('DELETE FROM matches')
        groups={}
        for row in self.db.execute('SELECT * FROM observations ORDER BY rowid'):
            r=dict(row);r['home_id']=self.resolve(r['home_id']);r['away_id']=self.resolve(r['away_id'])
            if r['competition']=='jpn.1' and r['provider']=='espn':
                raw=json.loads(r['raw_json']);slug=raw.get('season',{}).get('slug','')
                if '100-year' in slug or (r['date'].startswith('2026') and r['date']<'2026-07-01' and slug in ('placement-playoffs','championship')):
                    r['competition']='jpn.centennial.1'
            if r['competition'].startswith('jpn.'):
                r['season']=season_for(r['competition'],r['date'])
            # Date-only records retain their stated date; no invented timezone shift.
            key=(r['competition'],r['date'],r['home_id'],r['away_id'],r['orientation'])
            groups.setdefault(key,[]).append(r)
        blocked={'date_requires_verification','administrative_result','regulation_unresolved','unverified'}
        for key,rows in groups.items():
            mid=digest(key)[:32]
            # Preserve revisions, compare only latest observation per provider and source identity.
            latest={}
            for r in rows:
                k=(r['provider'],r['source_key'])
                if k not in latest or timestamp(r['observed_at'])>=timestamp(latest[k]['observed_at']):latest[k]=r
            good=[r for r in latest.values() if r['status']=='completed' and r['h90'] is not None and r['score_scope']=='regulation_90' and r['quality'] not in blocked]
            scores={(r['h90'],r['a90']) for r in good}
            chosen=max(good or list(latest.values()),key=lambda r:(timestamp(r['observed_at']),r['provider']))
            result='conflict' if len(scores)>1 else 'accepted' if scores else 'unresolved'
            h,a=next(iter(scores)) if len(scores)==1 else (None,None)
            if result=='conflict':
                official={score for score in scores if any(r['provider']=='kleague' and r['quality']=='official' and (r['h90'],r['a90'])==score for r in good)}
                if len(official)==1 and all(r['provider'] in ('canonical','kleague') for r in good):
                    h,a=next(iter(official));result='resolved_official'
                    self.db.execute('INSERT OR REPLACE INTO resolutions VALUES(?,?,?,?,?,?)',(mid,h,a,'KLEAGUE_OFFICIAL_FINAL_OVER_LEGACY',json.dumps([r['obs_id'] for r in good]),now()))
            if result=='conflict':
                corroborated={score for score in scores if {'espn','football-data'}<={r['provider'] for r in good if (r['h90'],r['a90'])==score}}
                if len(corroborated)==1 and all(r['provider'] in ('canonical','espn','football-data') for r in good):
                    h,a=next(iter(corroborated));result='resolved_corroborated'
                    self.db.execute('INSERT OR REPLACE INTO resolutions VALUES(?,?,?,?,?,?)',(mid,h,a,'ESPN_AND_FOOTBALL_DATA_AGREE_OVER_LEGACY',json.dumps([r['obs_id'] for r in good]),now()))
            eligible=int(result in ('accepted','resolved_corroborated','resolved_official') and key[4]=='home_away' and chosen['season']!='2026_TRANSITION_UNCLASSIFIED' and not any(t.startswith('unresolved-name:') for t in key[2:4]))
            stages=[r['stage'] for r in latest.values() if r['stage']]
            stage=next((s for s in stages if s in ('promotion_playoff','relegation_playoff','split_A','split_B')),chosen['stage'] or (stages[0] if stages else None))
            self.db.execute('INSERT INTO matches VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                            (mid,key[0],chosen['season'],key[1],chosen['kickoff_utc'],key[2],key[3],key[4],
                             'completed' if good else chosen['status'],h,a,result,len(rows),eligible,stage))
            self.db.executemany('INSERT INTO match_evidence VALUES(?,?)',[(mid,r['obs_id']) for r in rows])
            if result=='conflict':self.issue('RESULT_CONFLICT',None,mid,{'scores':list(scores),'observations':[r['obs_id'] for r in good]})
            if result=='conflict':self.db.execute("UPDATE quarantine SET resolved=0 WHERE kind='RESULT_CONFLICT' AND row_key=?",(mid,))
        # Previously flagged aliases are resolved only when their recorded identities now agree.
        for issue in list(self.db.execute("SELECT issue_id,detail FROM quarantine WHERE kind='ALIAS_COLLISION' AND resolved=0")):
            ids=json.loads(issue['detail']).get('ids',[])
            if ids and len({self.resolve(x) for x in ids})==1:
                self.db.execute('UPDATE quarantine SET resolved=1 WHERE issue_id=?',(issue['issue_id'],))
        self.db.commit()

    def audit(self):
        summary={t:self.db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ['sources','teams','aliases','team_redirects','observations','matches','training_90','quarantine']}
        summary['integrity']=self.db.execute('PRAGMA integrity_check').fetchone()[0]
        summary['foreign_key_violations']=len(self.db.execute('PRAGMA foreign_key_check').fetchall())
        summary['results']={r[0]:r[1] for r in self.db.execute('SELECT result_status,count(*) FROM matches GROUP BY result_status')}
        summary['competitions']=[dict(r) for r in self.db.execute('SELECT competition,season,count(*) matches,sum(training_eligible) eligible,min(date) first_date,max(date) last_date FROM matches GROUP BY competition,season ORDER BY competition,season')]
        summary['issues']={r[0]:r[1] for r in self.db.execute('SELECT kind,count(*) FROM quarantine WHERE resolved=0 GROUP BY kind')}
        summary['formal_kpi_added']=0
        summary['training_eligibility_scope']='90-minute score and identity gate only; not temporal validation or Champion acceptance'
        return summary
