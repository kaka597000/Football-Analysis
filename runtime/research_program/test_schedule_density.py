import unittest
from datetime import date

import numpy as np

from research_program import schedule_density as model
from research_program import schedule_density_study as study
from research_program.schedule_density_results import korean_result
from research_program.all_scope_model import H, A, pick


class ScheduleDensityTests(unittest.TestCase):
    def test_lookback_edges_roles_and_original_schedule_only_counts(self):
        cal = model.Calendar()
        for day, home, away in ((85, "X", "B"), (86, "X", "B"), (93, "X", "B"),
                               (96, "B", "X"), (98, "X", "B")):
            cal.observe(dict(home=home, away=away), day, scheduled=day == 98)
        x, meta = cal.features(dict(home="B", away="Y"), 100)
        np.testing.assert_allclose(x[0], [5/6, 0, 2/3, 0, 1., 0])
        np.testing.assert_allclose(x[1], [0, 5/6, 0, 2/3, 0, 1.])
        self.assertEqual(meta["home"], dict(days_since_last_within_14=2, games_in_14=4,
                         listed_away_in_7=2, schedule_only_in_14=1))
        with self.assertRaises(ValueError):
            cal.features(dict(home="B", away="Y"), 98)

    def test_same_date_order_and_labels_cannot_affect_features(self):
        games = [dict(home=a, away=b, date=d) for d,a,b in [
            ("2026-01-01","A","B"), ("2026-01-04","A","C"), ("2026-01-04","D","A"),
            ("2026-01-08","A","B"), ("2026-02-16","A","B")]]
        x, info, state = model.walk(games)
        reordered = [games[0], games[2], games[1], games[3], games[4]]
        other, _, _ = model.walk([dict(g, home_goals=10) for g in reordered])
        np.testing.assert_array_equal(x, other[[0,2,1,3,4]])
        self.assertFalse(x[-1].any())
        self.assertEqual(info[3]["home"]["games_in_14"], 3)
        snap = state.snapshot(date.fromisoformat("2026-02-16").toordinal())
        self.assertEqual(set(snap), {"A", "B"})

    def test_joint_fit_preserves_support_and_finds_a_calendar_effect(self):
        p = np.exp(-H - A) * (H + A <= 8)
        p /= p.sum()
        empty = model.fit([], np.empty((0,2,6)), np.empty((0,2), dtype=int))
        self.assertIs(model.apply(p, np.ones((2,6)), empty), p)
        x = np.zeros((120,2,6))
        x[:60,0,0] = 1
        x[60:,1,0] = 1
        scores = np.array([[2,0]] * 60 + [[0,2]] * 60)
        weights = model.fit([p] * len(x), x, scores)
        tilted = model.apply(p, x[0], weights)
        self.assertGreater(tilted[2,0]/tilted[0,0], p[2,0]/p[0,0])
        self.assertAlmostEqual(tilted.sum(), 1.)
        np.testing.assert_array_equal(tilted == 0, p == 0)
        self.assertIs(model.apply(p, np.zeros((2,6)), weights), p)
        self.assertEqual(weights["features"], list(model.FEATURES))
        frozen = pick(p, None)
        chosen, prob, _ = study.predict(p, x[0], weights, dict(score=0., wdl=0., total=0.), None, frozen, .321)
        self.assertEqual((chosen, prob), (frozen, .321))

    def test_short_batches_and_ordered_backups_do_not_count_as_first_sweeps(self):
        rows = []
        for i, (day, actual) in enumerate([("01",[1,0]),("01",[1,0]),("01",[1,1]),("02",[1,0])]):
            p = dict(primary="1-0", top3=["1-0", "1-1", "2-0"], wdl="home", total_side=None)
            rows.append(dict(id=str(i), cohort="eng.1", date="2026-01-"+day, actual=actual, line=None,
                             forecast={k:p for k in ("candidate","r7","fixed_full","market")},
                             primary_probability=dict(candidate=.15, r7=.15), calendar_signal=False,
                             coverage={s:dict(games_in_14=0) for s in ("home","away")}))
        batches = study.cohort_batches(rows)
        self.assertEqual([s["id"] for s in batches[0]["seats"]["candidate"]], ["0","1","2"])
        summary = study.summary(rows, batches)["ranked"]["candidate"]
        self.assertEqual((summary["full_batches"],summary["short_batches"],summary["all_three_first"]), (1,1,0))
        self.assertEqual(summary["ordered_seats"][2], dict(seat=3, filled=1, first=0, ordered_three=1))

    def test_awarded_score_never_becomes_a_training_label_or_confirmed_appearance(self):
        row = dict(id="fotmob:1", home="A", away="B", date="2026-10-03", stage="split-a", home_goals=3, away_goals=0)
        status = dict(utcTime="2026-10-03T09:00Z", finished=True, awarded=True, scoreStr="3 - 0",
                      reason=dict(short="AW", shortKey="awarded"))
        raw = dict(id="1", home=dict(id=70, name="A"), away=dict(id=95, name="B"), round="34", status=status)
        self.assertEqual(korean_result(row, raw), "awarded")
        status.update(awarded=False, reason=dict(short="FT", shortKey="fulltime_short"))
        self.assertIsNone(korean_result(row, raw))
        with self.assertRaises(ValueError):
            korean_result(dict(row, away_goals=2), raw)
        other = dict(row, id="fotmob:2", date="2026-10-05")
        _, coverage, _ = model.walk([row,other], {row["id"]})
        self.assertEqual(coverage[-1]["home"]["games_in_14"], 0)
