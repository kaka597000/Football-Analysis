import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'v5'))
import copy
import unittest
from datetime import datetime,timezone,timedelta
from five_role_review import build_packets,validate_agent_record


class Tests(unittest.TestCase):
    def fixture(self):
        now=datetime.now(timezone.utc)
        return {'match_id':'SOFTWARE_ONLY','cutoff_at':now.isoformat(),
                'kickoff_at':(now+timedelta(days=1)).isoformat()}

    def test_missing_evidence_is_unknown_and_no_agents_or_probability_changes(self):
        f=self.fixture();forecast={'input':f,'matrix_hash':'SOFTWARE_ONLY_HASH'}
        before=copy.deepcopy(forecast);r=build_packets(f,forecast)
        self.assertEqual(forecast,before);self.assertEqual(len(r['role_packets']),5)
        self.assertEqual(r['independent_agent_runs'],0);self.assertFalse(r['probability_fusion_applied'])
        self.assertTrue(all(v['missing'] for v in r['role_packets'].values()))

    def test_post_cutoff_or_wrong_match_evidence_is_rejected(self):
        f=self.fixture();f['decision_evidence']={'home':{'form':{'evidence':[{
            'source_url':'https://example.test','query_time':f['kickoff_at']}]}}}
        with self.assertRaises(ValueError):build_packets(f,{'input':f,'matrix_hash':'x'})
        with self.assertRaises(ValueError):build_packets(f,{'input':{},'matrix_hash':'x'})

    def test_mock_or_unavailable_agent_can_never_enter_future_fusion(self):
        f=self.fixture()
        for mode in ('MOCK','UNAVAILABLE','DEFAULT'):
            with self.assertRaises(ValueError):validate_agent_record({'execution_mode':mode},f)
        r={'execution_mode':'REAL','mock':False,'role':'statistician','match_id':f['match_id'],
           'input_cutoff':f['cutoff_at'],'generated_at':f['cutoff_at'],'run_id':'SOFTWARE_ONLY',
           'provider_model':'SOFTWARE_TEST_NO_NETWORK','reasoning':'Test contract only'}
        self.assertTrue(validate_agent_record(r,f))
        r['generated_at']=f['kickoff_at']
        with self.assertRaises(ValueError):validate_agent_record(r,f)


if __name__=='__main__':unittest.main()
