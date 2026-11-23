"""Resolve duplicate domestic league fixtures using independent naming streams and the pre-match schedule."""
from collections import defaultdict
from hashlib import sha256
from itertools import combinations
import json
import re

SCHEMA="""
CREATE TABLE IF NOT EXISTS phoenix_team_alias_evidence(
  competition TEXT,season TEXT,alias TEXT,canonical TEXT,dates INTEGER,opponents INTEGER,examples_json TEXT,
  PRIMARY KEY(competition,season,alias));
CREATE TABLE IF NOT EXISTS phoenix_fixture_identity(
  feed TEXT,fixture_id TEXT,canonical_id TEXT,competition TEXT,season TEXT,home_team TEXT,away_team TEXT,
  PRIMARY KEY(feed,fixture_id));
CREATE INDEX IF NOT EXISTS phoenix_identity_lookup ON phoenix_fixture_identity(canonical_id);
CREATE TABLE IF NOT EXISTS phoenix_fixture_flags(
  feed TEXT, fixture_id TEXT, reason TEXT, PRIMARY KEY(feed,fixture_id,reason));
CREATE VIEW IF NOT EXISTS phoenix_backtest_games AS
  SELECT i.canonical_id,i.competition,i.season,i.home_team,i.away_team,COUNT(*) AS provider_rows,
    COUNT(DISTINCT CASE WHEN f.score_home IS NOT NULL AND f.score_away IS NOT NULL
      THEN printf('%d:%d',f.score_home,f.score_away) END) AS distinct_scores,
    CASE WHEN min(f.score_home)=max(f.score_home) AND min(f.score_away)=max(f.score_away)
         THEN min(f.score_home) END AS h90,
    CASE WHEN min(f.score_home)=max(f.score_home) AND min(f.score_away)=max(f.score_away)
         THEN min(f.score_away) END AS a90
  FROM phoenix_fixture_identity i JOIN phoenix_fixtures f USING(feed,fixture_id) GROUP BY i.canonical_id;
CREATE VIEW IF NOT EXISTS phoenix_verified_backtest_games AS
  SELECT b.* FROM phoenix_backtest_games b WHERE b.distinct_scores=1
    AND EXISTS(SELECT 1 FROM phoenix_fixture_identity i WHERE i.canonical_id=b.canonical_id AND i.feed='trial')
    AND NOT EXISTS(SELECT 1 FROM phoenix_fixture_identity i JOIN phoenix_fixture_flags v USING(feed,fixture_id)
                   WHERE i.canonical_id=b.canonical_id);
"""


def rebuild(db):
    # Scope-specific season keys only apply to these home-and-away top-flight competitions.
    fixtures=db.execute("""SELECT f.feed,f.fixture_id,f.competition,f.season,f.kickoff_source,f.home,f.away,
      CASE WHEN f.feed='archive' THEN 'archive' ELSE json_extract(e.payload_json,'$.source') END AS stream
      FROM phoenix_fixtures f JOIN phoenix_fixture_evidence e USING(feed,fixture_id,source_id)
      WHERE f.competition IN ('eng.1','esp.1','ger.1','ita.1','fra.1') ORDER BY 3,5,1,2""").fetchall()
    by_opponent=defaultdict(list)
    records=[]
    preference={}
    for feed,mid,comp,season,kick,home,away,stream in fixtures:
        if not kick or not home or not away:continue
        if feed=='archive':
            year=int(kick[:4])-(int(kick[5:7])<7)
            season=f'{year%100:02}{(year+1)%100:02}'
        if not season:continue
        clock=kick[:16].replace('T',' ') if feed=='archive' or kick.endswith('+08:00') else None
        rank={'history':0,'main':1,'archive':2}.get(stream,3)
        for name in (home,away):
            key=(comp,season,name)
            preference[key]=min(preference.get(key,3),rank)
        records.append((feed,mid,comp,season,home,away))
        if clock:
            for side,known,candidate in [('home',home,away),('away',away,home)]:
                by_opponent[comp,season,clock,side,known].append((stream,candidate))
    votes=defaultdict(set)
    for (comp,season,clock,side,opponent),group in by_opponent.items():
        for (stream,a),(other,b) in combinations(set(group),2):
            if stream!=other and a!=b:votes[(comp,season,*sorted((a,b)))].add((clock,opponent))
    verified={}
    links=defaultdict(set)
    for (comp,season,a,b),hits in votes.items():
        if len({day[:10] for day,_ in hits})>=3 and len({opp for _,opp in hits})>=2:
            key=(comp,season,a,b);verified[key]=hits
            links[comp,season,a].add(b);links[comp,season,b].add(a)
    aliases={}
    evidence=[]
    for (comp,season,a,b),hits in verified.items():
        if len(links[comp,season,a])!=1 or len(links[comp,season,b])!=1:continue
        canonical,alias=sorted((a,b),key=lambda t:(preference[comp,season,t],t))
        aliases[comp,season,alias]=canonical
        evidence.append((comp,season,alias,canonical,len({d[:10] for d,_ in hits}),
                         len({o for _,o in hits}),json.dumps(sorted(hits)[:5],ensure_ascii=False)))
    identities=[]
    for feed,mid,comp,season,home,away in records:
        h=aliases.get((comp,season,home),home);a=aliases.get((comp,season,away),away)
        if h==a:raise ValueError('alias collapsed opposing teams: '+mid)
        token=json.dumps([comp,season,h,a],ensure_ascii=False,separators=(',',':')).encode()
        identities.append((feed,mid,sha256(token).hexdigest(),comp,season,h,a))
    supported={row[2] for row in identities if row[0]=='trial'}
    flags=[]
    for feed,mid,key,comp,season,home,away in identities:
        if feed=='archive' and key not in supported:
            flags.append((feed,mid,'no_matching_trial_fixture'))
        if re.search(r'女足|女子|青年队|预备队|B队|U[- ]?(?:17|18|19|20|21|23)\b',home+' '+away,re.I):
            flags.append((feed,mid,'team_level_requires_verification'))
    with db:
        db.execute('DELETE FROM phoenix_fixture_flags')
        db.executemany('INSERT INTO phoenix_fixture_flags VALUES(?,?,?)',flags)
        db.execute('DELETE FROM phoenix_team_alias_evidence')
        db.execute('DELETE FROM phoenix_fixture_identity')
        db.executemany('INSERT INTO phoenix_team_alias_evidence VALUES(?,?,?,?,?,?,?)',evidence)
        db.executemany('INSERT INTO phoenix_fixture_identity VALUES(?,?,?,?,?,?,?)',identities)
