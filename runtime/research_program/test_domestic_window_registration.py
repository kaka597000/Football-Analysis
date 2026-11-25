import gzip
import json
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from research_program import domestic_window_registration as windows
from research_program.r8_score_study import read


class DomesticWindowsTests(TestCase):
    def test_retains_a_suspended_first_half_on_its_resumption_calendar(self):
        fixture = dict(match_id='espn:42', home='espn:3', away='espn:4', kickoff_at='2026-12-09T19:00Z')
        status = dict(type=dict(name='STATUS_SUSPENDED', state='post'), clock=1980, period=1)
        teams = [dict(homeAway=side, team={'id': fixture[side][5:]}) for side in ('home', 'away')]
        game = dict(id='42', date=fixture['kickoff_at'], timeValid=True, status=status, competitors=teams)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = json.dumps({'events': [dict(id='42', date=game['date'], status=status, competitions=[game])]}).encode()
            source = root / 'board.json.gz'
            source.write_bytes(gzip.compress(raw))
            proof = root / 'manifest.json'
            entry = dict(competition='esp.1', source_provider='ESPN', compressed_path=source.name,
                         compressed_sha256=windows.sha(source), body_sha256=sha256(raw).hexdigest(),
                         observed_response_end_utc='2026-11-25T11:39:13Z')
            windows.atomic_json(proof, {'captures': [entry]})
            exceptions = windows.prematch_exceptions('esp.1', [fixture], proof)
            self.assertEqual(exceptions['espn:42']['game_clock_seconds'], 33 * 60)
            self.assertEqual(exceptions['espn:42']['source_sha256'], entry['body_sha256'])

    def test_midnight_boundary_and_each_competitions_actual_calendar_count(self):
        spec = read(windows.PROTOCOL)
        def fixture(n, start):
            return dict(cohort='esp.1', stage='regular', match_id='espn:'+str(n), home='espn:3', away='espn:4',
                        kickoff_at=start)
        fixtures = [fixture(n, '2026-11-28T01:00Z') for n in range(12)]
        fixtures += [fixture(17, '2026-12-03T16:00Z')]
        self.assertEqual(windows.sha(windows.PROTOCOL), windows.PROTOCOL_SHA)
        groups, coverage = windows.slates('esp.1', fixtures, spec)
        self.assertEqual(groups['dec04_dec11'], ['espn:17'])
        self.assertEqual(coverage['nov27_dec04']['strict_lines'], 10)
        self.assertEqual(coverage['nov27_dec04']['nonpush'], 9)
        with self.assertRaisesRegex(ValueError, 'outside'):
            windows.slates('esp.1', [*fixtures, fixture(18, '2026-12-10T16:00Z')], spec)
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            windows.slates('esp.1', [*fixtures, fixtures[0]], spec)

    def test_interrupted_first_keeps_the_original_registered_weights(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            fixture = dict(cohort='esp.1', stage='regular', match_id='espn:1', home='espn:3',
                           away='espn:4', kickoff_at='2026-11-28T01:00:00Z')
            catalog, proof, goals, scope, registry = [root / (key + '.json') for key in
                                                     ('catalog', 'proof', 'goals', 'scope', 'registry')]
            windows.atomic_json(catalog, {'esp.1': [fixture]})
            windows.atomic_json(proof, {'source': 'calendar'})
            windows.atomic_json(scope, {'competitions': [{'code': 'esp.1', 'requires_phase_identity': False}]})
            windows.atomic_json(goals, {'research_roadmap_20261124': {'stages': [1, 2, 3, 4]}})
            original = {'registry_sha256': 'original', 'native_models': {}}
            args = ('esp.1', catalog, proof, goals, scope, registry, root / 'archive')
            with patch.object(windows, 'native_model_inputs', return_value=original), \
                    patch.object(windows, 'prematch_exceptions', return_value={}), \
                    patch.object(windows.ledger, 'register', side_effect=OSError('interrupted')):
                with self.assertRaisesRegex(OSError, 'interrupted'):
                    windows.register(*args, freeze_root=root / 'freezes')
            def publish(fixtures, identity, track, folder, *, freeze_root):
                folder.mkdir(parents=True, exist_ok=True)
                return {'id': 'first'}
            with patch.object(windows.ledger, 'register', side_effect=publish) as restored:
                windows.register(*args, freeze_root=root / 'freezes')
            self.assertEqual(restored.call_args.args[1]['model_inputs'], original)
            self.assertEqual(read(root / 'archive/esp.1/registration/plan.json'), {'id': 'first'})
