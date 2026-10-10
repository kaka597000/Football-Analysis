import json
import tempfile
import unittest
from pathlib import Path
from store import Store
from five_dollar_history import ingest_page,bridge_provider_ids


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=Store(self.root/'db.sqlite');self.addCleanup(self.store.db.close)
        self.store.db.execute('CREATE TABLE provider_team_ids(provider TEXT,provider_id TEXT,team_id TEXT REFERENCES teams,source_id TEXT REFERENCES sources,evidence TEXT,PRIMARY KEY(provider,provider_id))')

    def capture(self,**overrides):
        row={'id':1,'league':{'id':686337048},'teams':{'home':{'id':11,'name':'Home'},'away':{'id':12,'name':'Away'}},
             'kickoff_utc':'2026-09-01T18:00:00+00:00','status':'finished','status_code':'full',
             'goals':{'home':2,'away':1},'statistics':{'shots_on_target':{'home':3,'away':2}},'events':[{'type':'goal'}]}
        row.update(overrides)
        p=self.root/'page.json';p.write_text(json.dumps({'http_status':200,'query_time':'2026-10-10T12:00:00+00:00',
            'url':'https://api.5dollarfootballapi.com/v1/leagues/686337048/fixtures','response':{'success':1,'data':[row]}}))
        return p

    def test_idempotent_import_preserves_raw_statistics_and_identity_mapping(self):
        h=self.store.team('GER','Home',100)
        p=self.capture();ingest_page(self.store,p,'ger.1',686337048);ingest_page(self.store,p,'ger.1',686337048)
        self.store.project();self.assertEqual(self.store.audit()['observations'],1)
        r=self.store.db.execute('SELECT * FROM observations').fetchone()
        self.assertEqual(r['home_id'],h);self.assertEqual(r['h90'],2);self.assertIsNone(r['first_known_at'])
        self.assertIn('statistics',json.loads(r['raw_json']))

    def test_enrichment_survives_unchanged_score_observation(self):
        p=self.capture();cap=json.loads(p.read_text());cap['response']['data'][0].pop('statistics');p.write_text(json.dumps(cap))
        ingest_page(self.store,p,'ger.1',686337048)
        p=self.capture();cap=json.loads(p.read_text());cap['query_time']='2026-10-10T13:00:00+00:00';p.write_text(json.dumps(cap))
        ingest_page(self.store,p,'ger.1',686337048)
        self.assertEqual(self.store.audit()['observations'],1)
        raws=[json.loads(r[0]) for r in self.store.db.execute('SELECT raw_json FROM api_fixture_payloads')]
        self.assertEqual(len(raws),2);self.assertTrue(any('statistics' in r for r in raws))

    def test_unknown_state_not_settled_even_if_feed_has_goals(self):
        p=self.capture(status='unknown');ingest_page(self.store,p,'ger.1',686337048);self.store.project()
        self.assertEqual(self.store.audit()['training_90'],0)
        self.assertIsNone(self.store.db.execute('SELECT h90 FROM matches').fetchone()[0])

    def test_japan_transition_and_bool_goals_quarantined(self):
        p=self.capture(league={'id':3836571083},kickoff_utc='2026-05-01T06:00:00+00:00')
        stats=ingest_page(self.store,p,'jpn.1',3836571083);self.assertEqual(stats['certified'],0)
        p=self.capture(id=2,goals={'home':True,'away':1});stats=ingest_page(self.store,p,'ger.1',686337048)
        self.assertEqual(stats['certified'],0)

    def test_wrong_league_does_not_enter_observations(self):
        p=self.capture();ingest_page(self.store,p,'eng.1',4160026622)
        self.assertEqual(self.store.audit()['observations'],0)

    def test_internally_inconsistent_full_time_retracts_certification(self):
        p=self.capture();ingest_page(self.store,p,'ger.1',686337048)
        p=self.capture(events=[{'type':'period_score','period':'second_half','score':{'home':2,'away':2}}])
        stats=ingest_page(self.store,p,'ger.1',686337048);self.assertEqual(stats['certified'],0)
        self.store.project();self.assertEqual(self.store.audit()['training_90'],0)
        self.assertIn('API_SCORE_INTERNAL_CONFLICT',self.store.audit()['issues'])

    def test_cross_provider_bridge_requires_two_opponent_date_proofs(self):
        h=self.store.team('GER','Canonical Home',100);a=self.store.team('GER','Away',200)
        p=self.capture();sid=self.store.source(p,'test')
        for i,date in enumerate(['2026-09-01','2026-09-08']):
            p=self.capture(id=i+1,kickoff_utc=date+'T18:00:00+00:00');ingest_page(self.store,p,'ger.1',686337048)
            self.store.add(sid,{'source_key':'espn:'+str(i),'competition':'ger.1','date':date,'season':'2026/27',
                 'home_id':h,'away_id':a,'orientation':'home_away','provider':'espn','observed_at':'2026-10-10T12:00:00+00:00'})
            if i==0:
                bridge_provider_ids(self.store);self.assertNotEqual(self.store.resolve('5dollar:11'),h)
        bridge_provider_ids(self.store);self.assertEqual(self.store.resolve('5dollar:11'),h)


if __name__=='__main__':unittest.main()
