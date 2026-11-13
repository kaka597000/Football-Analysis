import copy,unittest
from test_promotion_review import seal
import test_promotion_review as promotion_tests
from research_program.complementarity import compare


class ComplementarityTests(unittest.TestCase):
 def data(self):
  _,rows,_=promotion_tests.GateTests().package();pairs=[];results=[]
  for row in rows[:3]:
   pair=copy.deepcopy(row['current_record']);p=copy.deepcopy(row['candidate_record']['payload'])
   pair['payload']['execution_audit']['test']={'artifact_sha256':'b'*64}
   pair['payload']['test_model']=p;seal(pair);pairs.append(pair);results.append(row['result'])
  return pairs,results
 def test_disagreement_and_complete_population(self):
  pairs,results=self.data();r=compare(pairs,results[:2]);g=r['groups'][0]
  self.assertEqual(r['population'],3);self.assertEqual(g['pending'],1)
  self.assertEqual(g['different_primary'],3);self.assertEqual(g['only_test_right'],2)
  self.assertEqual(g['paired_net_test_hits'],2);self.assertEqual(g['coverage'],[1.,1.])
  self.assertEqual(g['probability_metrics'][1]['primary']['hits'],2)
 def test_conflicting_result_status_cannot_settle(self):
  for status in ('LIVE','POSTPONED','CANCELLED'):
   with self.subTest(event_status=status):
    pairs,results=self.data();results[0]['event_status']=status
    with self.assertRaisesRegex(ValueError,'conflicting result status'):compare(pairs,results)
  pairs,results=self.data()
  for result in results:result['event_status']='FT'
  self.assertEqual(compare(pairs,results)['groups'][0]['settled'],3)
 def test_missing_primary_never_filled_from_matrix(self):
  pairs,results=self.data();pairs[0]['payload']['test_model']['primary']=None;seal(pairs[0])
  g=compare(pairs,results)['groups'][0]
  self.assertEqual(g['settled'],3);self.assertEqual(g['settled_both_available'],2)
  self.assertEqual(g['full_settled_accuracy'][1],2/3)
  self.assertEqual(g['probability_metrics'][1]['n'],3)
  self.assertEqual(g['probability_metrics'][1]['primary']['coverage'],2/3)
  pairs[0]['payload']['test_model']['input']={};seal(pairs[0])
  with self.assertRaisesRegex(ValueError,'actual input'):compare(pairs,results)
 def test_versions_stay_separate_and_revisions_rejected(self):
  pairs,results=self.data();pairs[0]['payload']['test_model']['version']='OTHER';seal(pairs[0])
  self.assertEqual(len(compare(pairs,results)['groups']),2)
  with self.assertRaises(ValueError):compare(pairs+pairs[:1],results)
  pairs[0]['evaluation_slot']='SEPARATE_REVISION_OR_STAGE';seal(pairs[0])
  with self.assertRaises(ValueError):compare(pairs,results)
 def test_artifact_identities_and_alias_fixtures_cannot_pool(self):
  pairs,results=self.data();pairs[0]['payload']['execution_audit']['test']['artifact_sha256']='c'*64;seal(pairs[0])
  self.assertEqual(len(compare(pairs,results)['groups']),2)
  other=copy.deepcopy(pairs[0]);other['fixture']['match_id']='alias'
  for name in ('current_model','test_model'):other['payload'][name]['input']['match_id']='alias'
  seal(other)
  with self.assertRaisesRegex(ValueError,'different IDs'):compare(pairs+[other],results)


if __name__=='__main__':unittest.main()
