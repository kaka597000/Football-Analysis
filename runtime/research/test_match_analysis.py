import copy,unittest
from unittest.mock import patch
from research.match_analysis import build,render,MODULES

class Tests(unittest.TestCase):
    def inputs(self):
        f={'match_id':'synthetic','cohort':'eng.1','phase':'PreMatch','cutoff_at':'2026-01-01T12:00:00Z','kickoff_at':'2026-01-02T12:00:00Z'}
        pair={'current_model':{'version':'test-current','matrix':[[.5,.2],[.2,.1]]},'test_model':{'version':'test-independent','matrix':[[.1,.2],[.2,.5]]}}
        b={'match_id':'synthetic','status':'available','as_of':f['cutoff_at'],'summary':'Historical process observed',
           'facts':{'recent_xg':1.2},'sources':[{'source_url':'https://example.test/stats','known_at':'2026-01-01T10:00:00Z','query_time':'2026-01-01T11:00:00Z'}]}
        return f,pair,b
    def build(self,f,pair):return build(f,pair,current_artifact={},test_artifact={'feature_xg_match_count':0})
    def test_all_modules_present_without_claiming_missing_data_or_agents_ran(self):
        f,p,b=self.inputs();before=copy.deepcopy(p);r=self.build(f,p)
        self.assertEqual(set(r['modules']),set(MODULES));self.assertEqual(p,before)
        self.assertEqual(r['modules']['research']['execution']['minute_simulation']['status'],'NOT_CONFIGURED')
        self.assertEqual(r['modules']['xg']['status'],'unknown');self.assertEqual(r['modules']['xg']['recorded_training_rows']['test'],0)
        self.assertFalse(r['numeric_prediction_modified']);self.assertIn('xG / xGA',render(r))
    def test_latest_xg_evidence_does_not_relabel_training_or_change_matrix(self):
        f,p,b=self.inputs();f['analysis_modules']={'xg':b};r=self.build(f,p)
        self.assertEqual(r['modules']['xg']['status'],'available');self.assertEqual(r['modules']['xg']['recorded_training_rows']['test'],0)
        self.assertEqual(r['modules']['xg']['evidence_usage'],'QUALITATIVE_EVIDENCE_ONLY')
    def test_identity_late_or_secret_source_rejected_without_echoing_input(self):
        for key,value in [('match_id','wrong'),('as_of','2026-01-03T00:00:00Z')]:
            f,p,b=self.inputs();b[key]=value;f['analysis_modules']={'xg':b};self.assertEqual(self.build(f,p)['modules']['xg']['status'],'invalid')
        f,p,b=self.inputs();b['sources'][0]['source_url']='https://example.test/?api_key=synthetic-secret';f['analysis_modules']={'xg':b};self.assertNotIn('synthetic-secret',render(self.build(f,p)))
    def test_minute_engine_called_only_for_matching_valid_prematch_request(self):
        f,p,b=self.inputs();req={'artifact':{'cohort':'eng.1'},'state':{'minute':0},'regulation_minutes':96};f['minute_simulation_request']=req
        with patch('research.minute_simulator.simulate',return_value={'status':'SYNTHETIC_ENGINE_RESULT'}) as engine:
            r=self.build(f,p);self.assertEqual(r['modules']['research']['execution']['minute_simulation']['status'],'SYNTHETIC_ENGINE_RESULT');self.assertEqual(engine.call_args.kwargs['as_of'],f['cutoff_at'])
            req['state']['minute']=20;engine.reset_mock();r=self.build(f,p);engine.assert_not_called();self.assertEqual(r['modules']['research']['execution']['minute_simulation']['status'],'REFUSED_INVALID_OR_UNTRAINED_INPUT')
    def test_report_is_detached_from_mutable_evidence_and_model_outputs(self):
        f,p,b=self.inputs()
        p['current_model']['team_goal_analysis']={'home':{'mean':1.25}}
        f['analysis_modules']={'xg':b}
        report=self.build(f,p)
        before=render(report)
        b['facts']['recent_xg']=99
        b['sources'][0]['source_url']='https://example.test/changed'
        p['current_model']['team_goal_analysis']['home']['mean']=99
        self.assertEqual(render(report),before)

if __name__=='__main__':unittest.main()
