"""First-issue and registration regressions specific to the three-arm batch."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from research_program import three_arm_forward as forward


def quote(book, market):
    value = {'bookmaker': book, 'market': market, 'quoted_at': '2026-11-17T10:29:00Z',
             'odds': {'home': 2., 'draw': 3.3, 'away': 3.8} if market == 'h2h' else {'over': 1.9, 'under': 2.}}
    if market == 'totals':
        value.update(line=2.75, quoted_at='2026-11-17T10:28:00Z')
    return {'field': 'market_quote', 'provider': 'theoddsapi', 'value': value,
            'provider_match_id': 'abc', 'match_id': 'espn:1', 'observed_at': '2026-11-17T10:29:56Z',
            'evidence': {'sha256': 'a' * 64, 'url': 'https://example.test/odds'}}


class ForwardTests(unittest.TestCase):
    def test_same_book_separate_timestamps_and_no_crossed_markets(self):
        snapshot = {'snapshot_sha256': 'b' * 64, 'observations': [
            quote(book, market) for book in ('beta', 'pinnacle') for market in ('h2h', 'totals')]}
        selected = forward.market_pair(snapshot)
        self.assertEqual(selected['bookmaker'], 'pinnacle')
        self.assertEqual(selected['total']['line'], 2.75)
        self.assertNotEqual(selected['h2h_quoted_at'], selected['total_quoted_at'])
        snapshot['observations'].append(quote('pinnacle', 'totals'))
        self.assertEqual(forward.market_pair(snapshot)['bookmaker'], 'beta')
        snapshot['observations'] = [quote('pinnacle', 'h2h'), quote('beta', 'totals')]
        self.assertIsNone(forward.market_pair(snapshot))
        self.assertIsNone(forward.market_pair(None))

    def test_registered_population_includes_abstentions_and_replays_only_first_issue(self):
        now = datetime.now(timezone.utc)
        kickoff = (now + timedelta(days=1)).isoformat()
        enrolled = [{'match_id': 'espn:' + str(i), 'home': 'espn:h' + str(i), 'away': 'espn:a' + str(i),
                     'stage': 'regular', 'neutral': False, 'cohort': 'bra.1', 'kickoff_at': kickoff,
                     'identity_sha256': str(i) * 64} for i in (1, 2)]
        protocol = {'schema': 'THREE-ARM-RESEARCH-REGISTRATION-1', 'arms': list(forward.ARMS),
                    'fixtures': enrolled, 'recorded_at': now.isoformat(), 'automatic_promotion': False}
        def batch(root, identity_dir, competition, cutoff):
            fixtures = [dict(row, cutoff_at=cutoff, sources=[{'response_sha256': row['identity_sha256']}])
                        for row in enrolled]
            return fixtures, {row['match_id']: None for row in enrolled}, {}
        fixtures = batch(None, None, None, now.isoformat())[0]
        with self.assertRaisesRegex(ValueError, 'population'):
            forward.verify_registration(protocol, fixtures[1:], now.isoformat())
        with self.assertRaisesRegex(ValueError, 'identity'):
            forward.verify_registration(protocol, [dict(fixtures[0], neutral=True), fixtures[1]], now.isoformat())
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            out = root / 'batch'
            (root / 'market-channel.json').write_text('{}')
            (root / 'artifact.json').write_text(json.dumps({'cutoff_at': now.isoformat()}))
            (root / 'registry.json').write_text('{}')
            (root / 'registration.json').write_text(json.dumps(protocol))
            def run():
                return forward.capture(root, 'bra.1', root / 'artifact.json', root / 'registry.json', out,
                                       registration=root / 'registration.json')
            with patch.object(forward, 'batch_inputs', side_effect=batch), patch.object(
                    forward, 'forecast', return_value={'status': 'ABSTAIN_CURRENT_UNAVAILABLE', 'arms': {}}) as infer:
                first = run()
                self.assertEqual(infer.call_count, 2)
                self.assertEqual(first['population'], 2)
                self.assertTrue(all(row['status'] == 'ABSTAIN_CURRENT_UNAVAILABLE' for row in first['matches']))
                # A crash between committing the canonical freeze and its public mirror is recoverable.
                (out / first['matches'][0]['file']).unlink()
                with patch.object(forward, 'datetime') as clock:
                    clock.now.return_value = now + timedelta(days=2)
                    self.assertEqual(first, run())
                self.assertEqual(infer.call_count, 2)
                self.assertEqual(len(list((out / 'records').glob('*/*.json'))), 3)
                self.assertTrue((out / first['matches'][0]['file']).is_file())
                plan = forward.verify(forward.read(out / 'plan.json'))
                self.assertEqual(plan['payload']['registration_sha256'], forward.file_sha256(root / 'registration.json'))
                (root / 'artifact.json').write_text(json.dumps({'cutoff_at': now.isoformat(), 'different': True}))
                with self.assertRaisesRegex(ValueError, 'changed'):
                    run()


if __name__ == '__main__':
    unittest.main()
