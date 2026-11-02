import copy,json,math,tempfile,unittest
from pathlib import Path
from datetime import datetime,timezone,timedelta
import numpy as np
from classifier import fair,favorite,items,history_validate,FEATURES,CONFIG,VERSION,code_hash,predict,attach,review
class Tests(unittest.TestCase):
 def rows(self):
  return [dict(match_id='a',cohort='eng.1',date='2020-01-01',home='h',away='a',home_goals=2,away_goals=0,score_basis='regulation_90',odds=[1.5,4.,7.]),dict(match_id='b',cohort='eng.1',date='2020-01-02',home='h',away='b',home_goals=0,away_goals=0,score_basis='regulation_90',odds=[1.5,4.,7.]),dict(match_id='c',cohort='eng.1',date='2020-01-02',home='a',away='b',home_goals=0,away_goals=3,score_basis='regulation_90',odds=[7.,4.,1.5])]
 def context(self):
  now=datetime.now(timezone.utc);fixture=dict(match_id='synthetic-test',cohort='eng.1',home='h',away='a',neutral=False,cutoff_at=now.isoformat(),kickoff_at=(now+timedelta(days=1)).isoformat());quote=dict(match_id=fixture['match_id'],market='1x2',verified=True,source_url='https://example.test/odds',bookmaker='synthetic-test-only',query_time=now.isoformat(),home_odds=1.5,draw_odds=4.,away_odds=7.)
  n=len(FEATURES);artifact=dict(version=VERSION,cohort='eng.1',config=CONFIG,code_hash=code_hash(),built_at=(now-timedelta(hours=1)).isoformat(),history=self.rows(),alert_threshold=.2,acceptance_gate_passed=False,model=dict(medians=[0.]*n,means=[0.]*n,scales=[1.]*n,coefficients=[[0.]*n]*3,intercepts=[0.,0.,0.]))
  return artifact,fixture,quote
 def test_favorite_draw_and_weak_market_are_not_eligible(self):
  self.assertAlmostEqual(sum(fair([1.5,4.,7.])),1.);self.assertEqual(favorite([1.5,4.,7.])[0],'home');self.assertIsNone(favorite([4.,1.4,5.]));self.assertIsNone(favorite([2.6,3.2,2.6]))
 def test_invalid_market_is_rejected(self):
  for odds in ([0.,3.,4.],[1.5,float('nan'),4.],[1.5,1.,4.]):
   with self.assertRaises(ValueError):fair(odds)
 def test_target_and_same_date_outcomes_do_not_leak_into_features(self):
  a=self.rows();b=copy.deepcopy(a);b[1]['home_goals']=9;b[2]['away_goals']=9
  x=items(a);y=items(b)
  for i in range(3):np.testing.assert_allclose(x[i]['features'],y[i]['features'],equal_nan=True)
  self.assertNotEqual(x[1]['label'],y[1]['label'])
 def test_competition_and_duplicate_guards(self):
  a=self.rows();a[1]['cohort']='esp.1'
  with self.assertRaises(ValueError):history_validate(a)
  with self.assertRaises(ValueError):history_validate(self.rows()+self.rows()[:1])
 def test_missing_information_is_not_claimed_as_zero(self):
  x=items(self.rows())[0]['features'];self.assertTrue(math.isnan(x[5]));self.assertEqual(x[-2],1.)
 def test_prediction_preserves_inputs_and_probabilities(self):
  a,f,q=self.context();original=copy.deepcopy(f);r=predict(a,f,q)
  self.assertEqual(f,original);self.assertAlmostEqual(sum(r['probabilities'].values()),1.);self.assertAlmostEqual(r['favorite_nonwin_probability'],2/3);self.assertFalse(r['main_score_reordered']);self.assertEqual(r['risk_label'],'UNVALIDATED_NO_ACTION_ALERT')
 def test_future_quote_and_postkickoff_are_rejected(self):
  a,f,q=self.context();q['query_time']=(datetime.now(timezone.utc)+timedelta(hours=2)).isoformat()
  with self.assertRaises(ValueError):predict(a,f,q)
  a,f,q=self.context();f['kickoff_at']=(datetime.now(timezone.utc)-timedelta(hours=2)).isoformat()
  with self.assertRaises(ValueError):predict(a,f,q)
 def test_no_cross_league_fallback_or_missing_quote_probability(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'registry.json';path.write_text(json.dumps({'models':{'eng.1':{'artifact_path':'unused-test'}}}));a,f,q=self.context()
   self.assertEqual(attach(f,path)['status'],'ABSTAIN_MISSING_1X2_QUOTE');f['cohort']='afc.champions';self.assertEqual(attach(f,path)['status'],'ABSTAIN_NO_TRAINED_COHORT')
 def test_review_separates_draw_from_favorite_loss(self):
  now=datetime.now(timezone.utc);ko=now-timedelta(hours=4);records=[];results=[]
  for i,(h,a) in enumerate([(1,1),(0,2)]):
   mid=str(i);records.append(dict(match_id=mid,version=VERSION,status='RESEARCH_ONLY',favorite_side='home',risk_label='NO_HIGH_ALERT',data_cutoff=(ko-timedelta(hours=1)).isoformat(),generated_at=(ko-timedelta(minutes=30)).isoformat(),kickoff_at=ko.isoformat()));results.append(dict(match_id=mid,home_goals=h,away_goals=a,score_basis='regulation_90',observed_at=now.isoformat(),source_url='https://example.test/results'))
  r=review(records,results);self.assertEqual(r['favorite_losses'],1);self.assertEqual(r['favorite_nonwins'],2);self.assertIsNone(r['high_alert_precision'])
  with self.assertRaises(ValueError):review(records,results+results[:1])
if __name__=='__main__':unittest.main()
