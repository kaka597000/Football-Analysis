import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from research_program import future_status


class Tests(unittest.TestCase):
    def test_every_plan_is_reviewed_with_its_complete_population(self):
        inputs = {'plans': {'plans': [{'path':'a'}, {'path':'b'}]},
                  'packages': [{'match_id':'one'}], 'results': [],
                  'a': {'payload': {'eligible_match_ids':['one'], 'fixtures_identity':[{'match_id':'one'}]}},
                  'b': {'payload': {'eligible_match_ids':['two'], 'fixtures_identity':[{'match_id':'two'}]}}}
        def review(plan, packages, labels, evidence):
            ids=plan['payload']['eligible_match_ids']
            return {'population':len(ids),'completed':0,'pending_results':[p['match_id'] for p in packages],
                    'missing_first_pairs':[i for i in ids if i not in {p['match_id'] for p in packages}]}
        with tempfile.TemporaryDirectory() as tmp, patch.object(future_status,'read',side_effect=inputs.__getitem__), patch.object(future_status,'sha',return_value='hash'), patch.object(future_status,'review',side_effect=review) as called:
            value=future_status.run('plans','packages','results',Path(tmp)/'out.json')
        self.assertEqual(called.call_count,2)
        self.assertEqual(value['population'],2)
        self.assertEqual(value['missing_first_pairs'],['two'])
        self.assertEqual(value['pending_results'],[{'match_id':'one'}])


if __name__ == '__main__':
    unittest.main()
