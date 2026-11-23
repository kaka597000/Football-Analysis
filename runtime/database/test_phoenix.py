from pathlib import Path
import tempfile,unittest
import phoenix_identity
from store import Store
from phoenix import SCHEMA,archive_detail,archive_fixture,trial_fixture,trial_market,wall_seconds


class PhoenixTests(unittest.TestCase):
    def test_december_quote_and_after_kickoff_are_distinct(self):
        self.assertEqual(wall_seconds('2025-01-01 01:00','12-31 23:00'),7200)
        self.assertEqual(wall_seconds('2025-01-01 01:00','01-01 01:04'),-240)
        self.assertIsNone(wall_seconds('2025-01-01 01:00','bad'))

    def test_source_snapshots_do_not_double_count_tracks_or_conflate_league_ids(self):
        with tempfile.TemporaryDirectory() as t:
            store=Store(Path(t)/'db');db=store.db;db.executescript(SCHEMA)
            stamp='2026-11-23T00:00:00+00:00'
            for s in ('old','new'):
                db.execute('insert into sources(source_id,imported_at) values(?,?)',(s,stamp))
            g=dict(id=10,kick='2025-01-01 01:00',score='1:0',home='H',away='A',league='英格兰超级联赛',odds_rows=2)
            archive_fixture(db,g,'old',stamp)
            book=dict(code='bk001',nodes=2,track=[dict(t='12-31 23:00',h=2,d=3,a=4),dict(t='01-01 01:01',h=2,d=3,a=4)])
            data=dict(g,bookmakers=[book])
            archive_detail(db,data,'old',stamp)
            archive_detail(db,data,'old',stamp)
            archive_detail(db,data,'new','2026-11-23T01:00:00+00:00')
            self.assertEqual(db.execute('select count(*) from phoenix_archive_prematch_1x2').fetchone()[0],1)
            with self.assertRaises(ValueError):archive_detail(db,dict(data,away='wrong'),'old',stamp)
            tr=dict(fixture_id=10,league_code='epl',home_team='H',away_team='A',status='finished',home_goals=1,away_goals=0,season_code='2425',kickoff_at='2025-01-01T01:00:00+08:00')
            trial_fixture(db,tr,'old',stamp)
            trial_fixture(db,dict(tr,fixture_id=12,home_goals=3),'new',stamp)
            self.assertEqual(db.execute('select distinct_scores,h90 from phoenix_trial_league_games').fetchone()[:],(2,None))
            self.assertEqual(db.execute('select count(*) from phoenix_fixtures').fetchone()[0],3)
            with self.assertRaises(ValueError):trial_market(db,'10','all',{'ok':True,'data':{'fixture_id':20}},'new')
            db.executescript(phoenix_identity.SCHEMA)
            for i,opponent in enumerate(('X','Y','Z'),1):
                k=f'2025-04-0{i}T18:30:00+08:00'
                for source,team,mid in [('history','Team',20+i),('main','Alias',30+i)]:
                    tr2=dict(tr,source=source,fixture_id=mid,home_team=team,away_team=opponent,kickoff_at=k)
                    trial_fixture(db,tr2,'new',stamp)
            phoenix_identity.rebuild(db)
            proof=db.execute("SELECT alias,canonical,dates FROM phoenix_team_alias_evidence").fetchone()
            self.assertEqual(proof[:],('Alias','Team',3))
            self.assertEqual(db.execute("SELECT count(*) FROM phoenix_backtest_games WHERE home_team='Team'").fetchone()[0],3)
            db.close()


if __name__=='__main__':unittest.main()
