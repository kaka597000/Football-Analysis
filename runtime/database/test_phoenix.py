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

    def test_resume_reuses_committed_receipts_and_stops_on_final_quota(self):
        import json
        from phoenix_resume import resume
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'trial').mkdir()
            queue=[dict(fixture_id=i,provider_league_code='epl',status='finished') for i in (1,2)]
            (root/'trial/remaining-market-fixture-queue.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in queue))
            db=Store(root/'db').db
            db.executescript(SCHEMA)
            stamp='2026-11-22T20:00:00+08:00'
            db.execute('insert into sources(source_id,imported_at) values(?,?)',('src',stamp))
            for i in (1,2):
                trial_fixture(db,dict(fixture_id=i,league_code='epl',home_team='H',away_team='A',
                    kickoff_at=stamp,status='finished',season_code='2627'),'src',stamp)
            calls=[]
            def fetch(path):
                calls.append(path)
                if path=='/api/me':
                    return 200,b'{"data":{"leagues":["epl"],"rps":5}}','1'
                body=json.dumps({'ok':True,'data':{'fixture_id':int(path.split('/')[3]),'league_code':'epl'}})
                return 200,body.encode(),'0'
            for i in (1,2):
                self.assertEqual(resume(root,db,10,fetch,lambda _:None)['added'],1)
                self.assertTrue((root/f'trial/resumed/receipts/{i}.json').exists())
            self.assertEqual(resume(root,db,10,fetch,lambda _:None)['added'],0)
            self.assertEqual(calls,['/api/me','/api/matches/1/all?tick_limit=100',
                                    '/api/me','/api/matches/2/all?tick_limit=100'])
            db.close()

    def test_snapshot_clock_and_separate_database_scope(self):
        from scope_registry import competition,season_for
        import registry
        self.assertNotIn('uefa.europa',registry.LEAGUES)
        self.assertEqual(competition('UEFA Europa Conference League'),'uefa.europa.conf')
        self.assertEqual(season_for('uefa.europa','2026-11-01'),'2026/27')
        self.assertEqual(season_for('fifa.world','2026-06-20'),'2026')
        with tempfile.TemporaryDirectory() as temp:
            store=Store(Path(temp)/'db')
            db=store.db
            db.executescript(SCHEMA)
            kick='2026-11-22T20:00:00+08:00'
            db.execute('insert into sources(source_id,imported_at) values(?,?)',('src',kick))
            trial_fixture(db,dict(fixture_id=99,league_code='epl',home_team='H',away_team='A',
                kickoff_at=kick,status='scheduled',season_code='2627'),'src',kick)
            data={'fixture_id':99,'league_code':'epl','odds_ticks':[
                {'code':'b','changed_at':'2026-11-22T20:01:00+08:00','is_inplay':False,'eu_home':2}],
                'books':[{'bookmaker_code':'b','at':{
                    '60':{'eu':[2,3,4],'asia':[0,.91,.92],'changed_at':'2026-11-22T18:59:00+08:00'},
                    '30':{'eu':[2,3,4],'changed_at':'2026-11-22T19:31:00+08:00'}}}]}
            trial_market(db,'99','snapshot',{'ok':True,'data':data},'src')
            prices=db.execute('select market,phase from phoenix_trial_prematch_prices order by market').fetchall()
            self.assertEqual([tuple(v) for v in prices],
                [('snapshot_1x2','T-60'),('snapshot_handicap_provider_water','T-60')])
            db.close()

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
