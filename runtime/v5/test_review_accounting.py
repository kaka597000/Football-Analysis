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

if __name__ == '__main__':
    unittest.main()
