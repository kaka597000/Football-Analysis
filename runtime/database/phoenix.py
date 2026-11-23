"""Import the official Phoenix competition catalogue, fixtures and price observations into WCPP SQLite."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlparse

import phoenix_identity

# Provider competition identifiers, checked against the public directory's English names.
SCOPES={'epl':'eng.1','esp':'esp.1','ger':'ger.1','ita':'ita.1','fra':'fra.1',
        'j1':'jpn.1','k1':'kor.1','aus':'aus.1','mls':'usa.1','nor1':'nor.1','swe1':'swe.1',
        'ned1':'ned.1','ucl':'uefa.champions','uel':'uefa.europa','uecl':'uefa.europa.conf',
        'acl':'afc.champions','unations':'uefa.nations','jlc':'jpn.league_cup','bra1':'bra.1'}
ARCHIVE={'英格兰超级联赛':'eng.1','西班牙甲级联赛':'esp.1'}
REVIEW={**ARCHIVE,'法国甲级联赛':'fra.1','德国甲级联赛':'ger.1','意大利甲级联赛':'ita.1',
        '日本职业足球甲级联赛（J1）':'jpn.1','韩国职业甲级联赛':'kor.1','韩国职业足球甲级联赛':'kor.1',
        '韩国职业甲级足球联赛':'kor.1',
        '澳大利亚足球超级联赛':'aus.1','美国职业大联盟联赛':'usa.1','巴西甲级联赛':'bra.1',
        '荷兰甲级联赛':'ned.1','挪威超级联赛':'nor.1','瑞典超级联赛':'swe.1',
        '欧洲国家联赛':'uefa.nations','欧洲冠军联赛':'uefa.champions','欧洲足联欧洲联赛':'uefa.europa',
        '欧洲足联欧洲协会联赛':'uefa.europa.conf','亚洲冠军精英联赛':'afc.champions','日本联赛杯':'jpn.league_cup'}
PROVIDER='phoenix'
ORIGIN='data.soccerdreamai.com'
SCHEMA="""
CREATE TABLE IF NOT EXISTS phoenix_league_coverage(
  catalog_source TEXT REFERENCES sources(source_id), coverage_source TEXT REFERENCES sources(source_id),
  competition TEXT REFERENCES competitions(code), provider_code TEXT, provider_name TEXT,
  fixtures_advertised INTEGER, seasons INTEGER, first_fixture TEXT, prices_advertised INTEGER,
  catalog_json TEXT, coverage_json TEXT, PRIMARY KEY(catalog_source,coverage_source,competition));
CREATE TABLE IF NOT EXISTS phoenix_bookmakers(
  source_id TEXT REFERENCES sources(source_id),code TEXT,displayed_name TEXT,kind TEXT,payload_json TEXT,
  PRIMARY KEY(source_id,code));
CREATE TABLE IF NOT EXISTS phoenix_fixtures(
  feed TEXT, fixture_id TEXT, competition TEXT REFERENCES competitions(code), season TEXT,
  kickoff_source TEXT, home TEXT, away TEXT, score_home INTEGER, score_away INTEGER,
  score_basis TEXT, status TEXT, price_rows_advertised INTEGER, source_id TEXT REFERENCES sources(source_id),
  observed_at TEXT, PRIMARY KEY(feed,fixture_id));
CREATE INDEX IF NOT EXISTS phoenix_fixture_competition ON phoenix_fixtures(competition,kickoff_source);
CREATE TABLE IF NOT EXISTS phoenix_fixture_evidence(
  feed TEXT,fixture_id TEXT,source_id TEXT REFERENCES sources(source_id),payload_json TEXT,
  PRIMARY KEY(feed,fixture_id,source_id));
CREATE TABLE IF NOT EXISTS phoenix_price_points(
  source_id TEXT REFERENCES sources(source_id),feed TEXT,fixture_id TEXT,bookmaker TEXT,seq INTEGER,
  market TEXT, source_time TEXT,seconds_to_kickoff INTEGER, timing_basis TEXT,
  home REAL,draw REAL,away REAL,valid_price INTEGER,
  PRIMARY KEY(source_id,feed,fixture_id,bookmaker,market,seq));
CREATE INDEX IF NOT EXISTS phoenix_price_lookup ON phoenix_price_points(feed,fixture_id,bookmaker,seconds_to_kickoff);
CREATE TABLE IF NOT EXISTS phoenix_review_evidence(
  source_id TEXT REFERENCES sources(source_id),prediction_id TEXT,fixture_id TEXT,competition TEXT REFERENCES competitions(code),
  kickoff_source TEXT,home TEXT,away TEXT,bookmaker TEXT,open_h REAL,open_d REAL,open_a REAL,
  close_h REAL,close_d REAL,close_a REAL,open_handicap REAL,close_handicap REAL,
  open_total_line REAL,close_total_line REAL,payload_json TEXT,PRIMARY KEY(source_id,prediction_id,fixture_id));
CREATE INDEX IF NOT EXISTS phoenix_provider_review_match ON phoenix_review_evidence(competition,fixture_id);
CREATE TABLE IF NOT EXISTS phoenix_market_evidence(
  source_id TEXT REFERENCES sources(source_id),feed TEXT,fixture_id TEXT,endpoint TEXT,
  payload_json TEXT, PRIMARY KEY(source_id,feed,fixture_id,endpoint));
CREATE TABLE IF NOT EXISTS phoenix_trial_prices(
  source_id TEXT REFERENCES sources(source_id), fixture_id TEXT, market TEXT, bookmaker TEXT,
  phase TEXT, seq INTEGER, source_time TEXT, line REAL, home REAL, draw REAL, away REAL, payload_json TEXT,
  PRIMARY KEY(source_id,fixture_id,market,bookmaker,phase,seq));
CREATE INDEX IF NOT EXISTS phoenix_trial_market_lookup ON phoenix_trial_prices(fixture_id,market,bookmaker,source_time);
CREATE TABLE IF NOT EXISTS phoenix_detail_versions(
  feed TEXT,fixture_id TEXT,source_id TEXT REFERENCES sources(source_id),observed_at TEXT,
  PRIMARY KEY(feed,fixture_id));
CREATE INDEX IF NOT EXISTS phoenix_trial_fixture_key ON phoenix_fixtures(feed,competition,season,home,away);
CREATE VIEW IF NOT EXISTS phoenix_trial_league_games AS
  SELECT competition,season,home,away,count(*) AS provider_records,
    count(DISTINCT CASE WHEN score_home IS NOT NULL AND score_away IS NOT NULL
      THEN printf('%d:%d',score_home,score_away) END) AS distinct_scores,
    CASE WHEN min(score_home)=max(score_home) AND min(score_away)=max(score_away) THEN min(score_home) END AS h90,
    CASE WHEN min(score_home)=max(score_home) AND min(score_away)=max(score_away) THEN min(score_away) END AS a90
  FROM phoenix_fixtures WHERE feed='trial' AND season IS NOT NULL
    AND competition IN ('eng.1','esp.1','ger.1','ita.1','fra.1') GROUP BY competition,season,home,away;
CREATE VIEW IF NOT EXISTS phoenix_archive_trial_links AS
  SELECT a.fixture_id AS archive_id,t.fixture_id AS trial_id,a.competition,
    CASE WHEN a.score_home=t.score_home AND a.score_away=t.score_away THEN 1
         WHEN a.score_home IS NOT NULL AND t.score_home IS NOT NULL THEN 0 END AS score_agrees
  FROM phoenix_fixtures a JOIN phoenix_fixtures t
    ON a.competition=t.competition AND a.home=t.home AND a.away=t.away
    AND a.kickoff_source=replace(substr(t.kickoff_source,1,16),'T',' ')
    AND substr(t.kickoff_source,-6)='+08:00'
  WHERE a.feed='archive' AND t.feed='trial';
CREATE VIEW IF NOT EXISTS phoenix_archive_prematch_1x2 AS
  SELECT p.*,f.competition,f.kickoff_source,f.home AS home_team,f.away AS away_team,
         f.score_home,f.score_away
  FROM phoenix_price_points p JOIN phoenix_fixtures f USING(feed,fixture_id)
  JOIN phoenix_detail_versions d USING(feed,fixture_id,source_id)
  WHERE p.feed='archive' AND p.valid_price=1 AND p.seconds_to_kickoff>0;
"""


def compact(value):
    return json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False)


def wall_seconds(kick, quote):
    """Nearest calendar occurrence to kickoff, in the archive's display clock. Never roll a late quote back a year."""
    if not kick or not quote:return None
    try:
        ko=datetime.strptime(kick,'%Y-%m-%d %H:%M')
        candidates=[]
        for year in (ko.year-1,ko.year,ko.year+1):
            try:candidates.append(datetime.strptime(f'{year}-{quote}','%Y-%m-%d %H:%M'))
            except ValueError:continue
        at=min(candidates,key=lambda d:abs(ko-d))
        return int((ko-at).total_seconds())
    except (ValueError,TypeError):
        return None


def received(db,root,rec):
    url=rec['url']
    if urlparse(url).scheme!='https' or urlparse(url).hostname!=ORIGIN:
        raise ValueError('source is not the official provider')
    path=(root/rec['file']).resolve()
    if not path.is_relative_to(root.resolve()):raise ValueError('invalid relative source path')
    data=path.read_bytes()
    if path.suffix=='.gz':data=gzip.decompress(data)
    sha=hashlib.sha256(data).hexdigest()
    if sha!=rec['sha256']:raise ValueError('source payload checksum changed: '+path.name)
    at=rec['observed_at']
    if datetime.fromisoformat(at.replace('Z','+00:00')).tzinfo is None:raise ValueError('observation must be timezone-aware')
    sid=hashlib.sha256(compact([PROVIDER,url,sha]).encode()).hexdigest()
    db.execute('INSERT OR IGNORE INTO sources(source_id,path,url,sha256,observed_at,imported_at,provider) VALUES(?,?,?,?,?,?,?)',
               (sid,str(path),url,sha,at,datetime.now(timezone.utc).isoformat(),PROVIDER))
    return sid,json.loads(data)


def catalog(db,scope,listed,counts):
    sid,leagues=listed
    cov,coverage=counts
    by_code={x['code']:x for x in leagues['data']}
    by_stats={x['league_code']:x for x in coverage['data']['leagues']}
    db.executemany('INSERT OR IGNORE INTO phoenix_bookmakers VALUES(?,?,?,?,?)',
        [(cov,b['code'],b.get('name_cn'),b.get('kind'),compact(b)) for b in coverage['data']['bookmakers']])
    reverse={v:k for k,v in SCOPES.items()}
    for competition in scope:
        code=reverse.get(competition)
        item=by_code.get(code,{})
        stats=by_stats.get(code,{})
        if code and not item:raise ValueError('provider directory changed: '+code)
        db.execute('INSERT OR IGNORE INTO phoenix_league_coverage VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (sid,cov,competition,code,item.get('name_cn'),stats.get('fixtures'),stats.get('seasons'),
             item.get('first_fixture_at'),stats.get('odds_rows'),compact(item),compact(stats)))


def fixture(db,feed,mid,competition,source,stamp,raw,*,season,kick,home,away,h,a,basis,status,odds):
    if not mid or not home or not away or home==away:raise ValueError('missing fixture identity')
    previous=db.execute('SELECT competition FROM phoenix_fixtures WHERE feed=? AND fixture_id=?',(feed,mid)).fetchone()
    if previous and previous[0]!=competition:raise ValueError('provider fixture changed competition: '+mid)
    db.execute('INSERT OR IGNORE INTO phoenix_fixture_evidence VALUES(?,?,?,?)',(feed,mid,source,compact(raw)))
    db.execute("""INSERT INTO phoenix_fixtures VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(feed,fixture_id) DO UPDATE SET season=excluded.season,kickoff_source=excluded.kickoff_source,
        home=excluded.home,away=excluded.away,score_home=excluded.score_home,score_away=excluded.score_away,
        score_basis=excluded.score_basis,status=excluded.status,price_rows_advertised=excluded.price_rows_advertised,
        source_id=excluded.source_id,observed_at=excluded.observed_at WHERE excluded.observed_at>phoenix_fixtures.observed_at""",
        (feed,mid,competition,season,kick,home,away,h,a,basis,status,odds,source,stamp))


def archive_fixture(db,g,sid,at):
    code=ARCHIVE.get(g['league'])
    if not code:return
    score=re.fullmatch(r'(\d+)\s*[-:]\s*(\d+)',g.get('score') or '')
    h,a=map(int,score.groups()) if score else (None,None)
    fixture(db,'archive',str(g['id']),code,sid,at,g,season=None,kick=g['kick'],home=g['home'],away=g['away'],
        h=h,a=a,basis='domestic_top_flight_regular_league' if score else 'unresolved',
        status='finished' if score else 'unknown',odds=g.get('odds_rows'))


def trial_fixture(db,g,sid,at):
    code=SCOPES.get(g.get('league_code'))
    if not code:return
    home=g.get('home_team') or g.get('home_team_cn')
    away=g.get('away_team') or g.get('away_team_cn')
    h,a=g.get('home_goals'),g.get('away_goals')
    final=g.get('status')=='finished' and all(type(v)==int and v>=0 for v in (h,a))
    basis='domestic_top_flight_regular_league' if final and g['league_code'] in {'epl','esp','fra','ger','ita'} else 'provider_score_period_unconfirmed' if final else 'unsettled'
    fixture(db,'trial',str(g['fixture_id']),code,sid,at,g,season=g.get('season_code'),kick=g.get('kickoff_at'),
            home=home,away=away,h=h if final else None,a=a if final else None,basis=basis,
            status=g.get('status'),odds=g.get('bookmaker_count'))


def archive_detail(db,g,sid,at):
    mid=str(g['id'])
    row=db.execute("""SELECT competition,kickoff_source,home,away,price_rows_advertised
                   FROM phoenix_fixtures WHERE feed='archive' AND fixture_id=?""",(mid,)).fetchone()
    if row is None or tuple(row[:4])!=(ARCHIVE.get(g['league']),g['kick'],g['home'],g['away']):
        raise ValueError('detail does not match the archived fixture: '+mid)
    points=[]
    for book in g['bookmakers']:
        track=book['track']
        if book['nodes']!=len(track):raise ValueError('unexpected price point count: '+mid)
        for seq,item in enumerate(track):
            prices=[item.get(k) for k in ('h','d','a')]
            valid=all(type(v) in (int,float) and math.isfinite(v) and v>1 for v in prices)
            t=item.get('t')
            points.append((sid,'archive',mid,book['code'],seq,'1x2',t,wall_seconds(g['kick'],t),
                           'provider_display_clock_nearest_year_no_zone',*prices,int(valid)))
    if len(points)!=row[4]:raise ValueError('detail and directory count disagree: '+mid)
    db.executemany('INSERT OR IGNORE INTO phoenix_price_points VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',points)
    db.execute("""INSERT INTO phoenix_detail_versions VALUES('archive',?,?,?)
               ON CONFLICT(feed,fixture_id) DO UPDATE SET source_id=excluded.source_id,observed_at=excluded.observed_at
               WHERE excluded.observed_at>phoenix_detail_versions.observed_at""",(mid,sid,at))


def trial_market(db,mid,endpoint,value,sid):
    if not value.get('ok'):raise ValueError('unsuccessful trial market response')
    market=value['data']
    fixture_row=db.execute("SELECT competition FROM phoenix_fixtures WHERE feed='trial' AND fixture_id=?",(mid,)).fetchone()
    if not fixture_row:raise ValueError('market has no in-scope fixture: '+mid)
    if isinstance(market,dict) and market.get('fixture_id') is not None and str(market['fixture_id'])!=mid:
        raise ValueError('wrong trial market fixture: '+mid)
    db.execute('INSERT OR IGNORE INTO phoenix_market_evidence VALUES(?,?,?,?,?)',
               (sid,'trial',mid,endpoint,compact(market)))
    if not isinstance(market,dict):return
    if SCOPES.get(market.get('league_code'))!=fixture_row[0]:
        raise ValueError('trial market and directory competitions differ: '+mid)
    values=[]
    def add(name,b,phase,i,time,line,prices,raw):
        if any(p is not None for p in prices):
            values.append((sid,mid,name,b,phase,i,time,line,*prices,compact(raw)))
    for name in ('odds_1x2','asian_handicap','exchange'):
        for i,book in enumerate(market.get(name) or []):
            for phase in ('open','close'):
                if name=='odds_1x2':
                    v=book.get(phase) or {}
                    prices=[v.get(k) for k in ('home','draw','away')]
                elif name=='asian_handicap':
                    prices=[book.get(phase+'_home'),None,book.get(phase+'_away')]
                else:
                    prices=[book.get(phase+'_'+k) for k in ('h','d','a')]
                add(name,book['code'],phase,i,book.get(phase+'_at'),book.get('line_'+phase),prices,book)
    for i,tick in enumerate(market.get('odds_ticks') or []):
        phase='inplay' if tick.get('is_inplay') is True else 'prematch' if tick.get('is_inplay') is False else 'unknown'
        add('tick_1x2',tick['code'],phase,i,tick.get('changed_at'),None,
            [tick.get(k) for k in ('eu_home','eu_draw','eu_away')],tick)
        add('tick_handicap_provider_water',tick['code'],phase,i,tick.get('changed_at'),tick.get('asia_line'),
            [tick.get('asia_home_w'),None,tick.get('asia_away_w')],tick)
    for i,live in enumerate(market.get('odds_live') or []):
        eu=live.get('eu') or {};asian=live.get('asian') or {}
        add('latest_1x2',live['code'],'latest',i,live.get('captured_at'),None,
            [eu.get(k) for k in ('home','draw','away')],live)
        add('latest_handicap_provider_price',live['code'],'latest',i,live.get('captured_at'),asian.get('line'),
            [asian.get('home'),None,asian.get('away')],live)
    for i,book in enumerate(market.get('books') or []):
        for minute,slot in (book.get('at') or {}).items():
            if not isinstance(slot,dict):continue
            eu=slot.get('eu')
            if eu and len(eu)==3:
                add('snapshot_1x2',book['bookmaker_code'],'T-'+minute,i,slot.get('changed_at'),None,eu,slot)
    db.executemany('INSERT OR IGNORE INTO phoenix_trial_prices VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',values)


def capture_manifest(root,path):
    """Convert the collector's checksummed receipts, never its request headers, to one replay list."""
    source=json.loads((root/'official/probe-manifest.json').read_bytes())
    records=[]
    for name,kind in (('leagues','catalog'),('coverage','coverage')):
        item=source[name]
        records.append(dict(kind=kind,url=item['url'],file='official/'+item['file'],
                            observed_at=item['observed_at'],sha256=item['sha256']))
    for folder,filename,kind in (
            ('archive','list-pages.jsonl','archive-list'),('trial','fixture-list-manifest.json','trial-list'),
            ('archive','details.jsonl','archive-detail'),('trial','market-response-manifest.json','trial-market')):
        manifest=root/folder/filename
        if not manifest.exists():continue
        lines=manifest.read_bytes()
        items=map(json.loads,lines.splitlines()) if filename.endswith('jsonl') else json.loads(lines)
        for item in items:
            if item['http_status']!=200 or not item.get('file'):continue
            if kind=='trial-list' and item.get('league') not in SCOPES:continue
            record=dict(kind=kind,url=item['url'],file=folder+'/'+item['file'],
                        observed_at=item['fetched_at_utc'],sha256=item['payload_sha256'])
            if kind=='trial-market':
                record.update(fixture_id=item['fixture_id'],endpoint=urlparse(item['url']).path.split('/')[-1])
            records.append(record)
    review=root/'review/database-receipts.jsonl'
    if review.exists():
        for item in map(json.loads,review.read_bytes().splitlines()):
            if item.get('http_status')!=200 or item.get('kind') not in ('review-list','review-detail'):continue
            rec=dict(kind=item['kind'],url=item['url'],file='review/'+item['file'],observed_at=item['fetched_at_utc'],
                     sha256=item['payload_sha256'])
            if item['kind']=='review-detail':rec['fixture_id']=item['fixture_id']
            records.append(rec)
    tmp=path.with_suffix('.tmp')
    with tmp.open('w',encoding='utf8') as f:
        for record in records:f.write(compact(record)+'\n')
    tmp.replace(path)


def import_receipts(database,root,manifest,scope):
    items=[json.loads(line) for line in manifest.open() if line.strip()]
    with closing(sqlite3.connect(database,timeout=45)) as db:
        db.execute('PRAGMA foreign_keys=ON')
        db.executescript(SCHEMA+phoenix_identity.SCHEMA)
        with db:
            catalog_data={}
            for rec in items:
                if rec['kind'] in ('catalog','coverage'):
                    catalog_data[rec['kind']]=received(db,root,rec)
            if catalog_data:catalog(db,scope,catalog_data['catalog'],catalog_data['coverage'])
        for rec in items:
            kind=rec['kind']
            if kind in ('catalog','coverage'):continue
            with db:
                sid,value=received(db,root,rec)
                at=rec['observed_at']
                if kind=='archive-list':
                    for g in value['rows']:archive_fixture(db,g,sid,at)
                elif kind=='archive-detail':archive_detail(db,value,sid,at)
                elif kind=='trial-list':
                    if not value.get('ok') or not isinstance(value['data'],list):raise ValueError('trial list response schema')
                    for g in value['data']:trial_fixture(db,g,sid,at)
                elif kind=='review-list':
                    for g in value['rows']:
                        code=REVIEW.get(g.get('competition'))
                        if not code:continue
                        vals=[g.get(k) for k in ('open_home','open_draw','open_away','close_home','close_draw','close_away',
                                                 'open_ah_line','close_ah_line','open_bs_line','close_bs_line')]
                        db.execute('INSERT OR IGNORE INTO phoenix_review_evidence VALUES('+','.join('?'*19)+')',
                            (sid,str(g['prediction_id']),str(g['match_id']),code,g['kickoff'],g['home_team'],g['away_team'],
                             g.get('base_code') or g.get('base_company_id'),*vals,compact(g)))
                elif kind=='review-detail':
                    mid=str(rec['fixture_id'])
                    if db.execute('SELECT 1 FROM phoenix_review_evidence WHERE fixture_id=? LIMIT 1',(mid,)).fetchone():
                        db.execute('INSERT OR IGNORE INTO phoenix_market_evidence VALUES(?,?,?,?,?)',
                                   (sid,'publisher_review',mid,'published_match',compact(value)))
                elif kind=='trial-market':
                    mid=str(rec['fixture_id'])
                    trial_market(db,mid,rec['endpoint'],value,sid)
                else:raise ValueError('unknown Phoenix evidence kind '+kind)
        phoenix_identity.rebuild(db)


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--manifest',type=Path,help='Replay a previously frozen capture manifest.')
    for field in ('db','root','scope'):ap.add_argument('--'+field,required=True,type=Path)
    a=ap.parse_args()
    scope=[c['code'] for c in json.loads(a.scope.read_bytes())['competitions']]
    manifest=a.manifest
    if manifest is None:
        manifest=a.root/'database-manifest.jsonl'
        capture_manifest(a.root,manifest)
    if not a.db.exists():
        from store import Store
        initial=Store(a.db)
        initial.db.commit()
        initial.db.close()
    import_receipts(a.db,a.root,manifest,scope)
