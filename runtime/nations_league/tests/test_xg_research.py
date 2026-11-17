import copy
from datetime import date, timedelta
import unittest

import numpy as np

from nations_league import xg_research as xg
from v2.model import fit_strength, rates


def history(n=72):
    return [{'match_id': f'm:{i}', 'date': (date(2025, 1, 1)+timedelta(days=i)).isoformat(),
             'kickoff_at': (date(2025, 1, 1)+timedelta(days=i)).isoformat()+'T18:00:00+00:00',
             'home': f't:{i%4}', 'away': f't:{(i+1)%4}', 'home_goals': i%3,
             'away_goals': (i+2)%4, 'cohort': 'uefa.nations:A:league-phase',
             'score_basis': 'regulation_90', 'source_url': 'https://example.org/fixture',
             'observed_at': '2026-11-17T00:00:00+00:00', 'neutral': False}
            for i in range(n)]


def records(rows):
    return [{**r, 'values': {'expected': [2.7, .4], 'actual': [3.1, .6], 'shotmap': [2.8, .5]},
             'quality': {'global_estimated': True, 'directory_has_xg': False}}
            for r in rows]


class XgResearchTests(unittest.TestCase):
    def test_zero_blend_reproduces_retained_goal_estimator(self):
        rows = history()
        a = fit_strength(rows, half_life=730., ridge=8.)
        b = xg.fit(rows, xg.index_xg(records(rows), rows), xg.ARMS[0])
        for row in rows:
            np.testing.assert_allclose(rates(a, row)[:2], rates(b, row)[:2], rtol=1e-10)

    def test_fractional_targets_and_missing_fallback_keep_goal_labels(self):
        rows = history(3)
        before = copy.deepcopy(rows)
        source = records(rows[:1])
        source[0]['values']['expected'][1] = None
        values, coverage = xg.target_values(rows, xg.index_xg(source, rows), xg.ARMS[2])
        self.assertEqual(values[0, 0], .5*rows[0]['home_goals']+.5*2.7)
        self.assertEqual(values[0, 1], rows[0]['away_goals'])
        self.assertEqual(coverage['xg_used_sides'], 1)
        self.assertEqual(coverage['goal_fallback_sides'], 5)
        self.assertEqual(rows, before)

    def test_conflicting_and_estimated_fields_run_separately(self):
        rows = history(20)
        indexed = xg.index_xg(records(rows), rows)
        expected = xg.fit(rows, indexed, {'id': 'expected-100', 'field': 'expected', 'weight': 1.})
        actual = xg.fit(rows, indexed, {'id': 'actual-100', 'field': 'actual', 'weight': 1.})
        self.assertNotEqual(rates(expected, rows[0])[:2], rates(actual, rows[0])[:2])
        self.assertEqual(expected['coverage']['xg_used_sides'], 40)

    def test_target_and_later_xg_cannot_change_earlier_fit(self):
        rows = history()
        target = rows[60]
        indexed = xg.index_xg(records(rows), rows)
        training = xg.training_before(rows, target)
        before = xg.fit(training, indexed, xg.ARMS[2])
        for row in rows[60:]:
            indexed[row['match_id']]['values']['expected'] = [99., 99.]
        after = xg.fit(training, indexed, xg.ARMS[2])
        self.assertEqual(before, after)
        self.assertLess(before['train_end'], target['date'])
        with self.assertRaises(ValueError):
            xg.prediction(before, training[-1], 'test')

    def test_wrong_team_and_duplicate_xg_rejected(self):
        rows = history(2)
        source = records(rows)
        with self.assertRaises(ValueError):
            xg.index_xg(source+[source[0]], rows)
        source[0]['home'] = 'wrong-team'
        with self.assertRaises(ValueError):
            xg.index_xg(source, rows)

    def test_real_quarter_line_settles_without_affecting_score_coverage(self):
        rows = history()
        core = xg.fit(rows[:60], {}, xg.ARMS[0])
        target = rows[60]
        fixture = {**target, 'cutoff_at': target['date']+'T17:00:00+00:00',
                   'total_quote': {'verified': True, 'match_id': target['match_id'],
                     'bookmaker': 'test-book', 'market': 'asian_total', 'line': 2.25,
                     'over_odds': 1.9, 'under_odds': 1.9,
                     'quoted_at': target['date']+'T16:59:00+00:00',
                     'query_time': target['date']+'T17:00:00+00:00',
                     'source_url': 'https://example.org/odds'}}
        quoted = xg.historical_record(core, target, fixture)
        missing = xg.historical_record(core, target, None)
        self.assertIsNotNone(quoted['total_settlement'])
        self.assertIsNone(missing['total_settlement'])
        self.assertEqual(quoted['primary'], missing['primary'])
        self.assertEqual(xg.summarize([quoted, missing])['actual_line_n'], 1)


if __name__ == '__main__':
    unittest.main()
