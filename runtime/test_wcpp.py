import copy
import json
import math
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import wcpp

TIME = datetime(2030, 1, 1, 12, tzinfo=wcpp.UTC8)


def fixture():
    return {'match_id': 'synthetic-test', 'home': 'H', 'away': 'A',
            'competition': 'synthetic test league', 'phase': 'PreMatch',
            'cohort': 'MEN_TOP5_LEAGUE', 'identity_scope': 'extended',
            'cutoff_at': (TIME - timedelta(minutes=1)).isoformat(),
            'kickoff_at': (TIME + timedelta(hours=1)).isoformat(),
            'lambda_home': 1.5, 'lambda_away': 1.0, 'lambda_provenance': 'test only',
            'sources': [{'source_url': 'https://example.org/test', 'source_tier': 'L2',
                         'supports_field': ['fixture'], 'known_at': (TIME - timedelta(hours=1)).isoformat(),
                         'query_time': (TIME - timedelta(minutes=2)).isoformat()}]}


def parameters():
    return {'version': wcpp.VERSION, 'cohort': 'MEN_TOP5_LEAGUE',
            'cutoff_at': (TIME - timedelta(days=1)).isoformat(),
            'home_mean': 1.5, 'away_mean': 1.,
            'teams': {'H': {'attack': 1., 'concede': 1.}, 'A': {'attack': 1., 'concede': 1.}}}


class Tests(unittest.TestCase):
    def test_matrix_tail_partition(self):
        for lh, la in [(0.01, .01), (1.5, 1), (12, 12)]:
            p = wcpp.distribution(lh, la)
            self.assertAlmostEqual(sum(map(sum, p['matrix_0_to_7'])) + sum(p['tail_buckets_8_plus'].values()), 1.)
            self.assertAlmostEqual(sum(p['one_x_two'].values()), 1.)
            self.assertAlmostEqual(sum(p['total_goals']['over'].values()), 1.)

    def test_symmetric_outcomes(self):
        p = wcpp.distribution(1.4, 1.4)
        self.assertAlmostEqual(p['one_x_two']['主胜'], p['one_x_two']['客胜'])

    def test_analytic_probability(self):
        p = wcpp.distribution(1.5, 1.)
        self.assertEqual(p['primary'], '1-0')
        self.assertAlmostEqual(p['primary_probability'], 1.5 * math.exp(-2.5))
        self.assertAlmostEqual(p['total_goals']['under']['win'], math.exp(-2.5) * (1 + 2.5 + 2.5**2 / 2))

    def test_asian_integer_and_quarter(self):
        p2 = wcpp.distribution(1., 1., 2)
        exact2 = math.exp(-2) * 2
        self.assertAlmostEqual(p2['total_goals']['over']['push'], exact2)
        p225 = wcpp.distribution(1., 1., 2.25)
        self.assertAlmostEqual(p225['total_goals']['over']['half_loss'], exact2)
        self.assertAlmostEqual(p225['total_goals']['under']['half_win'], exact2)
        p275 = wcpp.distribution(1., 1., 2.75)
        self.assertAlmostEqual(p275['total_goals']['over']['half_win'], math.exp(-2) * 2**3 / 6)

    def test_bad_lambda(self):
        for value in [True, -1, float('nan'), float('inf'), 20]:
            with self.assertRaises(ValueError): wcpp.distribution(value, 1.)

    def test_cutoff_and_future_evidence(self):
        f = fixture(); f['cutoff_at'] = f['kickoff_at']
        with self.assertRaises(ValueError): wcpp.predict(f)
        f = fixture(); f['sources'][0]['known_at'] = TIME.isoformat()
        with self.assertRaises(ValueError): wcpp.predict(f)
        with self.assertRaises(ValueError): wcpp.timestamp('2030-01-01T12:00:00')

    def test_cohort_and_unknown_team(self):
        f = fixture(); f['cohort'] = 'UNCLASSIFIED'
        with self.assertRaises(ValueError): wcpp.predict(f)
        f = fixture(); f['home'] = 'unmapped'
        with self.assertRaises(ValueError): wcpp.apply_parameters(f, parameters())

    def test_freeze_and_evaluate(self):
        f = wcpp.apply_parameters(fixture(), parameters())
        f.update(identity_verified=True, context_audit_complete=True)
        with patch('wcpp.now', return_value=TIME), tempfile.TemporaryDirectory() as directory:
            p = wcpp.predict(f)
            path = wcpp.freeze(p, directory, parameters())
            saved = wcpp.read(path)
            self.assertFalse(saved['formal_validation'])
            self.assertEqual(saved['freeze_status'], 'CHALLENGER_RESEARCH_FREEZE')
            with self.assertRaises(FileExistsError): wcpp.freeze(p, directory, parameters())
            result = {'match_id': f['match_id'], 'score_basis': 'regulation_90',
                      'home_goals': 1, 'away_goals': 0, 'known_at': (TIME + timedelta(hours=3)).isoformat(),
                      'source_url': 'https://example.org/result'}
            with patch('wcpp.now', return_value=TIME + timedelta(hours=4)):
                self.assertTrue(wcpp.evaluate(saved, result)['primary_hit'])
                saved['prediction']['primary'] = '9-9'
                with self.assertRaises(ValueError): wcpp.evaluate(saved, result)

    def test_freeze_rejects_tampering_late_and_demo(self):
        f = wcpp.apply_parameters(fixture(), parameters())
        f.update(identity_verified=True, context_audit_complete=True)
        with patch('wcpp.now', return_value=TIME), tempfile.TemporaryDirectory() as directory:
            p = wcpp.predict(f)
            bad = copy.deepcopy(p); bad['prediction']['primary'] = '0-0'
            with self.assertRaises(ValueError): wcpp.freeze(bad, directory, parameters())
            badparams = parameters(); badparams['home_mean'] = 2
            with self.assertRaises(ValueError): wcpp.freeze(p, directory, badparams)
            f['demonstration'] = True
            with self.assertRaises(ValueError): wcpp.freeze(wcpp.predict(f), directory, parameters())
            with patch('wcpp.now', return_value=TIME + timedelta(hours=2)):
                with self.assertRaises(ValueError): wcpp.freeze(p, directory, parameters())

    def test_fit_excludes_unavailable_results(self):
        rows = []
        for i in range(25):
            rows.append({'cohort': 'MEN_TOP5_LEAGUE', 'home': 'H', 'away': 'A',
                         'kickoff_at': (TIME - timedelta(days=i+2)).isoformat(),
                         'result_known_at': (TIME - timedelta(days=i+1)).isoformat(),
                         'score_basis': 'regulation_90', 'home_goals': 2, 'away_goals': 1,
                         'source_url': 'https://example.org/history'})
        future = dict(rows[0], result_known_at=(TIME + timedelta(days=1)).isoformat(), home_goals=20)
        a = wcpp.fit(rows, TIME.isoformat(), 'MEN_TOP5_LEAGUE')
        b = wcpp.fit(rows + [future], TIME.isoformat(), 'MEN_TOP5_LEAGUE')
        self.assertEqual(a, b)
        with self.assertRaises(ValueError): wcpp.fit(rows[:2], TIME.isoformat(), 'MEN_TOP5_LEAGUE')
        with self.assertRaises(ValueError): wcpp.fit(rows + [rows[0]], TIME.isoformat(), 'MEN_TOP5_LEAGUE')

    def test_shadow_projection(self):
        from shadow import project, regime, margin
        h, a = wcpp.pmf(1.6), wcpp.pmf(1.1)
        mass = sum(h[:8])*sum(a[:8])
        totals, margins = {}, {}
        for i in range(8):
            for j in range(8):
                prob = h[i]*a[j]/mass
                totals[regime(i,j)] = totals.get(regime(i,j), 0) + prob
                margins[margin(i,j)] = margins.get(margin(i,j), 0) + prob
        q = project(1.6, 1.1, totals, margins)
        self.assertTrue(q['converged'])
        self.assertEqual(q['role'], 'SHADOW_ONLY')
        for i in range(8):
            for j in range(8): self.assertAlmostEqual(q['conditional_matrix_0_to_7'][i][j], h[i]*a[j]/mass)


if __name__ == '__main__':
    unittest.main()
