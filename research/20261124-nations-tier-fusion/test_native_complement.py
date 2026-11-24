"""Test native first-issue decisions and market evidence without archive assets."""
from copy import deepcopy
from types import SimpleNamespace
import unittest

from native_complement import _bsd_rows, _native_market, _signed_choices, _validate_fixture, digest


class NativeEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.cut = '2026-11-16T10:00:00Z'
        self.capture = '2026-11-16T09:59:00Z'
        self.price = '2026-11-16T09:57:00Z'
        self.kick = '2026-11-16T15:00:00Z'
        self.h = 'e'*64
        self.url = 'https://api.the-odds-api.com/v4/sports/soccer_uefa_nations_league/odds/'
        self.fixture = dict(match_id='espn:11', cutoff_at=self.cut, kickoff_at=self.kick, cohort='uefa.nations:A:league-phase',
                            date='2026-11-16', total_quote=dict(match_id='espn:11', line=2.5, market='standard_total',
                            provider='the-odds-api', bookmaker='pinnacle', over_odds=1.92, under_odds=1.95,
                            source_url=self.url, source_sha256=self.h, query_time=self.capture, quoted_at=self.price, verified=True))
        self.event = dict(id='book-event', sport_key='soccer_uefa_nations_league', commence_time=self.kick,
                          home_team='Team A', away_team='Team B', bookmakers=[dict(key='pinnacle', markets=[
                              dict(key='h2h',last_update=self.price,outcomes=[dict(name=n,price=p) for n,p in
                                   [('Team A',1.90),('Draw',3.60),('Team B',4.1)]]),
                              dict(key='totals',last_update=self.price,outcomes=[dict(name=n,price=p,point=2.5)
                                   for n,p in [('Over',1.92),('Under',1.95)]])])])
        self.cross = dict(market_event_id='book-event', source_sha256=self.h, captured_at=self.capture,
                          commence_time=self.kick, identity_match_count=1, home='Team A', away='Team B')
        self.roster = dict(names={'home':{'teama'},'away':{'teamb'}})
        self.arc = SimpleNamespace(panel='xg',markets=((self.url,self.price,self.capture),self.h,[self.event],{'espn:11':self.cross}))

    def market(self):
        return _native_market(self.arc,self.fixture,self.roster)

    def test_one_native_same_book_capture_builds_market(self):
        line,choice,h,reason = self.market()
        self.assertEqual((line,h,reason),(2.5,self.h,None))
        self.assertEqual(choice['primary'],choice['top3'][0])
        self.assertEqual(len(set(choice['top3'])),3)
        self.assertIn(choice['total'],('under','over'))

    def test_cross_event_or_cross_side_rejected(self):
        self.cross['market_event_id'] = 'another-event'
        self.assertIsNone(self.market()[0])
        self.cross['market_event_id'] = self.event['id']
        self.event['home_team'],self.event['away_team'] = 'Team B','Team A'
        self.assertIsNone(self.market()[0])

    def test_signed_total_survives_stale_or_missing_h2h_only(self):
        self.event['bookmakers'][0]['markets'][0]['last_update'] = '2026-11-16T08:00:00Z'
        self.assertEqual(self.market()[:3],(2.5,None,self.h))
        self.event['bookmakers'][0]['markets'].pop(0)
        self.assertEqual(self.market()[:3],(2.5,None,self.h))

    def test_wrong_price_or_capture_past_cutoff_cannot_certify_the_line(self):
        self.fixture['total_quote']['under_odds'] += .01
        self.assertEqual(self.market()[-1],'UNVERIFIED_NATIVE_TOTAL')
        self.fixture['total_quote']['under_odds'] -= .01
        self.fixture['cutoff_at'] = '2026-11-16T09:58:00Z'
        self.assertIsNone(self.market()[0])

    def test_conditional_frozen_primary_may_differ_from_matrix_mode(self):
        matrix = [[.05,.26,0],[.24,.23,0],[.22,0,0]]
        art = dict(core=dict(cohort=self.fixture['cohort'],train_end='2026-11-15'),
                   role='challenger',selector='wdl_conditioned_map',version='v',created_at=self.price)
        ref = dict(sha256='d'*64,version='v')
        pred = dict(matrix=matrix,matrix_hash=digest(matrix),primary='1-0',primary_probability=.24,
                    wdl={'主胜':.46,'平局':.28,'客胜':.26},wdl_reference='主胜',input=self.fixture,version='v',
                    training_data_cutoff='2026-11-15',artifact_sha256='d'*64,generated_at=self.cut,
                    selector=art['selector'])
        def choice():
            return _signed_choices(pred,art,ref,self.fixture,self.cut,None,'challenger')
        x = choice()
        self.assertEqual((x['primary'],x['top3'][0],x['wdl'],x['total']),('1-0','0-1','home',None))
        for field, replacement in [('primary','0-1'),('artifact_sha256','0'*64),('matrix_hash','0'*64)]:
            with self.subTest(field=field):
                prev = pred[field]
                pred[field] = replacement
                with self.assertRaises(ValueError): choice()
                pred[field] = prev
        art['created_at'] = self.kick
        with self.assertRaises(ValueError): choice()

    def test_zero_weight_is_not_a_distinct_bsd_source(self):
        control = dict(core=dict(blend=dict(id='goals'),coverage=dict(xg_used_sides=0)))
        art = dict(core=dict(blend=dict(weight=0),coverage={}))
        with self.assertRaisesRegex(ValueError,'incremental xG'):
            _bsd_rows(None,art,control,self.fixture)

    def test_venue_stage_and_postkickoff_commit_rejected(self):
        f = dict(self.fixture,gender='men',age='senior',entity='national',phase='PreMatch',score_basis='regulation_90',
                 identity_verified=True,competition='uefa.nations',stage='league-phase',league_level='A',
                 home='espn:1',away='espn:2',source_event_id='11',neutral=False)
        url = 'https://site.api.espn.com/apis/site/v2/sports/soccer/uefa.nations/summary?event=11'
        f['phase_evidence'] = dict(source_path='test',source_sha256=self.h,query_time=self.capture,source_url=url)
        game = dict(id='11',date=self.kick,neutralSite=False,competitors=[dict(homeAway='home',team=dict(id=1)),
                    dict(homeAway='away',team=dict(id=2))],status={'type':dict(state='pre',completed=False)},
                    groups=dict(name='GROUP A1'))
        h = dict(id='11',league=dict(slug='uefa.nations'),season=dict(year=2026,name='League Phase'),competitions=[game])
        a = SimpleNamespace(committed_at='2026-11-16T10:05:00Z',identity_captures={self.h:(url,self.price,self.capture)},
                            value=lambda path,expected:{'header':h})
        plan = dict(payload=dict(capture_track='native'),recorded_at=self.cut,record_sha256='a'*64)
        r = dict(fixture=f,match_id=f['match_id'],stage='prematch',track='native',revision=1,previous_sha256=None,
                 evaluation_slot='FIRST_PREMATCH',recorded_at=self.cut,payload=dict(plan_sha256='a'*64))
        original = deepcopy(f)
        def check():
            return _validate_fixture(a,r,f,original,plan)
        check()
        game['neutralSite'] = True
        with self.assertRaises(ValueError): check()
        game['neutralSite'] = False
        h['season']['name'] = 'Final'
        with self.assertRaises(ValueError): check()
        h['season']['name'] = 'League Phase'
        a.committed_at = self.kick
        with self.assertRaises(ValueError): check()

if __name__ == '__main__':
    unittest.main()
