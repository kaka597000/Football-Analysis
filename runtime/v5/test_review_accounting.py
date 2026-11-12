"""Review denominators must follow each frozen recommendation independently."""
import copy
import unittest
from paired_review import review
from weighted_model import legacy

class ReviewAccountingTests(unittest.TestCase):
    def fixture(self):
        fixture = {'match_id': 'synthetic', 'cutoff_at': '2020-01-01T00:00:00Z',
                   'kickoff_at': '2020-01-01T01:00:00Z'}
        m = [[0., 0.], [1., 0.]]
        f = {'input': fixture, 'matrix': m, 'matrix_hash': legacy.digest(m),
             'generated_at': fixture['cutoff_at'], 'primary': '1-0',
             'wdl_reference': '主胜', 'version': 'synthetic'}
        pair = {'match_id': 'synthetic', 'current_model': copy.deepcopy(f),
                'test_model': copy.deepcopy(f)}
        result = {'match_id': 'synthetic', 'home_goals': 1, 'away_goals': 0,
                  'event_status': 'FT',
                  'score_basis': 'regulation_90', 'source_url': 'https://example.test/result',
                  'observed_at': '2020-01-01T03:00:00Z'}
        return pair, result

    def test_missing_optional_total_does_not_block_review(self):
        pair, result = self.fixture()
        before = copy.deepcopy(pair)
        r = review([pair], [result])
        self.assertEqual(pair, before)
        s = r['models']['当前模型']
        self.assertEqual((s['primary_hits'], s['primary_eligible'], s['total_eligible']), (1, 1, 0))

    def test_direction_counts_when_primary_abstains(self):
        pair, result = self.fixture()
        pair['current_model']['primary'] = None
        pair['test_model']['wdl_reference'] = '观望'
        r = review([pair], [result])
        current, test = r['models']['当前模型'], r['models']['test模型']
        self.assertEqual((current['settled'], current['primary_eligible'], current['primary_abstentions']), (1, 0, 1))
        self.assertEqual((current['wdl_hits'], current['wdl_eligible']), (1, 1))
        self.assertEqual((test['primary_hits'], test['wdl_eligible'], test['wdl_abstentions']), (1, 0, 1))
        self.assertIsNone(r['paired_rows'][0]['models']['当前模型']['primary_hit'])

    def test_unfinished_match_stays_in_scope(self):
        pair, _ = self.fixture()
        r = review([pair], [])
        self.assertEqual((r['scope'], r['finished'], r['pending_count']), (1, 0, 1))
        self.assertEqual(r['models']['当前模型']['primary_eligible'], 0)
        self.assertEqual(r['models']['当前模型']['population'], 1)
        self.assertEqual(r['models']['当前模型']['coverage']['primary'], 1.)

    def test_live_or_interrupted_result_never_becomes_a_win(self):
        pair,result=self.fixture()
        for status in ('LIVE','SUSPENDED','POSTPONED'):
            result['event_status']=status
            report=review([pair],[result])
            self.assertEqual(report['finished'],0);self.assertEqual(report['pending_count'],1)
            self.assertEqual(report['models']['当前模型']['primary_eligible'],0)
        result.pop('event_status')
        with self.assertRaisesRegex(ValueError,'explicit result status'):review([pair],[result])
        result.update(status='LIVE',event_status='FT')
        with self.assertRaisesRegex(ValueError,'conflicting result status'):review([pair],[result])

    def test_quarter_lines_keep_all_five_outcomes_and_strict_denominator(self):
        pairs=[];results=[]
        for i,line in enumerate((.25,.75,1.,1.25,1.5)):
            pair,result=self.fixture();mid=f'synthetic-{i}';pair['match_id']=result['match_id']=mid
            for name in ('current_model','test_model'):
                pair[name]['input']['match_id']=mid
                quote={'match_id':mid,'verified':True,'bookmaker':'SYNTHETIC','market':'asian_total',
                       'line':line,'over_odds':1.9,'under_odds':1.9,'source_url':'https://example.test/synthetic-quote',
                       'quoted_at':'2019-12-31T23:50:00Z','query_time':'2020-01-01T00:00:00Z'}
                pair[name]['input']['total_quote']=quote
                pair[name]['total']={'quote':copy.deepcopy(quote),'reference_direction':'over'}
            pairs.append(pair);results.append(result)
        report=review(pairs,results);current=report['models']['当前模型']
        self.assertEqual(current['total_settlements'],{'win':1,'half_win':1,'push':1,'half_loss':1,'loss':1})
        self.assertEqual(current['total_eligible'],5);self.assertEqual(current['total_non_push_eligible'],4)
        self.assertEqual(current['total_strict_full_win_rate'],.25)

    def test_total_abstention_does_not_remove_score_or_direction(self):
        pair,result=self.fixture()
        pair['current_model']['total']={'quote':{'line':2.25},'reference_direction':None}
        r=review([pair],[result])['models']['当前模型']
        self.assertEqual(r['primary_eligible'],1);self.assertEqual(r['wdl_eligible'],1)
        self.assertEqual(r['total_eligible'],0);self.assertIsNone(r['total_strict_full_win_rate'])

    def test_unknown_quote_time_cannot_count_as_actual_line_success(self):
        pair,result=self.fixture()
        quote={'match_id':'synthetic','verified':True,'bookmaker':'SYNTHETIC','market':'asian_total',
               'line':.25,'over_odds':1.9,'under_odds':1.9,'source_url':'https://example.test/quote',
               'query_time':'2020-01-01T00:00:00Z'}
        for name in ('current_model','test_model'):
            pair[name]['input']['total_quote']=quote
            pair[name]['total']={'quote':copy.deepcopy(quote),'reference_direction':'over'}
        r=review([pair],[result])['models']['当前模型']
        self.assertEqual(r['primary_hits'],1);self.assertEqual(r['wdl_hits'],1)
        self.assertEqual(r['total_eligible'],0);self.assertEqual(r['coverage']['total'],0.)
        self.assertIn('MISSING_OR_INVALID_REAL_QUOTE_TIME',r['total_ineligible_reasons'])

    def test_pending_new_version_retains_its_own_population(self):
        pair,result=self.fixture();pending=copy.deepcopy(pair);pending['match_id']='pending'
        for f in ('current_model','test_model'):pending[f]['input']['match_id']='pending'
        pending['test_model']['version']='next-version'
        report=review([pair,pending],[result]);test=report['models']['test模型']
        self.assertEqual(test['aggregation_status'],'MIXED_VERSIONS_NO_POOLED_ACCURACY')
        self.assertEqual(test['by_version']['next-version']['population'],1)
        self.assertEqual(test['by_version']['next-version']['pending'],1)
        self.assertIsNone(test['by_version']['next-version']['primary_accuracy'])

    def test_explicit_test_failure_keeps_current_settlement(self):
        pair,result=self.fixture();failed=pair['test_model']
        failed.update(status='ABSTAIN_TEST_EXECUTION_FAILED',matrix=None,primary=None,wdl_reference=None,total=None)
        failed.pop('matrix_hash')
        r=review([pair],[result])
        self.assertEqual(r['models']['当前模型']['primary_hits'],1)
        self.assertEqual(r['models']['test模型']['primary_eligible'],0)
        self.assertEqual(r['models']['test模型']['unavailable_settled'],1)
        failed['status']='ABSTAIN'
        self.assertEqual(review([pair],[result])['models']['test模型']['unavailable_settled'],1)
        failed['primary']='1-0'
        with self.assertRaisesRegex(ValueError,'without recommendations'):review([pair],[result])

    def test_both_unavailable_require_real_frozen_fixture_context(self):
        pair,result=self.fixture();pair['fixture']=pair['current_model']['input']
        for name in ('current_model','test_model'):
            pair[name]={'status':'ABSTAIN_NO_ACTIVE_ROUTE','matrix':None,'primary':None,'version':None}
        r=review([pair],[result])
        self.assertEqual(r['finished'],1);self.assertEqual(r['models']['当前模型']['primary_eligible'],0)
        self.assertEqual(r['models']['当前模型']['unavailable_settled'],1)
        pair.pop('fixture')
        with self.assertRaisesRegex(ValueError,'frozen fixture context'):review([pair],[result])

if __name__ == '__main__':
    unittest.main()
