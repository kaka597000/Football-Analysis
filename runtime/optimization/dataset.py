"""Create a separate auditable research SQLite database; Canonical stays read-only."""
import json
import math
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from audit import connect, csv_write
from schema import digest, file_hash, fixture, save, now

LEAGUES = {'eng.1':'EPL','esp.1':'La_liga','ger.1':'Bundesliga','ita.1':'Serie_A','fra.1':'Ligue_1'}
SCOPE = {'eng.1':'ENG','esp.1':'ESP','ger.1':'GER','ita.1':'ITA','fra.1':'FRA'}
EXPECTED = {'eng.1':380,'esp.1':380,'ita.1':380,'ger.1':306,'por.1':306,'eng.2':552,'ger.2':306,'ned.1':306}

def norm(s):
    return ' '.join(unicodedata.normalize('NFKC',s).split()).casefold()

def export(db, registry, staging, out, audit_path=None):
    out = Path(out); out.mkdir(parents=True, exist_ok=False)
    c = connect(db)
    reg = json.loads(Path(registry).read_text())
    redirects = dict(c.execute('SELECT old_id,new_id FROM team_redirects'))
    def resolve(t):
        seen=set()
        while t in redirects:
            if t in seen: raise ValueError('cyclic team redirect')
            seen.add(t);t=redirects[t]
        return t
    names = {resolve(r['team_id']):r['name'] for r in c.execute('SELECT * FROM teams')}
    aliases = defaultdict(set)
    for r in c.execute('SELECT * FROM aliases'):
        aliases[(r['scope'],norm(r['alias']))].add(resolve(r['team_id']))
    for r in c.execute('SELECT * FROM teams'):
        aliases[(r['scope'],norm(r['name']))].add(resolve(r['team_id']))
    # Explicit provider spelling crosswalk; no fuzzy identity matching.
    crosswalk={'GER':{'Borussia M.Gladbach':'Borussia Mönchengladbach','RasenBallsport Leipzig':'RB Leipzig','Mainz 05':'Mainz','FC Heidenheim':'1. FC Heidenheim 1846','Greuther Fuerth':'SpVgg Greuther Fürth'},'FRA':{'Paris Saint Germain':'Paris Saint-Germain','Saint-Etienne':'Saint-Étienne'},'ESP':{'SD Huesca':'Huesca'}}
    for scope, pairs_ in crosswalk.items():
        for source,target in pairs_.items():
            candidates=aliases.get((scope,norm(target)),set())
            if len(candidates)==1: aliases[(scope,norm(source))]=set(candidates)
    allrows = [dict(r) for r in c.execute('SELECT * FROM matches ORDER BY date,match_id')]
    regular = [r for r in allrows if r['stage'] in ('regular','regular-season')]
    pairs=Counter((r['competition'],r['season'],resolve(r['home_id']),resolve(r['away_id'])) for r in regular)
    counts=Counter((r['competition'],r['season']) for r in regular if r['training_eligible'])
    # Existing archived process is reused only after ID, identities, day, and score agree.
    pp=Path('/workspace/WCPP/reports/python-upset-20261013/enriched-history.json')
    process={r['match_id']:r for r in json.loads(pp.read_text())} if pp.exists() else {}
    process_hash=file_hash(pp) if pp.exists() else None
    # Explicit neutral evidence from already audited artifacts; no unknown->false default.
    venues={}
    for p in Path('/workspace/WCPP/models/ml-continued-20261012/data-final').glob('*-history.json'):
        for r in json.loads(p.read_text()):
            if type(r.get('neutral')) is bool:
                venues[(r['date'],resolve(r['home']),resolve(r['away']))]=r['neutral']
    research=sqlite3.connect(out/'research.sqlite')
    research.executescript('''CREATE TABLE fixtures(match_id TEXT PRIMARY KEY,cohort TEXT,date TEXT,season TEXT,home TEXT,away TEXT,payload TEXT);
    CREATE TABLE labels(match_id TEXT PRIMARY KEY,home_goals INTEGER,away_goals INTEGER,first_known_at TEXT,source TEXT);
    CREATE TABLE facts(fact_id TEXT PRIMARY KEY,match_id TEXT,provider TEXT,kind TEXT,date TEXT,first_known_at TEXT,payload TEXT);
    CREATE TABLE rejections(match_id TEXT,reason TEXT,payload TEXT);
    CREATE TABLE source_files(path TEXT PRIMARY KEY,sha256 TEXT,provider TEXT);
    CREATE TABLE provider_identity(provider TEXT,competition TEXT,provider_name TEXT,canonical_team_id TEXT,evidence TEXT,PRIMARY KEY(provider,competition,provider_name));''')
    fixtures=[];labels={};facts=[];rejected=[];mapping={};target_codes={x.split(':')[0] for x in reg['models']} | set(LEAGUES)
    today=now()[:10]
    for r in allrows:
        code=r['competition'];reason=None
        if code not in target_codes: reason='OUTSIDE_REGISTERED_AND_XG_LEAGUE_SCOPE'
        elif not r['training_eligible'] or r['result_status']=='conflict': reason='UNSETTLED_OR_CONFLICT_OR_NON90'
        elif r['date']>=today: reason='CURRENT_DAY_OR_FUTURE'
        elif r['stage'] not in ('regular','regular-season'): reason='PHASE_NOT_DOMESTIC_REGULAR'
        elif code in EXPECTED and counts[(code,r['season'])]>EXPECTED[code]: reason='SEASON_CARDINALITY_ABOVE_EXPECTED_QUARANTINED'
        elif pairs[(code,r['season'],resolve(r['home_id']),resolve(r['away_id']))]>1: reason='REPEATED_ORIENTED_PAIR_QUARANTINED'
        elif r['h90'] is None or r['a90'] is None: reason='MISSING_90_MINUTE_LABEL'
        if reason:
            rejected.append(dict(match_id=r['match_id'],competition=code,season=r['season'],reason=reason))
            research.execute('INSERT INTO rejections VALUES(?,?,?)',(r['match_id'],reason,json.dumps(r)))
            continue
        h,a=resolve(r['home_id']),resolve(r['away_id']);mid=r['match_id']
        f=dict(match_id=mid,cohort=code,competition=code,date=r['date'],season=r['season'],home=h,away=a,
               home_name=names[h],away_name=names[a],stage=r['stage'],neutral=venues.get((r['date'],h,a)),
               gender='male',age='senior',era='DOMESTIC_REGULAR',leg=None,score_basis='regulation_90',
               kickoff_at=r['kickoff_utc'],time_precision='instant' if r['kickoff_utc'] else 'date',first_known_at=None)
        fixtures.append(f);mapping[(code,r['date'],h,a)]=mid
        labels[mid]=dict(match_id=mid,home_goals=r['h90'],away_goals=r['a90'],first_known_at=None,source='Canonical:accepted90')
        research.execute('INSERT INTO fixtures VALUES(?,?,?,?,?,?,?)',(mid,code,r['date'],r['season'],h,a,json.dumps(f)))
        research.execute('INSERT INTO labels VALUES(?,?,?,?,?)',(mid,r['h90'],r['a90'],None,'Canonical:accepted90'))
        p=process.get(mid)
        if p and (p['date'],resolve(p['home']),resolve(p['away']),p['home_goals'],p['away_goals'])==(r['date'],h,a,r['h90'],r['a90']):
            vals=p.get('process',{});clean={k:vals[k] for k in ('home_shots','away_shots','home_sot','away_sot') if type(vals.get(k)) in (int,float) and math.isfinite(vals[k]) and vals[k]>=0}
            if clean:
                facts.append(dict(fact_id=digest([mid,'process',clean]),match_id=mid,provider='football-data.co.uk',kind='process',date=r['date'],
                                  first_known_at=None,source_published_at=None,fetched_at=p.get('process_observed_at'),source_url=p.get('process_source_url'),
                                  raw_sha256=process_hash,availability_status='RETROSPECTIVE_PRIOR_ONLY',**clean))
    identity_rejections=[];xgcounts=Counter();raw_seen=set()
    for path in [Path(staging)/'understat/rows.jsonl']:
        row_file_hash=file_hash(path)
        for line in path.read_text().splitlines():
            x=json.loads(line);league=x.get('league');code=next((k for k,v in LEAGUES.items() if v==league),x.get('competition'))
            if code not in LEAGUES: continue
            hd=x.get('home_name') or x.get('home');ad=x.get('away_name') or x.get('away');day=x['date'][:10]
            ht=aliases.get((SCOPE[code],norm(hd)),set());at=aliases.get((SCOPE[code],norm(ad)),set())
            if len(ht)!=1 or len(at)!=1:
                identity_rejections.append(dict(provider='understat',competition=code,date=day,home=hd,away=ad,reason='NO_UNAMBIGUOUS_EXACT_ALIAS'))
                continue
            h,a=next(iter(ht)),next(iter(at));mid=mapping.get((code,day,h,a))
            if mid is None:
                identity_rejections.append(dict(provider='understat',competition=code,date=day,home=hd,away=ad,reason='NO_ACCEPTED_EXACT_DATE_IDENTITY_MATCH'))
                continue
            label=labels[mid]
            if (int(x['home_goals']),int(x['away_goals']))!=(label['home_goals'],label['away_goals']):
                identity_rejections.append(dict(provider='understat',competition=code,date=day,home=hd,away=ad,reason='90_MINUTE_SCORE_CONFLICT'))
                continue
            vals=[float(x[k]) for k in ('home_xg','away_xg')]
            if any(not math.isfinite(v) or not 0<=v<=30 for v in vals): raise ValueError('invalid true xG')
            key=(mid,'understat')
            if key in raw_seen: continue
            raw_seen.add(key)
            fact=dict(x,first_observed_at=x.get('source_fetched_at_beijing'),match_id=mid,provider='understat',kind='xg',date=day,first_known_at=None,source_published_at=None,
                      availability_status='RETROSPECTIVE_PRIOR_ONLY',home_xg=vals[0],away_xg=vals[1],
                      fact_id=digest([mid,'understat',vals]),source_path=str(path),rows_sha256=row_file_hash)
            facts.append(fact);xgcounts[code]+=1
            for title,tid in [(hd,h),(ad,a)]:
                research.execute('INSERT OR IGNORE INTO provider_identity VALUES(?,?,?,?,?)',('understat',code,title,tid,'exact registered alias or explicit provider spelling crosswalk + exact date/home-away + 90-minute score crosscheck'))
    for fact in facts:
        research.execute('INSERT INTO facts VALUES(?,?,?,?,?,?,?)',(fact['fact_id'],fact['match_id'],fact['provider'],fact['kind'],fact['date'],fact.get('first_known_at'),json.dumps(fact)))
    for path,provider in [(pp,'football-data.co.uk')]+[(p,'understat') for p in Path(staging).glob('understat/**/*') if p.is_file()]:
        if path.exists(): research.execute('INSERT OR IGNORE INTO source_files VALUES(?,?,?)',(str(path),file_hash(path),provider))
    research.commit();research.close();c.close()
    for name,rows in [('fixtures.jsonl',fixtures),('labels.jsonl',list(labels.values())),('facts.jsonl',facts)]:
        with (out/name).open('x') as f:
            for r in rows: f.write(json.dumps(r,ensure_ascii=False,allow_nan=False)+'\n')
    save(out/'rejections.json',rejected);save(out/'identity-rejections.json',identity_rejections)
    counts=Counter(r['cohort'] for r in fixtures)
    save(out/'manifest.json',dict(created_at=now(),canonical=str(db),canonical_modified=False,mode='RETROSPECTIVE_PRIOR_ONLY',
                                first_known_at_policy='NULL preserved; no historical as-of claim',cohort_counts=dict(counts),xg_counts=dict(xgcounts),
                                files={p.name:file_hash(p) for p in out.iterdir() if p.is_file()},
                                active_cohorts=list(reg['models']),rejected_n=len(rejected),identity_rejected_n=len(identity_rejections)))
    return dict(fixtures=len(fixtures),facts=len(facts),xg=dict(xgcounts),identity_rejected_n=len(identity_rejections))

def load(root):
    root=Path(root)
    manifest=json.loads((root/'manifest.json').read_text())
    for name,sha in manifest['files'].items():
        if file_hash(root/name)!=sha: raise ValueError('dataset hash mismatch: '+name)
    def lines(name):return [json.loads(s) for s in (root/name).read_text().splitlines()]
    return lines('fixtures.jsonl'),{r['match_id']:r for r in lines('labels.jsonl')},lines('facts.jsonl'),manifest
