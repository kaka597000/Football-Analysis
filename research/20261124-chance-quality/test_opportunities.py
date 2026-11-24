import copy
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np
from grade import grade, section
from intake import inventory
from opportunities import (
    check_source,
    clock,
    distribution,
    matrix,
    recent_three,
    shot_profile,
)
from retain import retain
from replay import market

CUTOFF = clock("2026-11-04T13:27:00Z")
KICK = clock("2026-11-04T20:00:00Z")


def fixture(mid="3", when="2026-10-31T18:00:00Z"):
    return dict(
        id=mid,
        home={"id": 11},
        away={"id": 12},
        tournament={"name": "Cup"},
        status=dict(utcTime=when, finished=True, started=True, reason={"short": "FT"}),
    )


def historical():
    row = fixture()
    stamp = row["status"]["utcTime"]
    shots = [
        dict(
            id=i,
            teamId=t,
            period="FirstHalf",
            isOwnGoal=og,
            situation=s,
            expectedGoals=x,
        )
        for i, t, og, s, x in [
            (1, 11, False, "RegularPlay", 0.3),
            (2, 12, False, "Penalty", 0.8),
            (3, 12, True, "RegularPlay", None),
        ]
    ]
    raw = dict(
        general=dict(
            matchId="3",
            gender="male",
            matchTimeUTCDate=stamp,
            finished=True,
            homeTeam={"id": 11},
            awayTeam={"id": 12},
            coverageLevel="xG",
        ),
        header=dict(teams=[{"id": 11}, {"id": 12}], status=dict(row["status"])),
        content=dict(
            shotmap=dict(shots=shots),
            stats={
                "Periods": {
                    "All": {
                        "stats": [{"stats": [{"key": "total_shots", "stats": [1, 1]}]}]
                    }
                }
            },
        ),
    )
    return row, raw


def receipt(body=b"page"):
    return dict(
        url="https://www.fotmob.com/en-GB/matches/abc-vs-def/x?matchId=3",
        http_status=200,
        requested_at="2026-11-04T13:24:00Z",
        received_at="2026-11-04T13:25:00Z",
        sha256=sha256(body).hexdigest(),
    )


class Shots(unittest.TestCase):
    def test_regulation_zero_and_penalty(self):
        row, raw = historical()
        self.assertEqual(shot_profile(raw, row, CUTOFF), {"11": [0.3], "12": []})

    def test_bad_join_and_extratime(self):
        row, raw = historical()
        for category in ("team", "et", "shootout", "gender", "future"):
            bad = copy.deepcopy(raw)
            if category == "team":
                bad["general"]["awayTeam"]["id"] = 13
            if category == "et":
                bad["header"]["status"]["halfs"] = {"secondExtraHalfStarted": "present"}
            if category == "shootout":
                bad["content"]["shotmap"]["shots"][0]["period"] = "PenaltyShootout"
            if category == "gender":
                bad["general"]["gender"] = "female"
            with self.subTest(category=category), self.assertRaises(ValueError):
                shot_profile(
                    bad,
                    row,
                    clock(row["status"]["utcTime"]) if category == "future" else CUTOFF,
                )

    def test_incomplete_shots_are_not_zero(self):
        row, raw = historical()
        for key in (
            "missing_xg",
            "missing_map",
            "duplicate",
            "denominator",
            "unknown_situation",
        ):
            bad = copy.deepcopy(raw)
            shots = bad["content"]["shotmap"]["shots"]
            if key == "missing_xg":
                shots[0]["expectedGoals"] = None
            if key == "missing_map":
                bad["content"]["shotmap"] = {}
            if key == "duplicate":
                shots[1]["id"] = shots[0]["id"]
            if key == "denominator":
                shots.pop(0)
            if key == "unknown_situation":
                shots[0]["situation"] = "Unknown"
            with self.subTest(key=key), self.assertRaises(ValueError):
                shot_profile(bad, row, CUTOFF)

    def test_equal_means_different_outcomes(self):
        one = [[0.8]] * 6
        two = [[0.4, 0.4]] * 6
        self.assertTrue(
            np.allclose(
                distribution(one, at_mean=True), distribution(two, at_mean=True)
            )
        )
        self.assertTrue(np.allclose(distribution(two), [0.36, 0.48, 0.16]))
        self.assertTrue(np.allclose(distribution(one), [0.2, 0.8]))
        self.assertAlmostEqual(sum(distribution([[]] * 6)), 1.0)

    def test_orient_allowed_and_fallback(self):
        h = [([1.0], [])] * 3
        a = [([], [1.0])] * 3
        self.assertTrue(np.array_equal(matrix(h, a), [[0.0], [1.0]]))
        with self.assertRaises(ValueError):
            matrix(h[:2], a)

    def test_retain_three_immediate_including_ineligible_period(self):
        t = fixture("99", KICK.isoformat())
        t["status"].update(finished=False, started=False)
        priors = [fixture(str(i), f"2026-11-0{i}T10:00:00Z") for i in (1, 2, 3)]
        priors[1]["status"]["reason"]["short"] = "Pen"
        friendly = fixture("5", "2026-11-04T08:00:00Z")
        friendly["tournament"]["name"] = "Club Friendlies"
        future = fixture("6", "2026-11-04T14:00:00Z")
        cal = dict(
            details={"id": 11, "type": "team", "gender": "male"},
            fixtures={"allFixtures": {"fixtures": priors + [future, t, friendly]}},
        )
        recent = recent_three(cal, "11", "99", ("11", "12"), KICK, CUTOFF)
        self.assertEqual([v["id"] for v in recent], ["3", "2", "1"])
        cal["fixtures"]["allFixtures"]["fixtures"].append(priors[0])
        with self.assertRaisesRegex(ValueError, "conflict"):
            recent_three(cal, "11", "99", ("11", "12"), KICK, CUTOFF)

    def test_native_receipt_digest_cutoff_and_source_id(self):
        with TemporaryDirectory() as tmp:
            body = b'{"test":1}'
            f = Path(tmp) / "matchDetails-9.json"
            f.write_bytes(body)
            r = dict(
                url="https://www.fotmob.com/api/data/matchDetails?matchId=9",
                query={"matchId": 9},
                bytes=len(body),
                sha256=sha256(body).hexdigest(),
                raw_file=str(f),
                status=200,
                query_time_utc="2026-11-04T13:25:00Z",
            )
            args = (Path(tmp), "matchDetails", "9", CUTOFF, CUTOFF)
            self.assertEqual(check_source(r, *args), {"test": 1})
            for changes in [
                dict(sha256="0" * 64),
                dict(query={"matchId": 8}),
                dict(query_time_utc=KICK.isoformat()),
            ]:
                with self.assertRaises(ValueError):
                    check_source(r | changes, *args)
            with self.assertRaises(ValueError):
                check_source(r, *args[:-1], KICK)


class Retention(unittest.TestCase):
    def test_receipt_only_is_never_a_body(self):
        with TemporaryDirectory() as tmp, patch("retain.now", return_value=CUTOFF):
            with self.assertRaisesRegex(ValueError, "raw_digest"):
                retain(Path(tmp), b"different", receipt(), "3", CUTOFF)
            with self.assertRaisesRegex(ValueError, "unusable_receipt"):
                retain(Path(tmp), b"page", receipt(), "4", CUTOFF)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_timely_original_idempotent_after_cut_but_never_reconstructed(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("retain.now", return_value=CUTOFF):
                proof = retain(root, b"page", receipt(), "3", CUTOFF)
            before = proof.read_bytes()
            with patch("retain.now", return_value=KICK):
                self.assertEqual(proof, retain(root, b"page", receipt(), "3", CUTOFF))
                self.assertEqual(proof.read_bytes(), before)
                next((root / "objects").iterdir()).unlink()
                with self.assertRaisesRegex(ValueError, "incomplete"):
                    retain(root, b"page", receipt(), "3", CUTOFF)

    def test_late_first_retention_and_clockless(self):
        with TemporaryDirectory() as tmp, patch("retain.now", return_value=KICK):
            with self.assertRaisesRegex(ValueError, "late_first"):
                retain(Path(tmp), b"page", receipt(), "3", CUTOFF)
        with self.assertRaisesRegex(ValueError, "timezone_missing"):
            clock("2026-11-04T13:27")

    def test_intake_rejects_late_duplicate_and_reversed_schedule(self):
        e = dict(
            fotmob_match_id="3",
            uefa_prematch_status="Upcoming",
            kickoff_utc=KICK.isoformat(),
            fotmob_match_url=receipt()["url"],
            home={"id": "11"},
            away={"id": "12"},
        )
        b = dict(
            per_team_recent_window=3, events=[e], receipts={receipt()["url"]: receipt()}
        )
        with self.assertRaisesRegex(ValueError, "clock"):
            inventory(b, KICK)
        for s in ("home", "away"):
            e[s].update(last5=[], recent_process=[])
        with self.assertRaisesRegex(ValueError, "schedule"):
            inventory(b, CUTOFF)


class Metrics(unittest.TestCase):
    def test_rescue_denominator_and_fallback(self):
        base = [[0.0, 0.4], [0.5, 0.1]]
        cand = [[0.2, 0.3], [0.1, 0.4]]
        row = dict(
            id="espn:1",
            team=base,
            market=base,
            line=2.0,
            home="A",
            away="B",
            kickoff=KICK.isoformat(),
            main_asian_line_verified=False,
        )
        result = dict(
            id="espn:1",
            home="A",
            away="B",
            kickoff=KICK.isoformat(),
            competition="uefa.champions",
            status="FT90",
            score=[0, 0],
        )
        scored = grade([row | dict(candidate=cand, same_xg_poisson=cand)], [result])[
            "with_opportunities"
        ]
        self.assertEqual((scored["rescued"], scored["double_miss"]), (1, 1))
        fallback = grade([row], [result])
        self.assertEqual(fallback["with_opportunities"]["arms"]["team"]["n"], 0)
        self.assertIsNone(section([])["arms"]["team"]["mean_log_loss"])
        self.assertEqual(
            fallback["all_with_team_fallback"]["arms"]["team"],
            fallback["all_with_team_fallback"]["arms"]["candidate"],
        )
        with self.assertRaises(ValueError):
            grade([row], [result | dict(status="PEN")])
        with self.assertRaises(ValueError):
            grade([row], [result | dict(away="C")])

    def test_real_market_and_orientation(self):
        when = "2026-11-04T13:20:00Z"
        q = dict(
            match_id="espn:1",
            bookmaker="Pinnacle",
            verified=True,
            query_time=when,
            quoted_at=when,
            source_event_id="E",
            source_file_sha256="hash",
            line=2.75,
            over_odds=1.9,
            under_odds=2.0,
        )
        f = dict(
            match_id="espn:1",
            home="espn:8",
            away="espn:18",
            total_quote=q,
            cutoff_at=CUTOFF.isoformat(),
            kickoff_at=KICK.isoformat(),
        )
        mapped = dict(
            odds_api_match_status="EXACT_TEAM_AND_KICKOFF",
            odds_api_event_id="E",
            espn_state="STATUS_SCHEDULED",
            espn_home_team_id=8,
            espn_away_team_id=18,
            odds_api_home_team="H",
            odds_api_away_team="A",
            kickoff_utc=KICK.isoformat(),
        )
        wdl = [
            {"name": k, "price": v} for k, v in [("H", 2.0), ("Draw", 3.4), ("A", 3.2)]
        ]
        totals = [
            dict(name="Over", price=1.9, point=2.75),
            dict(name="Under", price=2.0, point=2.75),
        ]
        book = dict(
            title="Pinnacle",
            markets=[
                dict(last_update=when, outcomes=wdl),
                dict(last_update=when, outcomes=totals),
            ],
        )
        event = dict(
            id="E",
            sport_key="soccer_uefa_champs_league",
            commence_time=KICK.isoformat(),
            home_team="H",
            away_team="A",
            bookmakers=[book],
        )
        capture = dict(queried_at_utc=when, saved_file_sha256="hash")
        m, line = market(f, mapped, capture, [event])
        self.assertAlmostEqual(np.sum(m), 1.0)
        self.assertEqual(line, 2.75)
        for updated, reason in [
            (mapped | dict(odds_api_away_team="H"), "market_event"),
            (mapped | dict(espn_home_team_id=18), "market_crosswalk"),
        ]:
            with self.assertRaisesRegex(ValueError, reason):
                market(f, updated, capture, [event])
        book["markets"][0]["last_update"] = "2026-11-04T13:22:00Z"
        with self.assertRaisesRegex(ValueError, "clock"):
            market(f, mapped, capture, [event])
        book["markets"][0]["last_update"] = when
        totals[1]["point"] = 2.5
        with self.assertRaisesRegex(ValueError, "total_pair"):
            market(f, mapped, capture, [event])

    def test_no_ablation_change_with_identical_backoff(self):
        stats = section([])
        self.assertEqual(stats["double_miss"], 0)
        self.assertIsNone(stats["arms"]["same_xg_poisson"]["mean_log_loss"])
