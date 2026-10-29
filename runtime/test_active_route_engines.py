import hashlib
import json
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from active_route_engines import align_matrix_support


REGISTRY = Path('/workspace/WCPP/runtime/active-model.json')


class ActiveRouteEngineTests(unittest.TestCase):
    @unittest.skipUnless(REGISTRY.is_file(), 'external active registry required')
    def test_four_requested_routes_are_hash_locked(self):
        registry = json.loads(REGISTRY.read_text())
        self.assertEqual(registry['version'], 'WCPP-DUAL-4')
        for cohort in ('jpn.1', 'kor.1', 'aus.1',
                       'afc.champions:ELITE:league_phase'):
            route = registry['models'][cohort]
            for prefix in ('', 'test_'):
                path = Path(route[prefix + 'artifact_path'])
                self.assertTrue(path.is_file())
                actual = hashlib.sha256(path.read_bytes()).hexdigest()
                self.assertEqual(actual, route[prefix + 'artifact_sha256'])

    @unittest.skipUnless(REGISTRY.is_file(), 'external active registry required')
    def test_afc_elite_route_is_phase_isolated(self):
        route = json.loads(REGISTRY.read_text())['models'][
            'afc.champions:ELITE:league_phase']
        self.assertEqual(route['scope'],
                         'AFC_ELITE_ADULT_MEN_LEAGUE_PHASE_ONLY')
        self.assertIn('LEGACY_CHAMPIONS', route['excluded'])
        self.assertIn('afc.champions_two', route['excluded'])

    @unittest.skipUnless(Path('/workspace/WCPP/models/active-routes-20261027/afc.champions-ELITE-league_phase').is_dir(), 'external AFC artifacts required')
    def test_today_afc_provider_team_map_has_no_cold_start_target(self):
        root = Path('/workspace/WCPP/models/active-routes-20261027/'
                    'afc.champions-ELITE-league_phase')
        mapping = json.loads((root / 'apifootballcom-team-map.json').read_text())
        artifact = json.loads((root / 'test-artifact.json').read_text())
        known = set(artifact['core']['teams'])
        self.assertEqual(len(mapping['mappings']), 32)
        self.assertFalse(mapping['unmapped_provider_team_ids'])
        self.assertTrue(all(x['canonical_team_id'] in known
                            for x in mapping['mappings'].values()))

    def test_zero_padding_preserves_all_probability_values(self):
        a = np.asarray([[.4, .1], [.2, .3]])
        b = np.zeros((3, 3)); b[:2, :2] = a
        current = {'matrix': a.tolist(), 'matrix_hash': 'old'}
        test = {'matrix': b.tolist(), 'matrix_hash': 'old'}
        audit = align_matrix_support(current, test)
        self.assertEqual(audit['status'], 'ZERO_PAD_APPLIED')
        self.assertEqual(np.asarray(current['matrix']).shape, (3, 3))
        np.testing.assert_array_equal(np.asarray(current['matrix'])[:2, :2], a)
        self.assertEqual(float(np.asarray(current['matrix']).sum()), 1.0)
        self.assertFalse(audit['probability_values_changed'])


if __name__ == '__main__':
    unittest.main()
