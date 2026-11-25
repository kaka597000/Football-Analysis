"""Score-set diversity preserves the first recommendation and reports its cost."""

import unittest

import numpy as np

from research_program.score_coverage_policy import select_scores


def pmf(probabilities):
    matrix = np.zeros((6, 6))
    for coordinates, probability in probabilities.items():
        matrix[coordinates] = probability
    return matrix / matrix.sum()


class CoveragePolicyTests(unittest.TestCase):
    def setUp(self):
        self.fixture = {
            'match_id': 'espn:1',
            'kickoff_at': '2026-02-01T11:00:00Z',
            'cutoff_at': '2026-02-01T10:30:00Z',
            'total_quote': {
                'match_id': 'espn:1', 'verified': True, 'market': 'asian_total', 'line': 3.25,
                'bookmaker': 'example', 'source_url': 'https://example.org/event/1',
                'over_odds': 1.90, 'under_odds': 1.95,
                'quoted_at': '2026-02-01T10:15:00Z', 'query_time': '2026-02-01T10:20:00Z',
            },
        }
        self.m1 = pmf({
            (1, 1): .17, (2, 1): .15, (1, 2): .13, (3, 1): .12, (2, 2): .10,
            (3, 2): .07, (1, 0): .12, (2, 0): .09, (0, 1): .05,
        })
        self.m2 = pmf({
            (1, 1): .17, (2, 1): .15, (1, 2): .13, (3, 1): .12, (2, 2): .10,
            (3, 2): .10, (1, 0): .09, (2, 0): .09, (0, 1): .05,
        })

    def test_verified_line_can_support_probable_high_total_backup(self):
        out = select_scores([self.m1, self.m2], primary='1-1', policy='diverse', fixture=self.fixture)
        self.assertEqual(out['primary'], '1-1')
        self.assertEqual(out['alternates'], ['2-1', '3-1'])
        self.assertAlmostEqual(out['pooled_probability_loss'], .01)
        self.assertEqual(out['verified_total_line'], 3.25)
        high = out['path_evidence']['total_at_least_four']
        self.assertTrue(high['supported'])
        self.assertFalse(high['covered_in_reference'])
        self.assertTrue(high['covered_in_selection'])

    def test_require_agreement_without_valid_market_when_distributions_differ(self):
        absent = select_scores([self.m1, self.m2], policy='diverse')
        self.assertEqual(absent['alternates'], ['2-1', '1-2'])
        self.fixture['total_quote']['match_id'] = 'espn:other'
        invalid = select_scores([self.m1, self.m2], policy='diverse', fixture=self.fixture)
        self.assertEqual(invalid['alternates'], absent['alternates'])
        self.assertIsNone(invalid['verified_total_line'])

    def test_stale_or_postkickoff_line_is_ignored(self):
        for cutoff in ('2026-02-01T10:15:30Z', '2026-02-01T11:10:00Z'):
            self.fixture['cutoff_at'] = cutoff
            with self.subTest(cutoff=cutoff):
                out = select_scores([self.m1, self.m2], policy='diverse', fixture=self.fixture)
                self.assertIsNone(out['verified_total_line'])
                self.assertEqual(out['alternates'], ['2-1', '1-2'])
                self.assertTrue(out['quote_ineligibility'])

    def test_probability_mass_budget_protects_the_original_backups(self):
        for policy, kwargs in [('probability', {}), ('diverse', {'mass_budget': .005})]:
            out = select_scores([self.m1, self.m2], primary='1-1', policy=policy, fixture=self.fixture, **kwargs)
            self.assertEqual(out['alternates'], ['2-1', '1-2'])
            self.assertAlmostEqual(out['pooled_probability_loss'], 0.)

    def test_goalless_path_needs_positive_model_support_and_preserves_first(self):
        m = pmf({
            (1, 1): .19, (2, 1): .16, (1, 2): .15, (0, 1): .13, (0, 0): .10,
            (0, 2): .09, (2, 0): .09, (3, 1): .09,
        })
        out = select_scores([m], primary='1-1', policy='diverse')
        self.assertEqual((out['primary'], out['alternates']), ('1-1', ['2-1', '0-1']))
        self.assertTrue(out['path_evidence']['home_zero']['covered_in_selection'])
        self.assertAlmostEqual(out['pooled_probability_loss'], .02)

    def test_backup_replacement_preserves_existing_supported_path(self):
        m = pmf({
            (1, 1): .20, (2, 1): .16, (0, 1): .14, (3, 1): .12, (2, 2): .10,
            (3, 2): .08, (0, 2): .06, (0, 3): .06, (0, 4): .04, (1, 0): .04,
        })
        out = select_scores([m], primary='1-1', policy='diverse')
        self.assertTrue(out['path_evidence']['home_zero']['covered_in_selection'])
        self.assertTrue(out['path_evidence']['total_at_least_four']['supported'])
        self.assertEqual(out['alternates'], ['2-1', '0-1'])

    def test_stable_order_rejects_invalid_inputs_and_never_repeats_primary(self):
        m = np.full((2, 2), .25)
        out = select_scores([m], primary='1-1')
        self.assertEqual((out['primary'], out['alternates']), ('1-1', ['0-0', '0-1']))
        for kwargs in [{'primary': '1-01'}, {'primary': '1-2'}, {'policy': 'unknown'}, {'mass_budget': 1.}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                select_scores([m], **kwargs)
        with self.assertRaises(ValueError):
            select_scores([m, self.m2])


if __name__ == '__main__':
    unittest.main()
