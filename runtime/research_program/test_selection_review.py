import copy,unittest
from datetime import datetime,timedelta,timezone
from research.analysis_freeze import digest
from research_program.selection_review import compare
class SelectionTests(unittest.TestCase):
 def data(self):
  now=datetime.now(timezone.utc);kickoff=now-timedelta(days=1);t=(kickoff-timedelta(hours=1)).isoformat()
  fixture={'match_id':'SYNTHETIC','cohort':'eng.1','competition':'eng.1','stage':'regular',
           'home':'Synthetic Home','away':'Synthetic Away','cutoff_at':t,'kickoff_at':kickoff.isoformat()}
  def record(payload,track):
   r={'fixture':fixture,'match_id':fixture['match_id'],'stage':'prematch','track':track,'freeze_id':track+'-synthetic','recorded_at':t,'evaluation_slot':'FIRST_PREMATCH','payload':payload}
   r['record_sha256']=digest(r);return r
  m=[[.1,.1],[.6,.2]];prediction={'matrix':m,'matrix_hash':digest(m),'primary':'1-0','version':'SYNTHETIC'}
  pair=record({'current_model':prediction,'test_model':prediction},'current-test-pair')
  comp=record({'primary':'1-1','current_test_reference':{'freeze_id':pair['freeze_id'],'sha256':pair['record_sha256']}},'comprehensive')
  result={'status':'completed','match_id':'SYNTHETIC','home_goals':1,'away_goals':1,'score_basis':'regulation_90','source_url':'https://example.test/result','observed_at':now.isoformat()}
  return {'pair':pair,'comprehensive':comp},result
 def test_unavailable_version_retains_abstain_population(self):
  entry,result=self.data()
  for prediction in entry['pair']['payload'].values():
   prediction.update(version=None,matrix=None,matrix_hash=None,primary=None)
  entry['pair']['record_sha256']=digest({k:v for k,v in entry['pair'].items() if k!='record_sha256'})
  entry['comprehensive']['payload']['current_test_reference']['sha256']=entry['pair']['record_sha256']
  entry['comprehensive']['record_sha256']=digest({k:v for k,v in entry['comprehensive'].items() if k!='record_sha256'})
  r=compare([entry],[result]);g=r['by_model_version']['current_model:UNAVAILABLE']
  self.assertEqual(g['population'],1);self.assertEqual(g['settled'],1)
  self.assertEqual(g['model_available'],0);self.assertEqual(g['paired_n'],0)
  self.assertEqual(r['comprehensive_hits'],1)
 def test_net_hit_and_pending_denominator(self):
  entry,result=self.data();r=compare([entry],[result]);self.assertEqual(r['by_model_version']['current_model:SYNTHETIC']['net_hits'],1)
  r=compare([entry],[]);self.assertEqual(len(r['pending']),1);self.assertEqual(r['population'],1)
 def reseal(self,entry):
  entry['pair']['record_sha256']=digest({k:v for k,v in entry['pair'].items() if k!='record_sha256'})
  entry['comprehensive']['payload']['current_test_reference']['sha256']=entry['pair']['record_sha256']
  entry['comprehensive']['record_sha256']=digest({k:v for k,v in entry['comprehensive'].items() if k!='record_sha256'})
 def test_missing_version_in_no_active_route_keeps_pending_population(self):
  entry,_=self.data()
  entry['pair']['payload']={k:{'primary':None,'matrix':None,'status':'ABSTAIN_NO_ACTIVE_ROUTE'} for k in ('current_model','test_model')}
  self.reseal(entry);r=compare([entry],[])
  self.assertEqual(r['population'],1);self.assertEqual(len(r['pending']),1)
  self.assertEqual(r['by_model_version']['current_model:UNAVAILABLE']['model_available'],0)
  self.assertIsNone(r['pending_rows'][0]['models']['current_model']['version'])
 def test_issued_primary_and_matrix_mode_are_separate_comparisons(self):
  entry,result=self.data()
  for prediction in entry['pair']['payload'].values():prediction['primary']='1-1'
  self.reseal(entry);r=compare([entry],[result]);m=r['rows'][0]['models']['current_model']
  self.assertEqual(m['statistical_primary'],'1-0');self.assertFalse(m['statistical_hit'])
  self.assertEqual(m['issued_primary'],'1-1');self.assertTrue(m['issued_primary_hit'])
  self.assertEqual(r['by_model_version']['current_model:SYNTHETIC']['net_hits'],1)
 def test_reference_tampering_or_revision_selection_rejected(self):
  entry,result=self.data();entry['comprehensive']['evaluation_slot']='SEPARATE_REVISION_OR_STAGE';body={k:v for k,v in entry['comprehensive'].items() if k!='record_sha256'};entry['comprehensive']['record_sha256']=digest(body)
  with self.assertRaises(ValueError):compare([entry],[result])
 def test_duplicate_matches_do_not_double_count(self):
  entry,result=self.data()
  with self.assertRaises(ValueError):compare([entry,entry],[result])
 def test_pair_team_competition_or_phase_conflicts_rejected(self):
  for field in ('home','away','competition','stage'):
   with self.subTest(field=field):
    entry,result=self.data()
    entry['pair']['fixture']=dict(entry['pair']['fixture'],**{field:'CONFLICT'})
    self.reseal(entry)
    with self.assertRaisesRegex(ValueError,'pair chronology/identity'):compare([entry],[result])
 def test_explicit_result_identity_conflicts_cannot_settle(self):
  for field in ('cohort','competition','stage','home','away','kickoff_at'):
   with self.subTest(field=field):
    entry,result=self.data()
    result[field]='2000-01-01T00:00:00Z' if field=='kickoff_at' else 'CONFLICT'
    with self.assertRaisesRegex(ValueError,'result.*identity'):compare([entry],[result])
 def test_alias_fixture_cannot_inflate_population(self):
  entry,result=self.data();alias=copy.deepcopy(entry)
  for record in alias.values():
   record['match_id']='ALIAS';record['fixture']['match_id']='ALIAS'
  self.reseal(alias)
  with self.assertRaisesRegex(ValueError,'different IDs'):
   compare([entry,alias],[result,dict(result,match_id='ALIAS')])
 def test_pair_cutoff_and_timezone_may_differ_for_same_fixture(self):
  entry,result=self.data();fixture=entry['pair']['fixture']
  entry['pair']['fixture']=dict(fixture,
   cutoff_at=(datetime.fromisoformat(fixture['cutoff_at'])-timedelta(minutes=10)).isoformat(),
   kickoff_at=datetime.fromisoformat(fixture['kickoff_at']).astimezone(timezone(timedelta(hours=8))).isoformat())
  self.reseal(entry)
  self.assertEqual(compare([entry],[result])['comprehensive_hits'],1)
 def test_same_teams_on_distinct_kickoffs_remain_independent(self):
  entry,result=self.data();later=copy.deepcopy(entry)
  kickoff=(datetime.fromisoformat(entry['comprehensive']['fixture']['kickoff_at'])+timedelta(hours=1)).isoformat()
  for record in later.values():
   record['match_id']='LATER';record['fixture'].update(match_id='LATER',kickoff_at=kickoff)
  self.reseal(later)
  report=compare([entry,later],[result,dict(result,match_id='LATER',kickoff_at=kickoff)])
  self.assertEqual(report['population'],2);self.assertEqual(report['comprehensive_hits'],2)
 def test_missing_legacy_identity_fields_are_not_invented(self):
  entry,result=self.data()
  for field in ('home','away','competition','stage'):
   entry['comprehensive']['fixture'].pop(field)
  self.reseal(entry)
  self.assertEqual(compare([entry],[result])['comprehensive_hits'],1)
 def test_contradictory_result_status_cannot_settle_comprehensive(self):
  entry,result=self.data();result['event_status']='LIVE'
  with self.assertRaisesRegex(ValueError,'conflicting result status'):compare([entry],[result])
 def test_pending_and_abstained_keep_each_version_denominator(self):
  entry,result=self.data();r=compare([entry],[])
  group=r['by_model_version']['current_model:SYNTHETIC']
  self.assertEqual(group['population'],1);self.assertEqual(group['pending'],1)
  self.assertEqual(group['comprehensive_coverage'],1.);self.assertIsNone(group['settled_pair_coverage'])
  entry['comprehensive']['payload']['primary']=None
  entry['comprehensive']['record_sha256']=digest({k:v for k,v in entry['comprehensive'].items() if k!='record_sha256'})
  group=compare([entry],[result])['by_model_version']['current_model:SYNTHETIC']
  self.assertEqual(group['population'],1);self.assertEqual(group['settled'],1)
  self.assertEqual(group['paired_n'],0);self.assertEqual(group['comprehensive_abstained'],1)
  self.assertEqual(group['comprehensive_coverage'],0.)

if __name__=='__main__':unittest.main()
