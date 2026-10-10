import json
import tempfile
import unittest
from pathlib import Path
from providers import espn, football_data, period_score
from registry import season_for
from store import Store

class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name)/'test.sqlite')
        self.addCleanup(self.store.db.close)
        self.p=Path(self.tmp.name)/'source.json';self.p.write_text('{}')
        self.sid=self.store.source(self.p,'test',observed='2026-10-01T00:00:00+00:00')
        self.h=self.store.team('ENG','Home',1);self.a=self.store.team('ENG','Away',2)

    def row(self,**overrides):
        d=dict(source_key='event:1',competition='eng.1',season='2026/27',date='2026-09-01',home_id=self.h,away_id=self.a,orientation='home_away',status='completed',h90=2,a90=1,quality='single_source',score_scope='regulation_90',provider='test',observed_at='2026-10-01T00:00:00+00:00')
        d.update(overrides);return d

    def test_idempotence_and_correction(self):
        self.store.add(self.sid,self.row());self.store.add(self.sid,self.row());self.store.project()
        self.assertEqual(self.store.audit()['observations'],1)
        self.store.add(self.sid,self.row(h90=3,observed_at='2026-10-02T00:00:00+00:00'));self.store.project()
        self.assertEqual(self.store.audit()['observations'],2)
        self.assertEqual(self.store.db.execute('select h90 from training_90').fetchone()[0],3)

    def test_conflicting_sources_block_training(self):
        self.store.add(self.sid,self.row());self.store.add(self.sid,self.row(h90=0,provider='other'));self.store.project()
        self.assertEqual(self.store.audit()['training_90'],0)
        self.assertEqual(self.store.audit()['results']['conflict'],1)

    def test_missing_and_unresolved_results_not_zero(self):
        self.store.add(self.sid,self.row(h90=None,a90=None,status='scheduled',score_scope='unknown'))
        self.store.project();r=self.store.db.execute('select * from matches').fetchone()
        self.assertIsNone(r['h90']);self.assertEqual(r['training_eligible'],0)

    def test_listed_jfa_order_is_not_home_advantage(self):
        self.store.add(self.sid,self.row(orientation='listed_first_second'));self.store.project()
        self.assertEqual(self.store.audit()['training_90'],0)
        self.assertEqual(self.store.audit()['results']['accepted'],1)

    def test_alias_does_not_merge_conflicting_provider_ids(self):
        self.store.team('ENG','Home',3)
        self.assertNotEqual(self.store.resolve('espn:1'),self.store.resolve('espn:3'))
        self.assertIn('ALIAS_COLLISION',self.store.audit()['issues'])

    def test_two_fixture_bridge_requires_multiple_dates(self):
        legacy=self.store.team('ENG','Home abbreviated')
        for date in ['2026-09-01','2026-09-08']:
            self.store.add(self.sid,self.row(date=date,source_key='a:'+date,provider='espn'))
            self.store.add(self.sid,self.row(date=date,home_id=legacy,source_key='b:'+date,provider='other'))
        self.store.bridge();self.assertEqual(self.store.resolve(legacy),self.h)
        self.store.project();self.assertEqual(self.store.audit()['matches'],2)

    def test_extra_time_score_not_mistaken_for_90(self):
        data={'events':[{'id':'1','date':'2026-09-01T18:00Z','competitions':[{'status':{'period':4,'type':{'completed':True,'name':'STATUS_END_OF_EXTRATIME'}},'competitors':[{'homeAway':side,'team':{'id':i,'displayName':name},'score':'3'} for side,i,name in [('home','1','Home'),('away','2','Away')]]}]}]}
        espn(self.store,data,self.sid,'eng.1','2026-10-01T00:00:00+00:00');self.store.project()
        self.assertEqual(self.store.audit()['training_90'],0)
        self.assertIn('REGULATION_UNRESOLVED',self.store.audit()['issues'])

    def test_season_mapping(self):
        self.assertEqual(season_for('bra.1','2026-09-13','2026/27'),'2026')
        self.assertEqual(season_for('eng.1','2025-09-13','2526'),'2025/26')
        self.assertEqual(season_for('uefa.nations','2024-03-21','2022/23'),'2022/23')
        self.assertEqual(season_for('uefa.nations','2026-03-21','2024/25'),'2024/25')
        self.assertEqual(season_for('jpn.1','2026-10-09'),'2026/27')
        self.assertEqual(season_for('jpn.2','2025-10-09'),'2025')

    def test_unchanged_refetch_does_not_add_observations(self):
        self.store.add(self.sid,self.row())
        self.store.add(self.sid,self.row(observed_at='2026-10-02T00:00:00+00:00'))
        self.assertEqual(self.store.audit()['observations'],1)

    def test_legacy_result_resolution_requires_both_feeds(self):
        self.store.add(self.sid,self.row(provider='canonical',h90=0))
        self.store.add(self.sid,self.row(provider='espn'))
        self.store.project();self.assertEqual(self.store.audit()['training_90'],0)
        self.store.add(self.sid,self.row(provider='football-data'))
        self.store.project();self.assertEqual(self.store.audit()['training_90'],1)
        self.assertEqual(self.store.db.execute('select result_status from matches').fetchone()[0],'resolved_corroborated')

    def test_ambiguous_name_requires_identity(self):
        self.store.team('ENG','Home',3)
        tid=self.store.team('ENG','Home')
        self.assertTrue(tid.startswith('unresolved-name:'))

    def test_scheduled_refresh_clock_uses_beijing(self):
        from scheduler import next_run,BJT
        import datetime as dt
        start=dt.datetime(2026,10,10,18,1,tzinfo=BJT)
        self.assertEqual(next_run(start).isoformat(),'2026-10-11T04:00:00+08:00')

    def test_reparse_preserves_time_but_corrects_stage(self):
        self.store.add(self.sid,self.row())
        self.store.add(self.sid,self.row(stage='promotion_playoff'))
        self.store.project()
        self.assertEqual(self.store.db.execute('select stage from matches').fetchone()[0],'promotion_playoff')

    def test_penalty_display_not_regulation(self):
        url='https://example.test/match/'
        text=f'<link rel="canonical" href="{url}"><input value="1" id="isFinished"><p id="js-score">1:2</p><h2 id="js-eventstage">After Penalties</h2><div id="js-partial">(1:0, 0:1, 0:0, 2:4)</div>'
        r=period_score(text,url)
        self.assertEqual((r['h90'],r['a90']),(1,1));self.assertEqual((r['pen_h'],r['pen_a']),(2,4))
        with self.assertRaises(ValueError):period_score(text,'https://example.test/other/')

    def test_aet_period_conservation(self):
        url='https://example.test/match/'
        text=f'<link rel="canonical" href="{url}"><input value="1" id="isFinished"><p id="js-score">3:1</p><h2 id="js-eventstage">After Extra Time</h2><div id="js-partial">(0:1, 1:0, 2:0)</div>'
        r=period_score(text,url);self.assertEqual((r['h90'],r['a90']),(1,1))
        with self.assertRaises(ValueError):period_score(text.replace('3:1','4:1'),url)

    def test_official_final_overrides_legacy_without_erasing_it(self):
        self.store.add(self.sid,self.row(provider='canonical',h90=0))
        self.store.add(self.sid,self.row(provider='kleague',quality='official'))
        self.store.project();self.assertEqual(self.store.audit()['observations'],2)
        self.assertEqual(self.store.db.execute('select result_status from matches').fetchone()[0],'resolved_official')

    def test_two_leg_penalties_do_not_require_single_match_draw(self):
        url='https://example.test/match/'
        text=f'<link rel="canonical" href="{url}"><input value="1" id="isFinished"><p id="js-score">1:3</p><h2 id="js-eventstage">After Penalties</h2><div id="js-partial">(0:1, 0:0, 1:1, 1:3)</div>'
        with self.assertRaises(ValueError):period_score(text,url)
        r=period_score(text,url,second_leg=True);self.assertEqual((r['h90'],r['a90']),(0,1))

if __name__=='__main__':unittest.main()
