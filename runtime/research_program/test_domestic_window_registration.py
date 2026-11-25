from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from research_program import domestic_window_registration as windows
from research_program.r8_score_study import read


class DomesticWindowsTests(TestCase):
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
