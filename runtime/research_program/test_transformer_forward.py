from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from research_program import transformer_forward as forward


class TransformerForwardTests(unittest.TestCase):
    def test_missing_market_keeps_enrollment_without_inference(self):
        with patch.object(forward.sequence, 'predict') as infer:
            result = forward.predict({}, None, [], {}, Path('unused'))
        self.assertEqual(result, {'status': 'ABSTAIN_NO_PAIRED_MARKET', 'arms': {}})
        infer.assert_not_called()

    def test_original_four_arms_are_independently_frozen_and_scored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'normalized-results.jsonl').touch()
            batch = root / 'future'
            kickoff = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
            mid = 'espn:10'
            quote = {'total': {'line': 2.5}}
            matrix = [[.05, .30, .05], [.02, .08, .02], [.10, .35, .03]]
            picks = forward.score.score_picks(matrix, line=2.5)
            arm = dict(picks, matrix=matrix, matrix_sha256=forward.digest(matrix))
            forecast = {'status': 'ISSUED_REAL_MARKET', 'arms': {a: arm for a in forward.ARMS}}
            def fixture(data, channel, artifacts, cutoff):
                f = {'match_id': mid, 'kickoff_at': kickoff, 'cutoff_at': cutoff,
                     'cohort':'bra.1', 'stage':'regular'}
                return {'fixtures': [f], 'markets': {mid: quote}, 'history': {mid: []}, 'sources': {}}
            with patch.object(forward, 'materials', return_value=({'registration':'a'}, {})), patch.object(
                    forward, 'inputs', side_effect=fixture), patch.object(forward,'predict',return_value=forecast) as infer:
                first = forward.capture(root, batch, root, root)
                (batch / 'freezes/espn-10.json').unlink()
                self.assertEqual(forward.capture(root, batch, root, root), first)
                self.assertEqual(infer.call_count, 1)
            self.assertEqual(set(first['matches'][0]['arms']), set(forward.ARMS))
            pending = forward.review(root, batch)
            self.assertEqual(pending['pending'], 1)
            self.assertIsNone(pending['arms']['transformer']['metrics']['score_top1']['accuracy'])
            self.assertEqual(pending, forward.review(root, batch))
            self.assertEqual(len(list((batch / 'reviews').glob('*/*.json'))), 1)
            with patch.object(forward, 'materials', return_value=({'registration':'a','different':True}, {})):
                with self.assertRaisesRegex(ValueError,'changed inputs'):
                    forward.capture(root,batch,root,root)
            score = {'home':2, 'away':1, 'source':{'captured_at': datetime.now(timezone.utc).isoformat()}}
            with patch.object(forward.settlement,'_results',return_value=({mid:score},{})):
                complete = forward.review(root,batch)
            self.assertEqual(len(complete['paired']), 6)
            self.assertEqual(complete['arms']['transformer']['metrics']['score_top1']['hits'], 1)
            self.assertEqual(complete['paired']['transformer_minus_lightgbm']['wdl']['net_hits'], 0)


if __name__ == '__main__':
    unittest.main()
