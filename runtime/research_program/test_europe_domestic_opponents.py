"""Verify the domestic opponent and target date boundaries."""

import unittest

import numpy as np

from research_program.europe_domestic_opponents import AWAY_BUCKET, HOME_BUCKET, OppositionHistory


def fixture(mid, day, home, away, score):
    return dict(id=mid, date=day, competition='eng.1', home_id=home, away_id=away,
                home_goals=score[0], away_goals=score[1], source_sha256='0' * 64)


class OpponentHistoryTest(unittest.TestCase):
    def test_opposition_profile_uses_earlier_dates_and_target_cutoff(self):
        early = fixture('1', '2025-09-01', '102', '103', (0, 5))
        recent = fixture('2', '2025-09-02', '101', '102', (2, 0))
        same_day = fixture('3', '2025-09-03', '101', '103', (9, 0))
        development = OppositionHistory([early, recent])
        full = OppositionHistory([early, dict(recent, home_goals=12, away_goals=12), same_day])
        teams = ('101', '999')
        date = '2025-09-03'
        first, exposure = development.context(teams, date)
        changed, changed_exposure = full.context(teams, date)
        np.testing.assert_array_equal(first, changed)
        self.assertEqual(exposure, changed_exposure)
        self.assertEqual(exposure['home']['opponent'], 1)
        self.assertEqual(exposure['home']['last_domestic_date'], recent['date'])
        self.assertEqual(exposure['away']['domestic'], 0)
        self.assertTrue(np.all(first[HOME_BUCKET > 0, 0] < 0.))
        self.assertTrue(np.all(first[AWAY_BUCKET > 0, 1] > 0.))
        self.assertFalse(np.any(full.context(teams, recent['date'])[0]))


if __name__ == '__main__':
    unittest.main()
