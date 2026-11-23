"""Read back physical database counts and exercise a fixed pre-kickoff 1X2 benchmark."""
import argparse
from pathlib import Path
import json
import sqlite3

SQL="""
WITH ranked AS (
 SELECT feed,fixture_id,bookmaker,home,draw,away,
        ROW_NUMBER() OVER(PARTITION BY fixture_id,bookmaker ORDER BY seconds_to_kickoff,seq DESC) AS rn
 FROM phoenix_archive_prematch_1x2 WHERE seconds_to_kickoff>=?
), opinions AS (
 SELECT fixture_id, 1.0/home/(1.0/home+1.0/draw+1.0/away) AS ph,
  1.0/draw/(1.0/home+1.0/draw+1.0/away) AS pd, 1.0/away/(1.0/home+1.0/draw+1.0/away) AS pa
 FROM ranked WHERE rn=1
), match_probs AS (
 SELECT fixture_id,avg(ph) AS h,avg(pd) AS d,avg(pa) AS a FROM opinions GROUP BY fixture_id
)
SELECT f.competition,count(*) AS eligible,
 sum(CASE WHEN (CASE WHEN m.h>=m.d AND m.h>=m.a THEN 1 WHEN m.d>=m.a THEN 0 ELSE -1 END)=
  (CASE WHEN f.score_home>f.score_away THEN 1 WHEN f.score_home=f.score_away THEN 0 ELSE -1 END) THEN 1 ELSE 0 END) AS wdl_hits
FROM match_probs m JOIN phoenix_fixtures f ON f.fixture_id=m.fixture_id AND f.feed='archive'
JOIN phoenix_fixture_identity i ON i.feed=f.feed AND i.fixture_id=f.fixture_id
JOIN phoenix_verified_backtest_games vg ON vg.canonical_id=i.canonical_id
WHERE f.score_home=vg.h90 AND f.score_away=vg.a90 GROUP BY f.competition ORDER BY f.competition;
"""


def audit(path):
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)
    db.row_factory=sqlite3.Row
    def rows(query,params=()):return [dict(r) for r in db.execute(query,params)]
    result=dict(database=path.name,
        directory=rows('SELECT competition,provider_code,fixtures_advertised,seasons,first_fixture,prices_advertised FROM phoenix_league_coverage ORDER BY competition'),
        records=rows("""SELECT feed,competition,count(*) AS records,sum(score_home IS NOT NULL AND score_away IS NOT NULL) AS scored,
                    min(kickoff_source) AS earliest,max(kickoff_source) AS latest FROM phoenix_fixtures GROUP BY feed,competition ORDER BY feed,competition"""),
        deduplicated=rows("""SELECT competition,count(*) AS games,sum(provider_rows>1) AS multiple_records,
                  sum(provider_rows-1) AS duplicate_rows,sum(distinct_scores=1) AS settled,
                  sum(distinct_scores>1) AS score_conflicts FROM phoenix_backtest_games GROUP BY competition ORDER BY competition"""),
        training_eligible=rows('SELECT competition,count(*) AS games FROM phoenix_verified_backtest_games GROUP BY competition'),
        flagged=rows('SELECT feed,reason,count(*) AS records FROM phoenix_fixture_flags GROUP BY feed,reason'),
        alias_evidence=rows('SELECT competition,season,alias,canonical,dates,opponents FROM phoenix_team_alias_evidence ORDER BY 1,2,3'),
        direct_cross_checks=rows("""SELECT competition,count(*) AS links,count(DISTINCT archive_id) AS unique_archive,
                   sum(score_agrees=1) AS agreed,sum(score_agrees=0) AS disputed FROM phoenix_archive_trial_links GROUP BY competition"""),
        archive_prices=rows("""SELECT f.competition,count(*) AS price_nodes,count(DISTINCT p.fixture_id) AS matches,
               count(DISTINCT p.bookmaker) AS bookmakers,sum(valid_price=1) AS valid_decimal,
               sum(p.seconds_to_kickoff>0) AS before_kickoff FROM phoenix_price_points p JOIN phoenix_detail_versions v USING(feed,fixture_id,source_id)
               JOIN phoenix_fixtures f USING(feed,fixture_id) GROUP BY f.competition ORDER BY f.competition"""),
        trial_detail=rows("""SELECT endpoint,count(*) AS captures,count(DISTINCT fixture_id) AS matches
              FROM phoenix_market_evidence WHERE feed='trial' GROUP BY endpoint"""),
        trial_prices=rows("""SELECT market,phase,count(*) AS rows,count(DISTINCT fixture_id) AS matches,
              sum(source_time IS NOT NULL) AS source_timed FROM phoenix_trial_prices GROUP BY market,phase ORDER BY market,phase"""),
        price_replay={str(hours)+'h':rows(SQL,(3600*hours,)) for hours in (24,1)},
        replay_clock='Archive provider display-clock relative time. Only the most recent eligible quote per bookmaker is used. The year is nearest to kickoff. Publisher first-known times were not observed historically.',
        raw_sources=rows('''SELECT count(*) AS sources,count(DISTINCT sha256) AS unique_responses FROM sources WHERE source_id IN (
            SELECT catalog_source FROM phoenix_league_coverage UNION SELECT coverage_source FROM phoenix_league_coverage
            UNION SELECT source_id FROM phoenix_fixture_evidence UNION SELECT source_id FROM phoenix_price_points
            UNION SELECT source_id FROM phoenix_market_evidence UNION SELECT source_id FROM phoenix_review_evidence
            UNION SELECT source_id FROM phoenix_review_details)''')[0])
    if db.execute("select 1 from sqlite_master where name='phoenix_review_evidence'").fetchone():
        result['published_review']=rows("""SELECT competition,count(DISTINCT prediction_id) AS provider_reviews,
              count(DISTINCT review_match_id) AS matches,count(DISTINCT CASE WHEN open_total_line IS NOT NULL OR close_total_line IS NOT NULL THEN review_match_id END) AS with_totals_line
              FROM phoenix_review_evidence GROUP BY competition ORDER BY competition""")
    result['review_detail_matches']=db.execute('SELECT count(DISTINCT review_match_id) FROM phoenix_review_details').fetchone()[0]
    result['trial_price_timing']=rows("""SELECT p.market,count(*) AS observations,
        sum(julianday(p.source_time)<julianday(f.kickoff_source)) AS before_kickoff,
        sum(julianday(p.source_time)=julianday(f.kickoff_source)) AS at_kickoff,
        sum(julianday(p.source_time)>julianday(f.kickoff_source)) AS after_kickoff,
        sum(julianday(p.source_time) IS NULL) AS unknown
        FROM phoenix_trial_prices p JOIN phoenix_fixtures f ON f.feed='trial' AND p.fixture_id=f.fixture_id
        GROUP BY p.market ORDER BY p.market""")
    result['trial_eligible_quotes']=rows("""SELECT market,count(*) AS observations FROM phoenix_trial_prematch_prices
        GROUP BY market ORDER BY market""")
    result['trial_details_by_competition']=rows("""SELECT f.competition,m.endpoint,count(DISTINCT m.fixture_id) AS matches
        FROM phoenix_market_evidence m JOIN phoenix_fixtures f USING(feed,fixture_id)
        WHERE m.feed='trial' GROUP BY f.competition,m.endpoint ORDER BY f.competition,m.endpoint""")
    db.close()
    return result


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--db',type=Path,required=True);ap.add_argument('--out',type=Path,required=True)
    a=ap.parse_args();a.out.write_text(json.dumps(audit(a.db),indent=2,ensure_ascii=False)+'\n')
