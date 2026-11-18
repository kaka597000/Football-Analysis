from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from research_program import sequence_forward as s


def put(path, value):
    path.write_text(json.dumps(value))


class SequenceForwardTests(unittest.TestCase):
    def test_team_panel_attaches_cutoff_checked_final_scores_in_team_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def event(key, date, home, away, score):
                teams = [{'homeAway': k, 'team': {'id': team}, 'score': {'value': v}}
                         for k, team, v in zip(('home', 'away'), (home, away), score)]
                c = {'id': key, 'date': date, 'competitors': teams, 'status': {
                    'type': {'name': 'STATUS_FULL_TIME', 'completed': True, 'state': 'post'}, 'period': 2}}
                return {'id': key, 'date': date, 'competitions': [c], 'league': {'slug': 'bra.1'}, 'season': {'year': 2026}}
            d = {'status': 'success', 'team': {'id': '9'}, 'season': {'year': 2026}, 'requestedSeason': {'year': 2026},
                 'events': [event('b', '2026-11-08T22:00Z', '2', '9', (1, 3)),
                            event('a', '2026-11-01T00:00Z', '9', '2', (4, 1)),
                            event('c', '2026-11-18T00:00Z', '9', '2', (1, 0))]}
            put(root / '9.json', d)
            ref = {'url': 'https://site.api.espn.com/apis/site/v2/sports/soccer/bra.1/teams/9/schedule?season=2026',
                   'file': '9.json', 'sha256': s.file_sha256(root / '9.json'),
                   'requested_at': '2026-11-18T02:50Z', 'captured_at': '2026-11-18T02:51Z'}
            put(root / '9.meta.json', ref)
            f = {'kickoff_at': '2026-11-18T23:30Z', 'cutoff_at': '2026-11-18T03:00Z'}
            evidence = s.team_schedule(root, 'espn:9', fixture=f, seen={})
            self.assertEqual(evidence['features'], [[0] * 4] * 3 + [[4, 1, 1, 1], [3, 1, 0, 1]])
            self.assertEqual([m['match_id'] for m in evidence['matches']], ['espn:a', 'espn:b'])
            with self.assertRaisesRegex(ValueError, 'time'):
                s.team_schedule(root, 'espn:9', fixture=dict(f, cutoff_at='2026-11-18T02:50Z'), seen={})
            with self.assertRaisesRegex(ValueError, 'conflicting'):
                s.team_schedule(root, 'espn:9', fixture=f, seen={'espn:a': {}})

    def test_repeat_freeze_and_pending_review_keep_the_original_population(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = datetime.now(timezone.utc)
            row = {'match_id': 'espn:77', 'home': 'espn:1', 'away': 'espn:2', 'cohort': 'bra.1', 'stage': 'regular',
                   'kickoff_at': (now + timedelta(days=3)).isoformat(), 'neutral': False, 'identity_sha256': 'e' * 64}
            put(root / 'registration.json', {'schema': 'WCPP-SEQUENCE-FORWARD-REGISTRATION-1', 'arms': list(s.ARMS),
                                             'recorded_at': now.isoformat(), 'fixtures': [row], 'automatic_promotion': False})
            put(root / 'market-channel.json', {})
            put(root / 'candidate.json', {'cutoff_at': now.isoformat()})
            put(root / 'calibration.json', {'cutoff_at': now.isoformat(), 'market_weight': 1.})
            put(root / 'registry.json', {})
            (root / 'normalized-results.jsonl').touch()
            out = root / 'batch'
            def candidates(channel, identity, competition, cutoff):
                f = dict(row, cutoff_at=cutoff, sources=[{'response_sha256': row['identity_sha256']}])
                return [f], {row['match_id']: None}, {}
            values = [[.3, .4], [.2, .1]]
            picks = s.market_model.score_picks(values)
            arms = {k: dict(picks, matrix=values, matrix_sha256=s.digest(values)) for k in s.ARMS}
            fake = {'status': 'ISSUED_NO_MARKET', 'native_current': {'matrix': values, 'matrix_sha256': s.digest(values)},
                    'arms': arms}
            def run():
                return s.capture(market_dir=root, identity_dir=root, history=root, registration=root / 'registration.json',
                                 artifact=root / 'candidate.json', market_artifact=root / 'calibration.json',
                                 registry=root / 'registry.json', out=out)
            with patch.object(s.market_forward, 'batch_inputs', side_effect=candidates), patch.object(
                    s, 'team_schedule', return_value={'features': [[0] * 4] * 5}), patch.object(s, 'predict', return_value=fake) as infer:
                first = run()
                (out / 'freezes/espn-77.json').unlink()
                self.assertEqual(first, run())
                self.assertEqual(infer.call_count, 1)
            r = s.publish_review(out, root)
            self.assertEqual((r['population'], r['issued'], r['pending'], r['settled']), (1, 1, 1, 0))
            self.assertEqual(r['arms']['lstm']['real_line_selections'], 0)
            self.assertEqual(s.publish_review(out, root), r)
            self.assertEqual(len(list((out / 'reviews').glob('*/*.json'))), 1)
            with patch.object(s.scored, '_results', return_value=({'espn:77': {'home': 0, 'away': 1}}, {})):
                settled = s.review(out, root)
                self.assertEqual(settled['arms']['lstm']['metrics']['score_top1']['hits'], 1)
                self.assertEqual(settled['arms']['lightgbm']['metrics']['wdl']['n'], 1)
                self.assertIsNone(settled['arms']['market_baseline']['metrics']['total_strict_full_win']['accuracy'])
            put(root / 'calibration.json', {'cutoff_at': now.isoformat(), 'changed': True})
            with self.assertRaisesRegex(ValueError, 'cannot change'):
                run()


if __name__ == '__main__':
    unittest.main()
