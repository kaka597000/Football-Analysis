import json,unittest
from pathlib import Path
import tempfile
from unittest.mock import patch
from competition_scope import policy,require,inventory,training_rows
class ScopeTests(unittest.TestCase):
 def test_exact_scope_and_pending_routes(self):
  self.assertEqual(len(policy()['competitions']),20)
  self.assertEqual(policy()['expected_competition_count'],20)
  for code in ('swe.1','fin.1','uefa.europa','uefa.europa.conf','fifa.world','uefa.nations','jpn.league_cup'):
   require({'cohort':code})
 def test_retired_wrong_identity_and_afc2_rejected(self):
  for fixture in [{'cohort':k} for k in ('bra.1','eng.2','ksa.1','mex.1','por.1','afc.cup:CHAMPIONS_TWO:group_stage')]+[{'cohort':'eng.1','gender':'women'},{'cohort':'eng.1','age':'U21'},{'cohort':'eng.1','competition':'bra.1'},{'cohort':'fifa.world','stage':'qualifying'},{'cohort':'eng.1','score_basis':'extra_time'}]:
   with self.subTest(fixture=fixture),self.assertRaises(ValueError):require(fixture)
 def test_phase_history_cannot_mix(self):
  with self.assertRaises(ValueError):training_rows([{'cohort':'fin.1','stage':'regular'},{'cohort':'fin.1','stage':'championship'}])
 def test_default_registry_reports_four_gaps_without_mutation(self):
  reg={'models':{k:{} for k in ['eng.1','swe.1','bra.1']}}
  original=json.dumps(reg);r=inventory(reg)
  self.assertIn('bra.1',r['historical_only_routes']);self.assertEqual(original,json.dumps(reg))
 def test_regular_aliases_share_existing_route_without_rewriting_source(self):
  rows=[{'cohort':'aus.1','stage':'regular'},{'cohort':'aus.1','stage':'regular-season'}]
  original=json.dumps(rows);training_rows(rows);self.assertEqual(original,json.dumps(rows))
 def test_malformed_identities_return_false(self):
  from competition_scope import permitted
  for item in (None,{'cohort':None},{'cohort':[]},{'cohort':'eng.1','competition':[]}):
   self.assertFalse(permitted(item))
 def test_original_identities_preserved_and_nations_route_pending(self):
  expected={'eng.1','esp.1','ger.1','ita.1','fra.1','jpn.1','kor.1','aus.1','usa.1','fin.1','nor.1','swe.1','ned.1','uefa.champions','uefa.europa','uefa.europa.conf','afc.champions','fifa.world'}
  rows=policy()['competitions'];self.assertEqual({r['code'] for r in rows}-{'uefa.nations','jpn.league_cup'},expected)
  nations=next(r for r in rows if r['code']=='uefa.nations')
  self.assertTrue(nations['active_route_gap']);self.assertTrue(nations['requires_phase_identity'])
  report=inventory({'models':{'eng.1':{}}});self.assertEqual(report['scope_count'],20)
  self.assertIn('uefa.nations',report['missing_competitions'])
  for stage in ('friendly','qualifying','qualification'):
   with self.assertRaises(ValueError):require({'cohort':'uefa.nations','stage':stage})
 def test_count_and_uniqueness_follow_explicit_configuration(self):
  base=policy()
  with tempfile.TemporaryDirectory() as root:
   path=Path(root)/'scope.json'
   for alter in ('count','duplicate','missing_count','bool_count'):
    item=json.loads(json.dumps(base))
    if alter=='count':item['competitions'].pop()
    elif alter=='duplicate':item['competitions'][-1]=item['competitions'][0]
    elif alter=='missing_count':del item['expected_competition_count']
    else:item['expected_competition_count']=True
    path.write_text(json.dumps(item))
    with self.subTest(alter=alter),patch('competition_scope.CONFIG',path),self.assertRaises(ValueError):policy()
if __name__=='__main__':unittest.main()
