"""Scoring must preserve frozen identity and separate forward evidence."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

spec = importlib.util.spec_from_file_location('alpha_score', Path(__file__).resolve().parents[2] / 'scripts/score_frozen_alpha_grid.py')
score = importlib.util.module_from_spec(spec)
spec.loader.exec_module(score)


class AlphaScoreTests(unittest.TestCase):
    def inputs(self, future=False):
        now = datetime.now(timezone.utc)
        fixture = {'match_id':'espn:123456789', 'cohort':'eng.1',
                   'kickoff_at':(now+timedelta(days=2 if future else -2)).isoformat(),
                   'cutoff_at':(now-timedelta(days=4)).isoformat(), 'home':'espn:1', 'away':'espn:2'}
        dc = np.array([[.4,.1],[.2,.3]]); ml = np.array([[.2,.3],[.1,.4]])
        original = {'freeze_id':'pair-1', 'track':'current-test-pair', 'stage':'prematch',
                    'evaluation_slot':'FIRST_PREMATCH', 'match_id':fixture['match_id'], 'fixture':fixture,
                    'recorded_at':fixture['cutoff_at'],
                    'payload':{'current_model':{'matrix':dc.tolist()}, 'test_model':{'matrix':((dc+ml)/2).tolist()}}}
        original['record_sha256'] = score.digest(original)
        metric = lambda m: {'matrix_sha256':score.digest(m.tolist())}
        row = dict(match_id=fixture['match_id'], cohort=fixture['cohort'], kickoff_at=fixture['kickoff_at'],
                   input_cutoff_at=fixture['cutoff_at'], first_freeze_id='pair-1',
                   first_freeze_sha256=original['record_sha256'], original_alpha=.5, future=future,
                   current=metric(dc), test=metric((dc+ml)/2), components={'dc':dc.tolist(), 'ml':ml.tolist()},
                   alphas={str(a):metric((1-a)*dc+a*ml) for a in [0,.25,.5,.75,1]})
        protocol = {'created_at':now.isoformat(), 'population':[{'match_id':row['match_id'],
                    'record_sha256':original['record_sha256'], 'future':future}]}
        records = {'pair-1':(None, original)}
        if future:
            candidate = dict(original, freeze_id='candidate-1', track='alpha-grid-candidate',
                             fixture=dict(fixture, cutoff_at=now.isoformat()), recorded_at=now.isoformat(),
                             payload=deepcopy(row))
            candidate.pop('record_sha256'); candidate['record_sha256'] = score.digest(candidate)
            records['candidate-1'] = (None, candidate)
            row['candidate_freeze'] = {'freeze_id':'candidate-1', 'sha256':candidate['record_sha256'],
                                       'recorded_at':candidate['recorded_at']}
        return [row], records, protocol

    def test_first_freeze_hash_fixture_and_matrix_binding(self):
        mutations = [lambda r:r.update(first_freeze_sha256='0'*64),
                     lambda r:r.update(cohort='ita.1'),
                     lambda r:r.update(input_cutoff_at='2020-01-01T00:00:00Z'),
                     lambda r:r['components']['dc'][0].__setitem__(0,.5),
                     lambda r:r['alphas']['0'].update(matrix_sha256='0'*64)]
        for mutate in mutations:
            rows, records, protocol = self.inputs(); mutate(rows[0])
            with self.assertRaises(ValueError): score.validate_predictions(rows, records, protocol)

    def test_duplicates_and_segment_relabeling_rejected(self):
        rows, records, protocol = self.inputs()
        with self.assertRaisesRegex(ValueError,'duplicate'): score.validate_predictions(rows*2, records, protocol)
        rows[0]['future'] = True
        with self.assertRaisesRegex(ValueError,'segment'): score.validate_predictions(rows, records, protocol)

    def test_future_candidate_payload_must_match(self):
        rows, records, protocol = self.inputs(True)
        score.validate_predictions(rows, records, protocol)
        rows[0]['extra_postfreeze_change'] = True
        with self.assertRaisesRegex(ValueError,'candidate'): score.validate_predictions(rows, records, protocol)

    def test_pending_future_keeps_own_denominator_and_verifies_checksum(self):
        rows, records, protocol = self.inputs(True)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); forecasts = root/'forecasts'; forecasts.mkdir()
            for key,(_,record) in records.items():
                folder=forecasts/key; folder.mkdir(); (folder/'000001.json').write_text(json.dumps(record))
            path=root/'predictions.jsonl'; path.write_text(json.dumps(rows[0])+'\n')
            (root/'protocol.json').write_text(json.dumps(protocol))
            result=score.evaluate(path, [], forecasts)
            self.assertEqual(result['segments']['future_frozen']['ALL']['population'],1)
            self.assertEqual(result['segments']['future_frozen']['ALL']['settled'],0)
            self.assertEqual(result['segments']['historical_reconstruction']['ALL']['population'],0)
            original=records['pair-1'][1]; original['fixture']['home']='forged'
            (forecasts/'pair-1/000001.json').write_text(json.dumps(original))
            with self.assertRaisesRegex(ValueError,'checksum'): score.evaluate(path, [], forecasts)


if __name__ == '__main__':
    unittest.main()
