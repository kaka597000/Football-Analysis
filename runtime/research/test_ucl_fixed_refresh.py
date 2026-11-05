import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from datetime import datetime,timezone
from unittest.mock import patch
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
import ucl_fixed_refresh as r

class UCLFixedRefreshTests(unittest.TestCase):
    def fixture(self,tmp):
        raw={'header':{'id':'1','league':{'slug':'uefa.champions'},'season':{'year':2026,'name':'2026-27 UEFA Champions League, League Phase'},'competitions':[{'date':'2026-01-01T18:00Z','neutralSite':False,'status':{'type':{'name':'STATUS_FULL_TIME','completed':True}},'competitors':[{'homeAway':side,'team':{'id':str(i)},'score':str(g),'linescores':[{'displayValue':str(g)},{'displayValue':'0'}]}for side,i,g in [('home',1,2),('away',2,1)]]}]}}
        p=Path(tmp)/'raw.json';p.write_text(json.dumps(raw));row={'match_id':'espn:1','provider_key':'espn:1','competition':'uefa.champions','cohort':r.COHORT,'stage':r.STAGE,'score_basis':'regulation_90','neutral':False,'date':'2026-01-01','home':'espn:1','away':'espn:2','home_goals':2,'away_goals':1,'first_known_at':None,'observed_at':'2026-01-02T00:00Z','source_url':'https://site.api.espn.com/apis/site/v2/sports/soccer/uefa.champions/summary?event=1','score_evidence':{'source_path':str(p),'response_sha256':r.sha(p),'query_time':None,'verified_at':'2026-01-02T00:00Z'}}
        return row,raw,p
    def test_retained_raw_reaudit_not_fake_fetch(self):
        with tempfile.TemporaryDirectory()as tmp:
            row,_,_=self.fixture(tmp);self.assertIs(r.verify_addition(row,datetime.now(timezone.utc)),row);self.assertIsNone(row['score_evidence']['query_time'])
    def test_tampered_raw_rejected(self):
        with tempfile.TemporaryDirectory()as tmp:
            row,raw,p=self.fixture(tmp);raw['header']['competitions'][0]['competitors'][0]['score']='3';p.write_text(json.dumps(raw))
            with self.assertRaisesRegex(ValueError,'hash'):r.verify_addition(row,datetime.now(timezone.utc))
    def test_three_period_score_refused(self):
        with tempfile.TemporaryDirectory()as tmp:
            row,raw,p=self.fixture(tmp);raw['header']['competitions'][0]['competitors'][0]['linescores'].append({'displayValue':'0'});p.write_text(json.dumps(raw));row['score_evidence']['response_sha256']=r.sha(p)
            with self.assertRaisesRegex(ValueError,'period'):r.verify_addition(row,datetime.now(timezone.utc))
    def test_url_event_must_match_verified_raw(self):
        with tempfile.TemporaryDirectory()as tmp:
            row,_,_=self.fixture(tmp);row['source_url']='https://site.api.espn.com/apis/site/v2/sports/soccer/uefa.champions/summary?event=999'
            with self.assertRaisesRegex(ValueError,'source URL'):r.verify_addition(row,datetime.now(timezone.utc))
    def test_pre_new_format_season_refused(self):
        with tempfile.TemporaryDirectory()as tmp:
            row,raw,p=self.fixture(tmp);raw['header']['season']['year']=2023;p.write_text(json.dumps(raw));row['score_evidence']['response_sha256']=r.sha(p)
            with self.assertRaisesRegex(ValueError,'pre-new-format'):r.verify_addition(row,datetime.now(timezone.utc))
    def test_unknown_neutral_refused(self):
        with tempfile.TemporaryDirectory()as tmp:
            row,_,_=self.fixture(tmp);row['neutral']=None
            with self.assertRaisesRegex(ValueError,'nonneutral'):r.verify_addition(row,datetime.now(timezone.utc))
    def test_network_time_cannot_precede_finish(self):
        with tempfile.TemporaryDirectory()as tmp:
            row,_,_=self.fixture(tmp);row['score_evidence']['query_time']='2026-01-01T18:30Z'
            with self.assertRaisesRegex(ValueError,'network observation'):r.verify_addition(row,datetime.now(timezone.utc))
    def test_future_firstknown_and_new_process_refused(self):
        with tempfile.TemporaryDirectory()as tmp:
            row,_,_=self.fixture(tmp);row['first_known_at']='2025-12-01T00:00Z'
            with self.assertRaisesRegex(ValueError,'first-known'):r.verify_addition(row,datetime.now(timezone.utc))
            row['first_known_at']=None;row['home_xg']=0.
            with self.assertRaisesRegex(ValueError,'separate validation'):r.verify_addition(row,datetime.now(timezone.utc))
    def test_current_refit_keeps_weights_recipe_and_old_object(self):
        original={'config':copy.deepcopy(r.weighted.CONFIG),'code_hash':r.weighted.code_hash(),'weights':[.2,.3,.5],'anchor':{'selected_recipe':'baseline','execution_recipe':'baseline','form':None,'config':copy.deepcopy(r.legacy.CONFIG),'code_hash':r.legacy.code_hash()}}
        snapshot=copy.deepcopy(original);rows=[{'date':'2026-01-01','observed_at':'2026-01-02T00:00Z'}]
        with patch.object(r.legacy,'fit_core',return_value={'fitted':'new'}):new=r.current_refit(original,rows,'2026-01-03T00:00Z','test')
        self.assertEqual(original,snapshot);self.assertEqual(new['weights'],snapshot['weights']);self.assertEqual(new['anchor']['execution_recipe'],'baseline');self.assertEqual(new['anchor']['history'],rows)
    def test_test_refit_keeps_exact_original_alpha_features(self):
        original={'config':copy.deepcopy(r.ml.CONFIG),'feature_names':copy.deepcopy(r.ml.FEATURES),'code_hash':r.ml.code_hash(),'alpha':.75,'acceptance':{'old_only':True}}
        class Booster:
            def model_to_string(self):return 'fitted-model'
            def feature_importance(self,x):
                import numpy as np
                return np.zeros(len(r.ml.FEATURES))
        rows=[{'date':'2026-01-01','observed_at':'2026-01-02T00:00Z'}]
        with patch.object(r.ml,'rolling_items',return_value=([{'dummy':'item'}],[{'fit_end':'2025-12-31','first_target':'2026-01-01'}])),patch.object(r.ml,'fit_booster',return_value=Booster()),patch.object(r.legacy,'fit_core',return_value={}):new,info=r.test_refit(original,rows,'2026-01-03T00:00Z','test')
        self.assertEqual(new['alpha'],.75);self.assertEqual(new['feature_names'],original['feature_names']);self.assertEqual(new['acceptance'],original['acceptance']);self.assertNotIn('booster_model',original)

if __name__=='__main__':unittest.main()
