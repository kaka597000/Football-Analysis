import unittest

import numpy as np

from research_program.sequence_history import fixed_market_baseline, rolling_sequences


def game(row, day, home, away, home_goals, away_goals, league="eng.1"):
    return dict(source_file="test.csv", source_line=row, cohort=league, game_date=day, home=home,
                away=away, home_goals=home_goals, away_goals=away_goals)


class SequenceHistoryTests(unittest.TestCase):
    def test_target_day_is_held_back_and_team_view_is_oriented(self):
        history = [
            game(2, "2025-01-01", "A", "B", 4, 2),
            game(3, "2025-01-02", "C", "A", 3, 1),
            game(4, "2025-01-02", "A", "D", 9, 8),
            game(5, "2025-01-03", "B", "A", 0, 0),
        ]
        targets = [dict(r, match_id=str(r["source_line"])) for r in history[1:]]
        result = rolling_sequences(history, targets)
        self.assertEqual(result[1]["sequence"][0][-1], [4, 2, 1, 1])
        self.assertEqual(result[1]["sequence"][0][-2], [0, 0, 0, 0])
        self.assertEqual(result[2]["sequence"][0][-1], [2, 4, 0, 1])
        self.assertEqual(result[2]["sequence"][1][-2:], [[1, 3, 0, 1], [9, 8, 1, 1]])
        for row in result:
            for side in row["sequence_sources"]:
                self.assertTrue(all(ref is None or ref[2] < row["game_date"] for ref in side))

    def test_uses_last_five_in_same_league_and_can_use_non_target_rows(self):
        matches = [game(n, f"2025-01-{n:02d}", "A", f"B{n}", n, 0) for n in range(1, 9)]
        matches.append(game(9, "2025-01-05", "A", "X", 30, 2, "bra.1"))
        row = rolling_sequences(matches, [dict(matches[7], match_id="one")])[0]
        self.assertEqual([s[0] for s in row["sequence"][0]], [3, 4, 5, 6, 7])
        self.assertEqual(row["sequence"][1], [[0, 0, 0, 0]] * 5)

    def test_market_projection_respects_both_fair_market_margins(self):
        market = {"h2h": {"home": 2., "draw": 3., "away": 4.},
                  "total": {"line": 2.5, "over": 1.75, "under": 2.}}
        m = fixed_market_baseline([1., .6], market)
        goals_h, goals_a = np.indices(m.shape)
        implied = 1 / np.array([2., 3., 4.])
        observed = [m[goals_h > goals_a].sum(), np.trace(m), m[goals_h < goals_a].sum()]
        np.testing.assert_allclose(observed, implied / implied.sum())
        self.assertAlmostEqual(m[goals_h + goals_a > 2.5].sum(), 2 / 3.75)


if __name__ == "__main__":
    unittest.main()
