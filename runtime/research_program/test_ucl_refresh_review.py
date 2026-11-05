import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from research_program import test_ucl_refresh_forward as setup_module
from research_program import ucl_refresh_forward as forward
from research_program import ucl_refresh_review as module


class RefreshReviewTests(unittest.TestCase):
    def setUp(self):
        self.helper=setup_module.RefreshForwardTests(methodName='test_capture_same_fixture_separate_tracks_and_pending')
        self.helper.setUp();self.addCleanup(self.helper.doCleanups)
        self.root=self.helper.root;self.protocol=self.helper.protocol;self.plan=self.helper.plan
        self.fixture=copy.deepcopy(self.helper.fixture)
    def capture(self,quote=False):
        fixture=copy.deepcopy(self.fixture)
        if quote:
            fixture['total_quote']={'match_id':fixture['match_id'],'verified':True,'bookmaker':'SyntheticBook',
                'market':'standard_total','line':1.25,'over_odds':2.,'under_odds':2.,
                'quoted_at':(forward.aware(fixture['cutoff_at'])-timedelta(minutes=1)).isoformat(),
                'query_time':fixture['cutoff_at'],'source_url':'https://example.test/synthetic'}
        def runner(f,registry):
            value=self.helper.runner(f,registry)
            for key in ('current_model','test_model'):
                value[key]['generated_at']=datetime.now(timezone.utc).isoformat()
                value[key]['wdl_reference']='主胜'
                value[key]['total']={'quote':f['total_quote'],'reference_direction':'over'} if quote else None
            return value
        return forward.capture(self.protocol,self.plan,fixture,self.root/'real-package.json',self.helper.freezes,runner)
    def result(self,h=0,a=0):
        observed=forward.aware(self.fixture['kickoff_at'])+timedelta(hours=2)
        mid=self.fixture['match_id'];raw={'header':{'id':mid,'league':{'slug':'uefa.champions'},'competitions':[{
            'date':self.fixture['kickoff_at'],'status':{'period':2,'type':{'completed':True,'name':'STATUS_FULL_TIME'}},
            'competitors':[{'homeAway':'home','team':{'id':'1'},'score':str(h)},
                           {'homeAway':'away','team':{'id':'2'},'score':str(a)}]}]}}
        path=self.root/'synthetic-result.json';path.write_text(json.dumps(raw))
        result={'match_id':mid,'event_status':'FT','score_basis':'regulation_90','home_goals':h,'away_goals':a,
            'observed_at':observed.isoformat(),'evidence':{'file':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'identity_verified':True,'query_time':observed.isoformat(),
            'url':'https://site.api.espn.com/apis/site/v2/sports/soccer/uefa.champions/summary?event=SYNTHETIC'}}
        return result,observed+timedelta(hours=1)
    def test_pending_and_missing_keep_whole_population(self):
        value=module.review(self.protocol,self.plan,[],[])
        self.assertEqual(value['population'],1);self.assertEqual(value['completed'],0)
        self.assertEqual(value['missing_first_pairs'],[self.fixture['match_id']])
        pair=self.capture();value=module.review(self.protocol,self.plan,[pair],[])
        self.assertEqual(value['pending_results'],[self.fixture['match_id']]);self.assertEqual(value['first_paired'],1)
        self.assertFalse(value['automatic_promotion'])
        self.assertTrue(all(d['primary_n']==0 and d['actual_line_n']==0 for d in value['independent_denominators'].values()))
    def test_strict_retained_result_and_four_independent_metrics(self):
        pair=self.capture();result,now=self.result()
        with patch.object(module,'datetime') as clock:
            clock.now.return_value=now
            value=module.review(self.protocol,self.plan,[pair],[result],self.root)
        self.assertEqual(value['completed'],1);self.assertEqual(len(value['comparisons']),2)
        self.assertEqual(set(value['independent_denominators']),set(module.ROLES))
        self.assertTrue(all(v['primary_hits']==1 for v in value['independent_denominators'].values()))
        self.assertTrue(all(v['primary']['n']==1 for v in value['probability_metrics'].values()))
        self.assertIn('INDEPENDENT_RESULT_AND_SOURCE_ASOF_AUDIT_REQUIRED',value['comparisons']['active_current_vs_refreshed_current']['reasons'])
        # Publication response corruption cannot produce a settled prediction.
        (self.root/result['evidence']['file']).write_text('{}')
        with patch.object(module,'datetime') as clock:
            clock.now.return_value=now
            with self.assertRaises(ValueError):module.review(self.protocol,self.plan,[pair],[result],self.root)
    def test_actual_quarter_line_settlement_not_binary_hit(self):
        pair=self.capture(quote=True);result,now=self.result(1,0)
        with patch.object(module,'datetime') as clock:
            clock.now.return_value=now
            value=module.review(self.protocol,self.plan,[pair],[result],self.root)
        for role in module.ROLES:
            self.assertEqual(value['independent_denominators'][role]['actual_line_half_loss'],1)
            self.assertEqual(value['independent_denominators'][role]['actual_line_n'],1)
        self.assertFalse(value['active_assets_modified'])
    def test_unfinished_observation_not_a_label_and_duplicates_refused(self):
        pair=self.capture();result,now=self.result();result['event_status']='LIVE'
        value=module.review(self.protocol,self.plan,[pair],[result],self.root)
        self.assertEqual(value['completed'],0);self.assertEqual(len(value['unscored_observations']),1)
        with self.assertRaises(ValueError):module.review(self.protocol,self.plan,[pair,pair],[])
        with self.assertRaises(ValueError):module.review(self.protocol,self.plan,[pair],[result,result],self.root)

if __name__=='__main__':unittest.main()
