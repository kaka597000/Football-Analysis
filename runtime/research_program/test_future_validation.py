"""Synthetic safety/denominator tests; these are not future football results."""
import copy
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from research.analysis_freeze import digest
from research_program.future_validation import (quote_audit, review, audit_existing, capture,
                                                lock_protocol, check_protocol, register, sha)
from research_program.test_promotion_review import GateTests, seal
from research_program.challengers import CONFIG


class FutureTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.plan,rows,_=GateTests().package();self.row=rows[0]
        self.plan['payload']['eligible_match_ids']=['0'];seal(self.plan)
        for role in ('current','candidate'):
            rec=self.row[role+'_record'];f=rec['fixture'];f['home']='espn:A';f['away']='espn:B'
            p=rec['payload']['current_model'] if role=='current' else rec['payload']
            p['input']=copy.deepcopy(f);p['input_sha256']=digest(f)
            if role=='current':rec['payload']['execution_audit']['fixture_sha256']=digest(f)
            p['wdl_reference']='主胜' if role=='candidate' else None
            seal(rec)

    def package(self):
        package={'match_id':'0','plan_sha256':self.plan['record_sha256']}
        for role in ('current','candidate'):
            rec=self.row[role+'_record'];path=self.root/(role+'.json');path.write_text(json.dumps(rec))
            package[role+'_receipt']={'path':str(path),'sha256':rec['record_sha256']}
        return package

    def result(self):
        f=self.row['current_record']['fixture'];observed=datetime.now(timezone.utc).isoformat()
        raw={'header':{'id':'0','league':{'slug':'eng.1'},'competitions':[{'date':f['kickoff_at'],
             'status':{'period':2,'type':{'completed':True,'name':'STATUS_FULL_TIME'}},
             'competitors':[{'homeAway':'home','team':{'id':'A'},'score':'1'},
                            {'homeAway':'away','team':{'id':'B'},'score':'0'}]}]}}
        # ESPN adapter uses provider IDs; adapt synthetic package to that convention.
        self.plan['payload']['eligible_match_ids']=['espn:0'];seal(self.plan)
        for role in ('current','candidate'):
            rec=self.row[role+'_record'];rec['fixture']['match_id']='espn:0'
            p=rec['payload']['current_model'] if role=='current' else rec['payload']
            p['input']=copy.deepcopy(rec['fixture']);p['input_sha256']=digest(p['input'])
            if role=='current':rec['payload']['execution_audit']['fixture_sha256']=digest(p['input'])
            seal(rec)
        path=self.root/'raw.json';path.write_text(json.dumps(raw))
        return {'match_id':'espn:0','event_status':'FT','score_basis':'regulation_90','observed_at':observed,
                'home_goals':1,'away_goals':0,'evidence':{'file':'raw.json','sha256':sha(path),'identity_verified':True,
                'query_time':observed,'url':'https://site.api.espn.com/synthetic'}}

    def test_pending_and_missing_retain_entire_preregistered_population(self):
        self.plan['payload']['eligible_match_ids']=['0','missing'];seal(self.plan)
        result=review(self.plan,[self.package()],[])
        self.assertEqual(result['population'],2);self.assertEqual(result['completed'],0)
        self.assertEqual(result['pending_results'],['0']);self.assertEqual(result['missing_first_pairs'],['missing'])
        self.assertFalse(result['automatic_promotion'])

    def test_live_result_is_pending_even_with_numeric_score(self):
        result=review(self.plan,[self.package()],[{'match_id':'0','event_status':'LIVE','home_goals':1,'away_goals':0}])
        self.assertEqual(result['pending_results'],['0'])
        self.assertEqual(result['independent_denominators']['candidate']['primary_n'],0)

    def test_direction_and_primary_have_independent_denominators(self):
        result=self.result();package=self.package();package['match_id']='espn:0'
        report=review(self.plan,[package],[result],self.root)
        self.assertEqual(report['completed'],1)
        self.assertEqual(report['independent_denominators']['current']['primary_n'],1)
        self.assertEqual(report['independent_denominators']['current']['wdl_n'],0)
        self.assertEqual(report['independent_denominators']['candidate']['wdl_hits'],1)
        self.assertEqual(report['independent_denominators']['candidate']['actual_line_n'],0)
        self.assertEqual(report['promotion_review']['status'],'BLOCKED')

    def test_retained_result_tampering_and_non_ft_source_are_rejected(self):
        result=self.result();package=self.package();package['match_id']='espn:0'
        (self.root/'raw.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'checksum'):review(self.plan,[package],[result],self.root)

    def test_result_requires_retained_source_not_supplied_score_alone(self):
        result=self.result();package=self.package();package['match_id']='espn:0'
        with self.assertRaisesRegex(ValueError,'retained result evidence'):review(self.plan,[package],[result])

    def test_pending_pair_still_requires_same_cutoff_and_locked_artifacts(self):
        self.row['candidate_record']['fixture']['cutoff_at']=datetime.now(timezone.utc).isoformat();seal(self.row['candidate_record'])
        with self.assertRaisesRegex(ValueError,'same fixture'):review(self.plan,[self.package()],[])

    def test_quote_query_time_does_not_replace_missing_real_quote_time(self):
        f=copy.deepcopy(self.row['current_record']['fixture']);cutoff=f['cutoff_at']
        q={'verified':True,'match_id':f['match_id'],'bookmaker':'synthetic','market':'asian_total','line':2.25,
           'over_odds':1.9,'under_odds':1.9,'source_url':'https://example.test/quote','query_time':cutoff,'quoted_at':None}
        f['total_quote']=q;self.assertFalse(quote_audit(f)['eligible'])
        q['quoted_at']=cutoff;self.assertTrue(quote_audit(f)['eligible'])
        q['quoted_at']=(datetime.fromisoformat(cutoff)-timedelta(hours=2)).isoformat()
        self.assertFalse(quote_audit(f)['eligible'])

    def test_five_way_actual_line_uses_same_quote_with_current_metadata(self):
        result=self.result()
        for role in ('current','candidate'):
            rec=self.row[role+'_record'];f=rec['fixture'];q={'verified':True,'match_id':f['match_id'],'bookmaker':'synthetic',
                'market':'asian_total','line':1.25,'over_odds':1.9,'under_odds':1.9,'source_url':'https://example.test/quote',
                'query_time':f['cutoff_at'],'quoted_at':f['cutoff_at']}
            f['total_quote']=q;p=rec['payload']['current_model'] if role=='current' else rec['payload']
            p['input']=copy.deepcopy(f);p['input_sha256']=digest(f)
            p['total']={'quote':dict(q,quoted_at_status='KNOWN'),'reference_direction':'over'}
            if role=='current':rec['payload']['execution_audit']['fixture_sha256']=digest(f)
            seal(rec)
        package=self.package();package['match_id']='espn:0'
        report=review(self.plan,[package],[result],self.root)
        for role in ('current','candidate'):
            self.assertEqual(report['independent_denominators'][role]['actual_line_n'],1)
            self.assertEqual(report['independent_denominators'][role]['actual_line_five_way'],{'half_loss':1})

    def test_abstention_is_retained_in_existing_freeze_audit(self):
        rec=self.row['current_record'];rec['match_id']='0';rec['previous_sha256']=None
        rec['payload']={'current_model':{'status':'ABSTAIN','primary':None,'matrix':None},
                        'test_model':{'status':'ABSTAIN','primary':None,'matrix':None}}
        seal(rec);folder=self.root/'ledger';folder.mkdir();(folder/'000001.json').write_text(json.dumps(rec))
        report=audit_existing(self.root)
        self.assertEqual(report['first_pair_population'],1)
        self.assertEqual(report['chain_or_record_errors'],[])
        self.assertFalse(report['rows'][0]['models']['current_model']['primary_selected'])

    def test_lock_detects_registry_and_artifact_mutation(self):
        current=self.root/'current.json';current.write_text(json.dumps({'anchor':{'version':'BASE'}}))
        registry=self.root/'registry.json';registry.write_text(json.dumps({'models':{'eng.1':{'artifact_path':str(current),'artifact_sha256':sha(current)}}}))
        candidate=self.root/'candidate.json';candidate.write_text(json.dumps({'schema':'WCPP-DYNAMIC-FORWARD-CANDIDATE-1',
            'version':'CAND','cohort':'eng.1','distribution':{'family':'poisson','parameter':None},'model':{'config':CONFIG,'stage':'regular'}}))
        protocol=lock_protocol(registry,[candidate],self.root/'protocol.json');check_protocol(protocol)
        candidate.write_text('{}')
        with self.assertRaisesRegex(ValueError,'candidate artifact'):check_protocol(protocol)

    def test_registration_cannot_accept_finished_target(self):
        with patch('research_program.future_validation.check_protocol'):
            with self.assertRaisesRegex(ValueError,'unstarted'):
                register({'cohorts':{}},[self.row['current_record']['fixture']],self.root/'plans.json',self.root/'freezes')

    def test_capture_rejects_historical_rerun_before_inference(self):
        protocol={'protocol_sha256':'p'};self.plan['payload']['protocol_sha256']='p'
        self.plan['payload']['fixtures_identity']=[{k:self.row['current_record']['fixture'][k] for k in ('match_id','cohort','stage','kickoff_at','home','away')}];seal(self.plan)
        with patch('research_program.future_validation.check_protocol'):
            with self.assertRaisesRegex(ValueError,'future cutoff'):
                capture(protocol,self.plan,self.row['current_record']['fixture'],self.root/'captured.json',self.root/'freezes')


if __name__ == '__main__':unittest.main()
