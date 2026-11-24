"""Mechanism checks for the offline comparison. No supplier access or model registration."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from build_inputs import season_rows
from grade_study import decision, label, market, summarize, utc
from market_receipts import event_identity, quote_prices
from match_fixtures import token, unique_club_ids
from model_study import entry_value, examples, forecast


def fixture(day, identifier, deep=(5, 4)):
    return dict(
        id=identifier,
        date=day,
        home_id="h",
        away_id="a",
        goals=[1, 0],
        xg=[1.3, 0.9],
        deep=list(deep),
    )


def source_fixture():
    h = dict(id="1", title="H")
    a = dict(id="2", title="A")
    day = "2025-01-01 19:00:00"
    fixture = dict(
        id="m",
        h=h,
        a=a,
        isResult=True,
        datetime=day,
        goals={"h": "2", "a": "1"},
        xG={"h": "1.5", "a": "1.1"},
    )
    history = [
        dict(
            date=day,
            h_a="h",
            scored=2,
            missed=1,
            xG=1.5,
            xGA=1.1,
            deep=0,
            deep_allowed=4,
        ),
        dict(
            date=day,
            h_a="a",
            scored=1,
            missed=2,
            xG=1.1,
            xGA=1.5,
            deep=4,
            deep_allowed=0,
        ),
    ]
    return {
        "dates": [
            fixture,
            dict(id="n", h=a, a=h, datetime="2026-11-07 19:00:00", isResult=False),
        ],
        "teams": {
            h["id"]: dict(title="H", history=history[:1]),
            a["id"]: dict(title="A", history=history[1:]),
        },
    }


class ResearchWindow(unittest.TestCase):
    def test_unplayed_source_keeps_only_identity_and_schedule(self):
        payload = source_fixture()
        payload["dates"][1].update(goals={"h": "99"}, xG={"a": "99"}, deep=99)
        extracted = season_rows(payload, utc("2026-10-15T01:00Z"))
        self.assertEqual(extracted["completed"][0]["deep"], [0, 4])
        self.assertEqual(
            set(extracted["scheduled"][0]),
            {"id", "date", "scheduled", "home_id", "away_id", "home", "away"},
        )

    def test_missing_or_inconsistent_team_counts_cannot_activate_signal(self):
        payload = source_fixture()
        payload["teams"]["2"]["history"][0]["deep_allowed"] = 9
        rows = season_rows(payload, utc("2026-10-15T01:00Z"))
        self.assertEqual(rows["completed"][0]["deep"], [None, None])
        self.assertIsNone(
            entry_value([{"deep_for": 1, "deep_against": 1}] * 2, "deep_for")
        )

    def test_repeated_or_cross_team_results_cannot_enter_training(self):
        payload = source_fixture()
        payload["dates"][0]["goals"]["a"] = "0"
        with self.assertRaisesRegex(ValueError, "Goals conflict"):
            season_rows(payload, utc("2026-10-15T01:00Z"))

    def test_current_day_is_invisible_even_to_second_game_on_that_day(self):
        earlier = [fixture(f"2025-01-0{i}", str(i)) for i in (1, 2, 3)]
        day = [fixture("2025-01-05", "4"), fixture("2025-01-05", "5")]
        season = dict(completed=earlier + day, scheduled=[])
        before = examples(season)
        season["completed"][-2].update(goals=[9, 9], xg=[25, 25], deep=[50, 50])
        after = examples(season)
        for i in (-1, -2):
            np.testing.assert_array_equal(before[i]["base"], after[i]["base"])
            self.assertEqual(before[i]["pressure"], after[i]["pressure"])
            self.assertEqual(
                before[i]["history_ids"], [["3", "2", "1"], ["3", "2", "1"]]
            )

    def test_october_inputs_remain_fixed_across_sequential_targets(self):
        season = dict(
            completed=[fixture(f"2026-10-0{i}", str(i)) for i in (1, 2, 3)],
            scheduled=[fixture("2026-11-03", "A"), fixture("2026-11-04", "B")],
        )
        targets = examples(season, future=True)
        self.assertEqual(
            [s["history_ids"] for s in targets],
            [[["3", "2", "1"], ["3", "2", "1"]]] * 2,
        )
        self.assertEqual(targets[0]["pressure"], targets[1]["pressure"])

    def test_fallback_exact_and_control_coefficients_fixed(self):
        item = dict(base=np.array([[1.0], [0.0]]), pressure=None)
        learned = dict(
            intercept=0.4,
            base_weights=[0.2],
            pressure_mean=1.0,
            pressure_std=1.0,
            beta=0.1,
        )
        base, new = forecast(item, learned)
        self.assertEqual(base, new)
        item["pressure"] = [2.0, 0.0]
        control, changed = forecast(item, learned)
        self.assertEqual(control, base)
        self.assertGreater(changed[0], base[0])
        self.assertLess(changed[1], base[1])

    def test_club_names_must_be_unique_within_the_provider(self):
        ids = unique_club_ids(
            {"1": {token("Olympique"), token("Équipe")}, "2": {token("Olympique")}}
        )
        self.assertEqual(ids["olympique"], {"1", "2"})
        self.assertEqual(ids["equipe"], {"1"})


class MatchSettlement(unittest.TestCase):
    def setUp(self):
        self.result = dict(
            competition="eng.1", match_id="espn:7", kickoff_at="2026-11-20T20:00:00Z"
        )
        self.cutoff = utc("2026-11-20T19:00:00Z")
        self.quote = dict(
            competition="Premier League",
            match_id="7",
            kickoff_at=self.result["kickoff_at"],
            bookmaker="Pinnacle",
            captured_at="2026-11-20T18:00:00Z",
            quote_at="2026-11-20T17:59:00Z",
            line="2.25",
            over_price="1.91",
            under_price="1.93",
            source_ref="example",
            source_sha256="a" * 64,
        )

    def test_quote_before_cutoff_and_both_sides_required(self):
        for changes in [
            dict(competition="esp.1"),
            dict(match_id="8"),
            dict(kickoff_at="2026-11-20T19:30Z"),
            dict(captured_at=""),
            dict(captured_at="2026-11-20T19:01Z"),
            dict(under_price="1"),
            dict(quote_at="2026-11-20T18:01Z"),
            dict(source_ref=""),
        ]:
            with self.subTest(changes=changes):
                self.assertIsNone(
                    market([self.quote | changes], self.result, self.cutoff)
                )
        self.assertEqual(market([self.quote], self.result, self.cutoff), self.quote)

    def test_market_raw_ref_rejects_reversed_teams_or_wrong_fixture(self):
        q = self.quote | dict(raw_event_id="A", home="Home", away="Away")
        for event, home, away in (("B", "Home", "Away"), ("A", "Away", "Home")):
            with self.assertRaisesRegex(ValueError, "event identity"):
                event_identity(q, event, self.result["kickoff_at"], home, away)

    def test_market_raw_ref_must_have_identical_line_and_prices(self):
        for line, over, under in ((2.5, 1.91, 1.93), (2.25, 1.93, 1.91)):
            with self.assertRaisesRegex(ValueError, "two-sided line"):
                quote_prices(self.quote, line, over, under)

    def test_quote_tier_precedence_and_ambiguous_lines(self):
        no_clock = self.quote | dict(quote_at="", bookmaker="Alpha")
        self.assertEqual(market([no_clock], self.result, self.cutoff), no_clock)
        self.assertEqual(
            market([no_clock, self.quote], self.result, self.cutoff), self.quote
        )
        self.assertIsNone(
            market(
                [self.quote, self.quote | dict(line="2.75")], self.result, self.cutoff
            )
        )

    def test_matrix_top_three_distinct_and_asian_quarter_and_push(self):
        base = decision([2.0, 1.8], 2.25, [1, 1])
        other = decision([2.0, 1.8], 2, [1, 1])
        self.assertEqual(base["total_side"], "over")
        self.assertEqual(base["settlement"], "HALF_LOSS")
        self.assertEqual(other["settlement"], "PUSH")
        self.assertEqual(base["primary"], base["top3"][0])
        self.assertEqual(len(set(base["top3"])), 3)
        row = dict(
            id="x",
            cohort="eng.1",
            date="2026-11-20",
            actual=[1, 1],
            line=2,
            signal_used=True,
            forecast={"base": other, "candidate": other},
        )
        report = summarize([row])
        self.assertEqual(report["paired"]["actual_ou"]["eligible"], 0)
        self.assertIsNone(
            report["score_decision_coverage"]["actual_user_displayed_top3"]
        )

    def test_reject_out_of_scope_or_premature_result_receipts(self):
        import gzip

        from build_inputs import sha

        r = dict(
            age="senior",
            gender="male",
            stage="regular",
            score_basis="regulation_90",
            kickoff_at="2026-11-20T20:00Z",
            raw_reference=dict(
                path="r.gz", sha256=sha(b"{}"), captured_at="2026-11-20T20:45Z"
            ),
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("grade_study.git_file", return_value=gzip.compress(b"{}")),
        ):
            with self.assertRaisesRegex(ValueError, "capture time"):
                label(None, None, r, Path(tmp))
            with self.assertRaisesRegex(ValueError, "Scope"):
                label(None, None, r | dict(stage="playoff"), Path(tmp))


if __name__ == "__main__":
    unittest.main()
