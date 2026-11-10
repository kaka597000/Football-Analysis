"""Append-only source observations and independent UEFA Nations League inventory.

CLI: python -m runtime.nations_league.database build/import/stats/export ...
Input JSON/JSONL captures identify an ESPN event and retain source URL, raw path,
SHA256 and query time. Summary facts are derived again from raw bytes. Incoming
training_eligible/phase/score claims never grant eligibility. Missing evidence
remains inventory. Exports contain one explicitly selected phase and league.
"""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
from urllib.parse import parse_qs, urlparse

COMPETITION = 'uefa.nations'
SCHEMA_VERSION = 1
CANONICAL = Path('/workspace/WCPP/data/unified/WCPP_统一赛事库.sqlite')
STAGES = ('league-phase', 'group-stage', 'quarterfinals', 'semifinals', 'final', 'third-place', 'promotion-relegation-playoffs')
LEVELS = ('A', 'B', 'C', 'D', 'A-B', 'B-C', 'C-D')
TERMINAL = ('STATUS_FULL_TIME', 'STATUS_FINAL_AET', 'STATUS_FINAL_PEN')
SCORE_FIELDS = ('home_total', 'away_total', 'home_90', 'away_90', 'home_et', 'away_et', 'home_penalties', 'away_penalties')
FACT_FIELDS = ('season_year', 'league_level', 'stage', 'group_id', 'kickoff_at', 'date', 'status', 'neutral', *SCORE_FIELDS,
               'raw_verified', 'summary_verified', 'training_eligible', 'eligibility_reason', 'score_basis_evidence', 'phase_evidence_json', 'supplemental_phase_evidence_json', 'observed_at')


def now(): return datetime.now(timezone.utc).isoformat()
def dump(value): return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
def digest(value): return hashlib.sha256(dump(value).encode()).hexdigest()
def aware(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None: raise ValueError('timezone required')
    return parsed.astimezone(timezone.utc)
def integer(value):
    if value is None or value == '': return None
    if isinstance(value, dict): value = value.get('value', value.get('displayValue'))
    if isinstance(value, bool): raise ValueError('boolean score')
    number = float(value)
    if not number.is_integer() or not 0 <= number <= 99: raise ValueError('invalid integer score')
    return int(number)
def team_id(value):
    value = str(value or '').removeprefix('espn:')
    if not re.fullmatch(r'\d+', value): raise ValueError('explicit ESPN team ID required')
    return 'espn:' + value


def connect(path, readonly=False):
    path = Path(path).resolve()
    if path == CANONICAL.resolve(): raise ValueError('canonical database is read-only and outside this module')
    c = sqlite3.connect('file:' + str(path) + ('?mode=ro' if readonly else '?mode=rw'), uri=True)
    c.row_factory = sqlite3.Row; c.execute('PRAGMA foreign_keys=ON')
    if c.execute('PRAGMA user_version').fetchone()[0] != SCHEMA_VERSION:
        c.close(); raise ValueError('not a supported nations database')
    return c


def build(path):
    path = Path(path).resolve()
    if path == CANONICAL.resolve(): raise ValueError('canonical database cannot be changed')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb'): pass
    c = sqlite3.connect(path)
    try:
        c.executescript('''
        PRAGMA foreign_keys=ON;
        CREATE TABLE teams(team_id TEXT PRIMARY KEY CHECK(team_id GLOB 'espn:[0-9]*'), name TEXT NOT NULL);
        CREATE TABLE source_records(source_id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('summary','scoreboard','inventory')),
            source_url TEXT, raw_path TEXT, raw_sha256 TEXT, query_time TEXT, captured_at TEXT NOT NULL,
            raw_verified INTEGER NOT NULL CHECK(raw_verified IN (0,1)), metadata_json TEXT NOT NULL);
        CREATE TABLE import_runs(run_id TEXT PRIMARY KEY, input_sha256 TEXT NOT NULL, started_at TEXT NOT NULL,
            completed_at TEXT NOT NULL, counts_json TEXT NOT NULL);
        CREATE TABLE events(event_key TEXT PRIMARY KEY, competition TEXT NOT NULL CHECK(competition='uefa.nations'),
            provider_event_id TEXT NOT NULL, home_id TEXT NOT NULL REFERENCES teams(team_id), away_id TEXT NOT NULL REFERENCES teams(team_id),
            season_year INTEGER, league_level TEXT CHECK(league_level IN ('A','B','C','D','A-B','B-C','C-D')),
            stage TEXT CHECK(stage IN ('league-phase','group-stage','quarterfinals','semifinals','final','third-place','promotion-relegation-playoffs')),
            group_id TEXT, kickoff_at TEXT NOT NULL, date TEXT NOT NULL, status TEXT NOT NULL,
            neutral INTEGER CHECK(neutral IN (0,1)),
            home_total INTEGER CHECK(home_total BETWEEN 0 AND 99), away_total INTEGER CHECK(away_total BETWEEN 0 AND 99),
            home_90 INTEGER CHECK(home_90 BETWEEN 0 AND 99), away_90 INTEGER CHECK(away_90 BETWEEN 0 AND 99),
            home_et INTEGER CHECK(home_et BETWEEN 0 AND 99), away_et INTEGER CHECK(away_et BETWEEN 0 AND 99),
            home_penalties INTEGER CHECK(home_penalties BETWEEN 0 AND 99), away_penalties INTEGER CHECK(away_penalties BETWEEN 0 AND 99),
            raw_verified INTEGER NOT NULL CHECK(raw_verified IN(0,1)), summary_verified INTEGER NOT NULL CHECK(summary_verified IN(0,1)),
            training_eligible INTEGER NOT NULL CHECK(training_eligible IN(0,1)), eligibility_reason TEXT NOT NULL,
            score_basis_evidence TEXT, phase_evidence_json TEXT NOT NULL, supplemental_phase_evidence_json TEXT, observed_at TEXT, source_id TEXT NOT NULL REFERENCES source_records(source_id),
            last_observation_id TEXT NOT NULL REFERENCES observations(observation_id) DEFERRABLE INITIALLY DEFERRED, CHECK(home_id<>away_id), UNIQUE(competition,provider_event_id),
            CHECK(training_eligible=0 OR (summary_verified=1 AND raw_verified=1 AND home_90 IS NOT NULL AND away_90 IS NOT NULL AND stage IS NOT NULL AND league_level IS NOT NULL AND neutral IS NOT NULL)));
        CREATE TABLE observations(observation_id TEXT PRIMARY KEY, event_key TEXT NOT NULL REFERENCES events(event_key),
            source_id TEXT NOT NULL REFERENCES source_records(source_id), run_id TEXT NOT NULL REFERENCES import_runs(run_id),
            payload_json TEXT NOT NULL, projected INTEGER NOT NULL CHECK(projected IN(0,1)), recorded_at TEXT NOT NULL);
        CREATE TABLE quarantine(quarantine_id TEXT PRIMARY KEY, event_key TEXT, run_id TEXT NOT NULL REFERENCES import_runs(run_id),
            reason TEXT NOT NULL, input_json TEXT NOT NULL, recorded_at TEXT NOT NULL);
        CREATE INDEX observations_event ON observations(event_key);
        CREATE INDEX export_phase ON events(stage,league_level,training_eligible,date);
        CREATE TRIGGER observations_no_update BEFORE UPDATE ON observations BEGIN SELECT RAISE(ABORT,'append-only observations'); END;
        CREATE TRIGGER observations_no_delete BEFORE DELETE ON observations BEGIN SELECT RAISE(ABORT,'append-only observations'); END;
        CREATE TRIGGER sources_no_update BEFORE UPDATE ON source_records BEGIN SELECT RAISE(ABORT,'append-only sources'); END;
        CREATE TRIGGER sources_no_delete BEFORE DELETE ON source_records BEGIN SELECT RAISE(ABORT,'append-only sources'); END;
        PRAGMA user_version=1;
        ''')
        c.commit()
    finally: c.close()
    return {'database': str(path), 'schema_version': SCHEMA_VERSION}


def _hints(row, side):
    value = row.get(side)
    if isinstance(value, dict): return team_id(value.get('id', value.get('team_id'))), value.get('name', value.get('displayName', ''))
    return team_id(row.get(side+'_id', value)), row.get(side+'_name', '')


def _score_periods(competitor,status):
    try:
        from runtime.research_program.nations_phase import verify_period_scores
    except ModuleNotFoundError:
        from research_program.nations_phase import verify_period_scores
    return verify_period_scores(competitor,status)


def _phase(raw):
    try:
        from runtime.research_program.nations_phase import classify_source_phase
    except ModuleNotFoundError:
        from research_program.nations_phase import classify_source_phase
    return classify_source_phase(raw)


def normalize_capture(row, raw_root, raw_cache=None):
    """Return source and independently validated snapshot, or reject contradictory evidence."""
    competition = row.get('competition_id', row.get('competition', COMPETITION))
    if competition != COMPETITION: raise ValueError('competition mismatch')
    event_id = str(row.get('event_id', row.get('match_id', ''))).removeprefix('espn:')
    if not event_id.isdigit(): raise ValueError('explicit event ID required')
    kind = row.get('source_kind', 'inventory')
    if kind not in ('summary', 'scoreboard', 'inventory'): raise ValueError('unsupported source kind')
    query = row.get('query_time'); query = aware(query).isoformat() if query else None
    if query and aware(query) > datetime.now(timezone.utc): raise ValueError('future source query')
    source = {'kind': kind, 'source_url': row.get('source_url'), 'raw_path': row.get('raw_path'),
              'raw_sha256': row.get('raw_sha256'), 'query_time': query, 'raw_verified': 0}
    raw = None; path = Path(source['raw_path']) if source['raw_path'] else None
    if path and not path.is_absolute(): path = Path(raw_root)/path
    if path and path.is_file():
        if not path.resolve().is_relative_to(Path(raw_root).resolve()): raise ValueError('raw path outside allowed root')
        cache_key=str(path.resolve())
        cached=raw_cache.get(cache_key) if raw_cache is not None else None
        if cached is None:
            body=path.read_bytes();raw=json.loads(body);cached=(hashlib.sha256(body).hexdigest(),raw.get('data',raw))
            if raw_cache is not None:raw_cache[cache_key]=cached
        checksum,raw=cached
        if not source['raw_sha256'] or checksum != source['raw_sha256']: raise ValueError('raw SHA256 mismatch')
        if not isinstance(raw, dict): raise ValueError('raw object required')
        source['raw_path'] = str(path.resolve()); source['raw_verified'] = 1
    facts = {key: None for key in FACT_FIELDS}
    facts.update(raw_verified=source['raw_verified'], summary_verified=0, training_eligible=0,
                 eligibility_reason='INVENTORY_NO_VERIFIED_SUMMARY', phase_evidence_json='{}', observed_at=query)
    if raw is None:
        home, hn = _hints(row, 'home'); away, an = _hints(row, 'away')
        facts.update(kickoff_at=aware(row['kickoff_at']).isoformat(), status=row.get('status', 'UNKNOWN'))
        # Unverified stage/score claims remain in the immutable input, not trusted projection.
        facts['season_year'] = row.get('season_year')
    else:
        parsed = urlparse(source['source_url'] or '')
        prefix = '/apis/site/v2/sports/soccer/'+COMPETITION+'/'
        if parsed.scheme != 'https' or parsed.hostname != 'site.api.espn.com' or parsed.port not in (None,443) or parsed.path != prefix+kind:
            raise ValueError('source URL does not identify nations endpoint')
        if kind == 'summary' and parse_qs(parsed.query).get('event') != [event_id]: raise ValueError('source URL event mismatch')
        if kind == 'summary':
            head = raw.get('header', {})
            if head.get('league', {}).get('slug') != COMPETITION: raise ValueError('raw summary competition mismatch')
            events = [head]
        elif kind == 'scoreboard':
            if {x.get('slug') for x in raw.get('leagues', [])} != {COMPETITION}: raise ValueError('raw scoreboard competition mismatch')
            events = raw.get('events', [])
        else: raise ValueError('raw inventory requires an explicit source kind')
        events = [e for e in events if str(e.get('id')) == event_id]
        if len(events) != 1: raise ValueError('raw event identity absent or duplicated')
        event = events[0]; comps = event.get('competitions', [])
        if len(comps) != 1 or str(comps[0].get('id')) != event_id: raise ValueError('raw competition event mismatch')
        comp = comps[0]; competitors = comp.get('competitors', [])
        if len(competitors) != 2 or {x.get('homeAway') for x in competitors} != {'home','away'}: raise ValueError('raw sides ambiguous')
        sides = {x['homeAway']: x for x in competitors}
        home, away = (team_id(sides[s]['team']['id']) for s in ('home','away'))
        hn, an = (sides[s]['team'].get('displayName', sides[s]['team'].get('name', '')) for s in ('home','away'))
        for side, actual in (('home',home),('away',away)):
            if row.get(side) is not None or row.get(side+'_id') is not None:
                if _hints(row,side)[0] != actual: raise ValueError('supplied team contradicts raw')
        kickoff = aware(event.get('date') or comp['date'])
        if comp.get('date') and aware(comp['date']) != kickoff: raise ValueError('raw kickoff contradiction')
        if row.get('kickoff_at') and aware(row['kickoff_at']) != kickoff: raise ValueError('supplied kickoff contradicts raw')
        state = comp.get('status',event.get('status',{})).get('type',{})
        status = state.get('name','UNKNOWN'); completed = state.get('completed') is True
        neutral = comp.get('neutralSite')
        if neutral is not None and type(neutral) is not bool: raise ValueError('invalid raw neutral flag')
        phase = _phase(raw if kind=='summary' else event)
        supplemental=row.get('supplemental_phase_evidence')
        if kind=='summary' and supplemental and not phase.get('complete'):
            extra_path=Path(supplemental['source_path'])
            if not extra_path.is_absolute() or not extra_path.resolve().is_relative_to(Path(raw_root).resolve()):
                raise ValueError('supplemental raw path outside allowed root')
            try:
                from runtime.research_program.nations_phase import resolve_phase_evidence
            except ModuleNotFoundError:
                from research_program.nations_phase import resolve_phase_evidence
            context={'raw_root':str(Path(raw_root).resolve()),'competition':COMPETITION,'cohort':COMPETITION,'source_event_id':event_id,
                     'home':home,'away':away,'date':kickoff.date().isoformat(),'kickoff_at':kickoff.isoformat(),
                     'observed_at':max((v for v in (query,supplemental.get('query_time')) if v),key=aware)}
            phase=resolve_phase_evidence(raw,supplemental,context)
            facts['supplemental_phase_evidence_json']=dump(supplemental)
            facts['observed_at']=context['observed_at']
        stage, level = phase.get('stage'), phase.get('league_level')
        if stage not in (*STAGES,None) or level not in (*LEVELS,None): raise ValueError('unknown classified phase')
        facts.update(kickoff_at=kickoff.isoformat(),status=status,neutral=neutral,season_year=event.get('season',{}).get('year'),
                     stage=stage,league_level=level,group_id=str(comp.get('group',{}).get('groupId')) if comp.get('group',{}).get('groupId') is not None else None,
                     phase_evidence_json=dump(phase))
        for side in ('home','away'):
            facts[side+'_total'] = integer(sides[side].get('score')) if completed else None
            facts[side+'_penalties'] = integer(sides[side].get('shootoutScore')) if completed else None
        if stage in ('league-phase','group-stage') and status in ('STATUS_FINAL_AET','STATUS_FINAL_PEN'):
            raise ValueError('EXTRA_TIME_OR_PENALTIES_IN_LEAGUE_PHASE')
        if kind=='summary' and completed and status=='STATUS_FINAL_PEN':
            if None not in (facts['home_total'],facts['away_total']) and facts['home_total']!=facts['away_total']:
                raise ValueError('PENALTY_PRE_SHOOTOUT_SCORE_NOT_TIED')
            if None not in (facts['home_penalties'],facts['away_penalties']) and facts['home_penalties']==facts['away_penalties']:
                raise ValueError('COMPLETED_SHOOTOUT_SCORE_TIED')
        reasons = []
        if kind!='summary': reasons.append('SUMMARY_REQUIRED')
        if not completed or status not in TERMINAL: reasons.append('NOT_COMPLETED_SUPPORTED_STATUS')
        if not query: reasons.append('QUERY_TIME_UNKNOWN')
        elif completed and kickoff+timedelta(minutes=90)>aware(query): raise ValueError('completed response precedes kickoff plus 90 minutes')
        if kind=='summary' and completed and status in TERMINAL:
            period_scores={side:_score_periods(sides[side],comp.get('status',event.get('status',{}))) for side in ('home','away')}
            if all(period_scores.values()):
                for side,scores in period_scores.items():
                    facts[side+'_90']=scores['regulation'];facts[side+'_et']=scores['extra_time']
                facts['summary_verified']=1
                facts['score_basis_evidence']=dump({side:scores.get('score_basis_evidence',scores.get('basis')) for side,scores in period_scores.items()})
            else:reasons.append('EXPLICIT_REGULATION_PERIODS_UNRESOLVED')
        if not facts['summary_verified']: reasons.append('REGULATION_SCORE_NOT_VERIFIED')
        if not phase.get('complete') or not stage or not level: reasons.append('PHASE_OR_LEAGUE_UNVERIFIED')
        if neutral is None: reasons.append('NEUTRAL_UNKNOWN')
        facts['training_eligible']=int(not reasons)
        facts['eligibility_reason']='VERIFIED_90_MINUTE_PHASE' if not reasons else ';'.join(sorted(set(reasons)))
    if home==away: raise ValueError('same team fixture')
    facts['date']=aware(facts['kickoff_at']).date().isoformat()
    facts.update(event_key='espn:'+event_id,competition=COMPETITION,provider_event_id=event_id,home_id=home,away_id=away,
                 home_name=hn or home,away_name=an or away)
    source['source_id']=digest(source)
    return source,facts


def _projection_conflict(old,new):
    for key in ('home_id','away_id'):
        if old[key]!=new[key]: return 'EVENT_TEAM_IDENTITY_CONFLICT'
    for key in ('stage','league_level','neutral'):
        if old[key] is not None and new[key] is not None and old[key]!=new[key]: return 'VERIFIED_'+key.upper()+'_CONFLICT'
    if old['status'] in TERMINAL and new['status'] in TERMINAL:
        for key in ('date',*SCORE_FIELDS):
            if old[key] is not None and new[key] is not None and old[key]!=new[key]: return 'COMPLETED_RESULT_CONFLICT:'+key
    return None


def _prepare_captures(c,rows,raw_root):
    prepared=[]
    offered={}
    for item in rows:
        if item.get('source_kind')=='scoreboard' and item.get('query_time') and item.get('raw_path'):
            mid='espn:'+str(item.get('event_id',item.get('match_id',''))).removeprefix('espn:')
            raw=Path(item['raw_path']);raw=raw if raw.is_absolute() else Path(raw_root)/raw
            offered.setdefault(mid,[]).append({'source_path':str(raw.resolve()),'source_sha256':item.get('raw_sha256'),'source_url':item.get('source_url'),'query_time':item['query_time']})
    for row in rows:
        supplied=dict(row)
        if row.get('source_kind')=='summary' and not row.get('supplemental_phase_evidence'):
            mid='espn:'+str(row.get('event_id',row.get('match_id',''))).removeprefix('espn:')
            candidates=list(offered.get(mid,[]))
            stored=c.execute("SELECT s.* FROM observations o JOIN source_records s USING(source_id) WHERE o.event_key=? AND s.kind='scoreboard' AND s.raw_verified=1 AND s.query_time IS NOT NULL ORDER BY s.query_time DESC LIMIT 1",(mid,)).fetchone()
            if stored:candidates.append({'source_path':stored['raw_path'],'source_sha256':stored['raw_sha256'],'source_url':stored['source_url'],'query_time':stored['query_time']})
            if candidates:
                supplied['supplemental_phase_evidence']=max(candidates,key=lambda x:aware(x['query_time']))
        prepared.append(supplied)
    return prepared


def import_captures(path,rows,raw_root):
    input_hash=digest(rows);counts=Counter();raw_cache={};c=connect(path)
    try:
        prepared=_prepare_captures(c,rows,raw_root)
        raw_state={}
        for row in prepared:
            paths=[row.get('raw_path'),row.get('supplemental_phase_evidence',{}).get('source_path')]
            for value in paths:
                if value:
                    raw_path=Path(value);raw_path=raw_path if raw_path.is_absolute() else Path(raw_root)/raw_path
                    if str(raw_path) not in raw_state:
                        raw_state[str(raw_path)]=hashlib.sha256(raw_path.read_bytes()).hexdigest() if raw_path.is_file() and raw_path.resolve().is_relative_to(Path(raw_root).resolve()) else None
        # Evidence dependencies are part of idempotency, including actual bytes.
        run_id=digest(['nations-import-v1',input_hash,digest(prepared),raw_state])
        existing=c.execute('SELECT counts_json FROM import_runs WHERE run_id=?',(run_id,)).fetchone()
        if existing: return {'run_id':run_id,'idempotent':True,'original_counts':json.loads(existing[0])}
        with c:
            started=now();c.execute('INSERT INTO import_runs VALUES(?,?,?,?,?)',(run_id,input_hash,started,started,'{}'))
            for row,supplied in zip(rows,prepared):
                try:
                    source,facts=normalize_capture(supplied,raw_root,raw_cache)
                    sid=source['source_id'];event=facts['event_key'];oid=digest([sid,event,facts])
                    if c.execute('SELECT 1 FROM observations WHERE observation_id=?',(oid,)).fetchone(): counts['duplicate_capture']+=1;continue
                    old=c.execute('SELECT e.*,s.kind,s.query_time FROM events e JOIN source_records s USING(source_id) WHERE event_key=?',(event,)).fetchone()
                    if old:
                        conflict=_projection_conflict(old,facts)
                        if conflict: raise ValueError(conflict)
                    c.execute('INSERT OR IGNORE INTO source_records VALUES(?,?,?,?,?,?,?,?,?)',(sid,source['kind'],source['source_url'],source['raw_path'],source['raw_sha256'],source['query_time'],now(),source['raw_verified'],dump(row)))
                    for side in ('home','away'): c.execute('INSERT OR IGNORE INTO teams VALUES(?,?)',(facts[side+'_id'],facts[side+'_name']))
                    projected=True
                    if old:
                        rank={'inventory':0,'scoreboard':1,'summary':2}
                        projected=rank[source['kind']]>=rank[old['kind']]
                        if old['observed_at'] and (not facts['observed_at'] or aware(facts['observed_at'])<aware(old['observed_at'])): projected=False
                        if old['status'] in TERMINAL and facts['status'] not in TERMINAL: projected=False
                        if old['summary_verified'] and not facts['summary_verified']: projected=False
                    if not old:
                        columns=('event_key','competition','provider_event_id','home_id','away_id',*FACT_FIELDS,'source_id','last_observation_id')
                        values=[facts[k] for k in columns[:-2]]+[sid,oid]
                        c.execute('INSERT INTO events('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',values)
                        counts['new_events']+=1
                    elif projected:
                        # Preserve independently verified phase/venue if a later capture omits it.
                        for k in ('stage','league_level','neutral'):
                            if facts[k] is None and old[k] is not None: projected=False;break
                        if projected:
                            c.execute('UPDATE events SET '+','.join(k+'=?' for k in FACT_FIELDS)+',source_id=?,last_observation_id=? WHERE event_key=?',[old[k] if k=='group_id' and facts[k] is None else facts[k] for k in FACT_FIELDS]+[sid,oid,event])
                            counts['updated_events']+=1
                    c.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?)',(oid,event,sid,run_id,dump(facts),int(projected),now()))
                    counts['observations']+=1
                except (ValueError,TypeError,KeyError,AttributeError,OverflowError,OSError,json.JSONDecodeError) as exc:
                    reason=str(exc);qid=digest([row,reason])
                    c.execute('INSERT OR IGNORE INTO quarantine VALUES(?,?,?,?,?,?)',(qid,row.get('event_id',row.get('match_id')),run_id,reason,dump(row),now()));counts['quarantined']+=1
            for raw_path,checksum in raw_state.items():
                if checksum is not None and hashlib.sha256(Path(raw_path).read_bytes()).hexdigest()!=checksum:
                    raise ValueError('source bytes changed during import')
            c.execute('UPDATE import_runs SET completed_at=?,counts_json=? WHERE run_id=?',(now(),dump(dict(counts)),run_id))
            if c.execute('PRAGMA foreign_key_check').fetchall(): raise ValueError('foreign key integrity failure')
        return {'run_id':run_id,'counts':dict(counts),'idempotent':False}
    finally:c.close()


def stats(path):
    c=connect(path,True)
    try:
        return {'database':str(Path(path).resolve()),'schema_version':SCHEMA_VERSION,
                'counts':{t:c.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ('teams','events','source_records','observations','import_runs','quarantine')},
                'by_phase':[dict(r) for r in c.execute('SELECT stage,league_level,status,training_eligible,count(*) n FROM events GROUP BY stage,league_level,status,training_eligible ORDER BY stage,league_level,status')],
                'eligible':c.execute('SELECT count(*) FROM events WHERE training_eligible=1').fetchone()[0],
                'eligibility_reasons':[dict(r) for r in c.execute('SELECT eligibility_reason,count(*) n FROM events WHERE training_eligible=0 GROUP BY eligibility_reason ORDER BY n DESC')],
                'quarantine_reasons':[dict(r) for r in c.execute('SELECT reason,count(*) n FROM quarantine GROUP BY reason ORDER BY n DESC')],
                'future_fixtures':c.execute('SELECT count(*) FROM events WHERE kickoff_at>?',(now(),)).fetchone()[0],
                'integrity_check':c.execute('PRAGMA integrity_check').fetchone()[0],
                'foreign_key_errors':[tuple(r) for r in c.execute('PRAGMA foreign_key_check')],
                'coverage':'PARTIAL_UNTIL_INDEPENDENT_INVENTORY_VERIFICATION','asof_status':'NOT_CERTIFIED'}
    finally:c.close()


def export(path,out,stage,league_level,inventory=False):
    if stage not in STAGES or league_level not in LEVELS: raise ValueError('explicit supported stage and league required')
    c=connect(path,True)
    try:
        sql='SELECT e.*,s.source_url,s.raw_path,s.raw_sha256,s.query_time FROM events e JOIN source_records s USING(source_id) WHERE stage=? AND league_level=?'
        if not inventory:sql+=' AND training_eligible=1'
        rows=[dict(r) for r in c.execute(sql+' ORDER BY date,event_key',(stage,league_level))]
        for r in rows:
            for proof in ({'source_path':r['raw_path'],'source_sha256':r['raw_sha256']},json.loads(r['supplemental_phase_evidence_json']) if r['supplemental_phase_evidence_json'] else None):
                if proof and r['training_eligible']:
                    if hashlib.sha256(Path(proof['source_path']).read_bytes()).hexdigest()!=proof['source_sha256']:
                        raise ValueError('export evidence bytes no longer match verified capture')
            r.update(match_id=r['event_key'],cohort=COMPETITION+':'+league_level+':'+stage,home=r['home_id'],away=r['away_id'],
                     home_goals=r['home_90'],away_goals=r['away_90'],score_basis='regulation_90' if r['summary_verified'] else None,
                     first_known_at=None,gender='men',age='senior',entity='national',source_event_id=r['provider_event_id'],
                     phase_evidence={'source_path':r['raw_path'],'source_sha256':r['raw_sha256'],'source_url':r['source_url'],'query_time':r['query_time']},
                     neutral=None if r['neutral'] is None else bool(r['neutral']))
            if r['supplemental_phase_evidence_json']:r['supplemental_phase_evidence']=json.loads(r['supplemental_phase_evidence_json'])
        output={'schema':'NATIONS_PHASE_EXPORT_1','competition':COMPETITION,'league_level':league_level,'stage':stage,
                'rows':rows,'inventory_only':inventory,'historical_asof_eligible':False,'active_model_unchanged':True,
                'database_sha256':hashlib.sha256(Path(path).read_bytes()).hexdigest(),'created_at':now()}
        out=Path(out);out.parent.mkdir(parents=True,exist_ok=True)
        with out.open('x') as f:f.write(dump(output)+'\n')
        return {'output':str(out),'rows':len(rows),'stage':stage,'league_level':league_level}
    finally:c.close()


def load_rows(path):
    text=Path(path).read_text()
    if Path(path).suffix=='.jsonl':return [json.loads(line) for line in text.splitlines() if line.strip()]
    value=json.loads(text);return value['rows'] if isinstance(value,dict) else value


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('build');p.add_argument('--db',required=True)
    p=sub.add_parser('import');p.add_argument('--db',required=True);p.add_argument('--input',required=True);p.add_argument('--raw-root',required=True)
    p=sub.add_parser('stats');p.add_argument('--db',required=True)
    p=sub.add_parser('export');p.add_argument('--db',required=True);p.add_argument('--out',required=True);p.add_argument('--stage',required=True,choices=STAGES);p.add_argument('--league-level',required=True,choices=LEVELS);p.add_argument('--inventory',action='store_true')
    a=parser.parse_args()
    if a.command=='build':result=build(a.db)
    elif a.command=='import':result=import_captures(a.db,load_rows(a.input),a.raw_root)
    elif a.command=='stats':result=stats(a.db)
    else:result=export(a.db,a.out,a.stage,a.league_level,a.inventory)
    print(dump(result))


if __name__=='__main__':main()
