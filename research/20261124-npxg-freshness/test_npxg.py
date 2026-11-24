"""Synthetic cutoff, identity, intake and fallback regressions for the isolated npxG audit."""

import csv
import io
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from audit import (
    dump,
    eligible_snapshot,
    matching,
    previous_six,
    sha,
    valid_pair,
)
from capture_local import capture, insert, parse_policy, record
from fotmob_coverage import compatible_record, verified_teams
from model_replay import choose_rates, select_line
from preview_local import preview

UTC = timezone.utc
START = datetime(2026, 1, 1, 8, tzinfo=UTC)
CLOCK = START + timedelta(days=20, hours=2)
CODES = "ABCDE"


def fixture_data(root, *, incomplete=False, missing=None, received=CLOCK):
    teams = {t: {"id": t, "title": t, "history": []} for t in CODES}
    games = []
    cal = []
    last = ("A", "B")
    pairs = [(h, a) for h in CODES for a in CODES if h != a and (h, a) != last] + [last]
    for i, (h, a) in enumerate(pairs):
        mid = str(i + 1)
        kick = START + timedelta(days=i if i < len(pairs) - 1 else 25)
        prior = (h, a) != last
        stamp = kick.strftime("%Y-%m-%d %H:%M:%S")
        g = {
            "id": mid,
            "h": {"id": h, "title": h},
            "a": {"id": a, "title": a},
            "datetime": stamp,
            "isResult": prior,
        }
        if prior:
            g["goals"] = {"h": "1", "a": "0"}
            for side, opp, n, loc, gf, ga in (
                (h, a, 0.7, "h", 1, 0),
                (a, h, 0.4, "a", 0, 1),
            ):
                team = {
                    "h_a": loc,
                    "date": stamp,
                    "scored": gf,
                    "missed": ga,
                    "xG": 1.2 if loc == "h" else 0.9,
                    "xGA": 0.9 if loc == "h" else 1.2,
                    "npxG": n,
                    "npxGA": 0.4 if loc == "h" else 0.7,
                }
                if mid == missing:
                    team["npxG"] = None
                teams[side]["history"].append(team)
        games.append(g)
        cal.append(
            {
                "fixture_id": "fixture-" + mid,
                "home": h,
                "away": a,
                "kickoff_at": kick.isoformat(),
                "status": "FT90" if prior else "SCHEDULED",
            }
        )
    wire = json.dumps({"teams": teams, "dates": games}).encode()
    record(
        root,
        dict(
            kind="league",
            state="NATIVE_LEAGUE_DATA",
            competition="eng.1",
            season=2026,
            source="https://understat.com/getLeagueData/EPL/2026",
            status=200,
            start_at=(received - timedelta(seconds=1)).isoformat(),
            received_at=received.isoformat(),
            decoded_sha256=sha(wire),
        ),
        wire,
    )
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=cal[0])
    writer.writeheader()
    writer.writerows(cal[1:] if incomplete else cal)
    raw = buf.getvalue().encode()
    (root / "calendar.csv").write_bytes(raw)
    meta = root / "calendar.json"
    dump(
        meta,
        dict(
            competition="eng.1",
            season=2026,
            calendar_provider="synthetic independent schedule",
            received_at=CLOCK.isoformat(),
            file="calendar.csv",
            sha256=sha(raw),
            complete_league_calendar_as_of=True,
        ),
    )
    return meta, games


class TestAudit(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def test_same_six_for_both_clubs(self):
        meta, g = fixture_data(self.root)
        result = preview(self.root, "eng.1", 2026, g[-1]["id"], meta, CLOCK)
        self.assertEqual(result["status"], "BOTH_SIX")
        self.assertEqual([x["observed_slots"] for x in result["sides"]], [6, 6])
        self.assertFalse(result["prediction_issued"])

    def test_missing_middle_fixture_does_not_take_seventh(self):
        meta, g = fixture_data(self.root, missing="12")
        result = preview(self.root, "eng.1", 2026, g[-1]["id"], meta, CLOCK)
        self.assertEqual(result["status"], "INSUFFICIENT_EXACT_LAST_SIX")
        self.assertEqual([x["observed_slots"] for x in result["sides"]], [5, 6])

    def test_cannot_self_assert_calendar_completeness(self):
        meta, g = fixture_data(self.root, incomplete=True)
        self.assertEqual(
            preview(self.root, "eng.1", 2026, g[-1]["id"], meta, CLOCK)["status"],
            "INCOMPLETE_INDEPENDENT_CALENDAR",
        )

    def test_provider_edition_must_be_available_by_effective_cutoff(self):
        late = START + timedelta(days=25)
        meta, g = fixture_data(self.root, received=late)
        self.assertEqual(
            preview(self.root, "eng.1", 2026, g[-1]["id"], meta, CLOCK)["status"],
            "NO_NATIVE_SNAPSHOT_BY_CHECK",
        )
        self.assertEqual(
            preview(
                self.root, "eng.1", 2026, g[-1]["id"], meta, late + timedelta(hours=1)
            )["status"],
            "SOURCE_AFTER_CUTOFF",
        )

    def test_cutoff_archive_version_and_unknowns(self):
        later = CLOCK + timedelta(days=1)
        old = dict(observed=CLOCK - timedelta(days=1), known_by=later)
        fresh = dict(observed=CLOCK, known_by=later)
        self.assertIsNone(eligible_snapshot([old, fresh], CLOCK, True))
        self.assertIs(eligible_snapshot([old, fresh], CLOCK, False), fresh)
        self.assertIs(eligible_snapshot([old, fresh], later, True), fresh)

    def test_wrong_home_or_receipt_order_rejected(self):
        g = dict(id="4", home="H", away="A", day="2026-01-01")
        ob = dict(
            home_id="H",
            away_id="A",
            date=g["day"],
            npx=[0.0, 1.0],
            observed_at=CLOCK.isoformat(),
            known_by=CLOCK.isoformat(),
        )
        self.assertIs(matching({"4": [ob]}, g, "H", CLOCK, True), ob)
        self.assertIsNone(
            matching(
                {"4": [ob | {"home_id": "A", "away_id": "H"}]}, g, "H", CLOCK, True
            )
        )
        self.assertIsNone(
            matching({"4": [ob | {"npx": [None, 1]}]}, g, "H", CLOCK, False)
        )
        self.assertIsNone(
            matching({"4": [ob, ob | {"npx": [0.0, 1.4]}]}, g, "H", CLOCK, True)
        )

    def test_same_day_unknown_time_not_admitted(self):
        g = dict(
            id="1",
            home="H",
            away="A",
            day=CLOCK.date().isoformat(),
            kickoff=None,
            finished=False,
        )
        self.assertEqual(previous_six([g], "A", CLOCK, "2")[1], "AMBIGUOUS_SAME_DAY")

    def test_zero_valid_reciprocal_required(self):
        h = {"npxG": 0, "npxGA": 0.4, "xG": 0.2}
        a = {"npxG": 0.4, "npxGA": 0, "xG": 0.5}
        self.assertTrue(valid_pair(h, a))
        self.assertFalse(valid_pair(h | {"npxGA": 0.6}, a))
        self.assertFalse(valid_pair(h | {"npxGA": None}, a))

    def test_raw_or_receipt_tampering_and_idempotent_record(self):
        meta, g = fixture_data(self.root)
        blob = next((self.root / "objects").glob("*.bin"))
        data = blob.read_bytes()
        insert(blob, data)
        with self.assertRaises(ValueError):
            insert(blob, b"different")
        blob.write_bytes(b"{}")
        with self.assertRaisesRegex(ValueError, "bytes differ"):
            preview(self.root, "eng.1", 2026, g[-1]["id"], meta, CLOCK)

    def test_only_pending_matches_can_be_previewed(self):
        meta, g = fixture_data(self.root)
        rq = preview(self.root, "eng.1", 2026, g[2]["id"], meta, CLOCK)
        self.assertIn(
            rq["status"],
            (
                "CALENDAR_AFTER_CUTOFF",
                "TARGET_NOT_PENDING_IN_CAPTURE",
                "SOURCE_AFTER_CUTOFF",
            ),
        )

    def test_html_policy_does_not_query_league(self):
        self.assertIsNone(
            parse_policy(200, b"  <!doctype html><title>error</title>", None)
        )
        with patch(
            "capture_local.request", return_value=(200, b"<html>notice</html>", {})
        ) as call:
            capture(self.root, 2026, ["eng.1", "fra.1"])
            self.assertEqual(call.call_count, 1)
        self.assertEqual(
            json.loads(next((self.root / "receipts").glob("*.json")).read_bytes())[
                "state"
            ],
            "POLICY_UNAVAILABLE",
        )

    def test_denial_and_failed_league_stop(self):
        for code in (403, 429):
            with (
                self.subTest(code=code),
                patch(
                    "capture_local.request",
                    side_effect=[
                        (200, b"User-agent: *\nAllow: /", {}),
                        (code, b"", {}),
                    ],
                ) as call,
            ):
                capture(self.root, 2026, ["eng.1", "fra.1"])
                self.assertEqual(call.call_count, 2)

    def test_missing_six_preserves_baseline_exactly(self):
        from numpy import array

        x = array([[1.0], [0.5]])
        ctrl = {"intercept": 0.0, "base_weights": [1.0]}
        b, c = choose_rates(ctrl, x, None, None)
        self.assertEqual(b.tobytes(), c.tobytes())
        with self.assertRaises(ValueError):
            choose_rates(ctrl, x, {"intercept": 0.0, "weights": [0.0]}, None)

    def test_cross_provider_alias_needs_independent_ordered_opponents(self):
        a = CLOCK
        b = CLOCK + timedelta(days=2)
        clubs = [
            dict(calendar_name="Known one", understat_name="One", understat_id="1"),
            dict(calendar_name="Known two", understat_name="Two", understat_id="2"),
        ]

        def game(id, opp, at):
            return dict(
                fotmob_match_id=id,
                home_fotmob_team_id="x",
                home_name="Different alias",
                away_fotmob_team_id=opp,
                away_name="Known " + opp,
                kickoff_utc=at.isoformat(),
            )

        games = [game("g1", "one", a), game("g2", "two", b)]
        self.assertNotIn(
            "x", verified_teams(games, clubs, {a: [("3", "1")], b: [("2", "3")]})[0]
        )
        self.assertEqual(
            verified_teams(games, clubs, {a: [("3", "1")], b: [("3", "2")]})[0]["x"],
            "3",
        )

    def test_fotmob_cannot_claim_capture_without_companion_receipt(self):
        row = dict(
            finished_90_no_et_marker="True",
            full_time_reason="FT",
            source_commit_time_utc=CLOCK.isoformat(),
            member_raw_sha256="abc",
            native_npxg_home=".2",
            native_npxg_away=".9",
            native_npxga_away=".2",
            native_npxga_home=".9",
            captured_at_utc="",
        )
        self.assertTrue(compatible_record(row))
        self.assertFalse(
            compatible_record(
                row
                | {
                    "captured_at_utc": CLOCK.isoformat(),
                    "meta_sha256_matches_raw": "True",
                    "meta_file_sha256": "",
                }
            )
        )
        self.assertFalse(compatible_record(row | {"full_time_reason": "Pen"}))

    def test_two_source_clocks_and_cutoff(self):
        target = dict(
            kickoff=(CLOCK + timedelta(hours=1)).isoformat(), cutoff=CLOCK.isoformat()
        )
        q = dict(
            kickoff_at=target["kickoff"],
            bookmaker="synthetic",
            line="2.75",
            over_price="1.94",
            under_price="1.96",
            raw_sha256="a",
            receipt_sha256="b",
            captured_at=CLOCK.isoformat(),
            quote_at="",
        )
        self.assertIs(select_line([q], target), q)
        self.assertIsNone(select_line([q | {"captured_at": target["kickoff"]}], target))
        self.assertIsNone(select_line([q | {"quote_at": target["kickoff"]}], target))
        self.assertIsNone(select_line([q, q | {"line": "3"}], target))


if __name__ == "__main__":
    unittest.main()
