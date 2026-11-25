"""Compatibility checks run with the pinned #78 source and runtime on PYTHONPATH."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from keep_prior import keep
from opportunities import clock
from test_opportunities import historical


def inputs(now):
    prior, detail = historical()
    prior["tournament"]["leagueId"] = detail["general"]["leagueId"] = 47
    past = now - timedelta(days=3)
    prior["status"]["utcTime"] = detail["general"]["matchTimeUTCDate"] = detail[
        "header"
    ]["status"]["utcTime"] = past.isoformat()
    attempt = detail["content"]["shotmap"]["shots"][0]
    attempt.update(situation="FromCorner", x=92.0, y=33.0)
    kick, cutoff = now + timedelta(hours=4), now + timedelta(minutes=15)
    target = dict(
        id="41",
        home="11",
        away="22",
        league_id=47,
        cutoff=cutoff.isoformat(),
        kickoff=kick.isoformat(),
    )
    schedule = dict(
        details={"id": 11, "type": "team", "gender": "male"},
        fixtures={
            "allFixtures": {
                "fixtures": [
                    prior,
                    dict(
                        id="41",
                        home={"id": 11},
                        away={"id": 22},
                        tournament={"leagueId": 47, "name": "League"},
                        status={
                            "utcTime": kick.isoformat(),
                            "finished": False,
                            "started": False,
                        },
                    ),
                ]
            }
        },
    )
    rec = dict(
        url="https://www.fotmob.com/en-GB/matches/a-b/abc?matchId=3",
        http_status=200,
        requested_at=(now - timedelta(minutes=2)).isoformat(),
        received_at=(now - timedelta(minutes=1)).isoformat(),
    )
    return target, schedule, prior, detail, rec


def page(detail, receipt):
    block = json.dumps({"props": {"pageProps": detail}})
    body = (
        '<html><script id="__NEXT_DATA__" type="application/json">'
        + block
        + "</script></html>"
    ).encode()
    receipt["sha256"] = sha256(body).hexdigest()
    return body


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = datetime.now(timezone.utc)
        self.target, self.cal, self.prior, self.detail, self.rec = inputs(self.now)
        self.cut = clock(self.target["cutoff"])

    def keep_once(self):
        body = page(self.detail, self.rec)
        return keep(
            body,
            self.rec,
            self.cal,
            "11",
            self.target,
            self.cut,
            self.root,
            calendar_received=clock(self.rec["requested_at"]),
        )

    def test_cli_roundtrip_uses_native_calendar_and_real_retainer(self):
        root = self.root
        body = page(self.detail, self.rec)
        (root / "raw").write_bytes(body)
        calbody = json.dumps(self.cal).encode()
        (root / "teams-11.json").write_bytes(calbody)
        row = dict(
            status=200,
            raw_file="teams-11.json",
            url="https://www.fotmob.com/api/data/teams?id=11",
            query={"id": "11"},
            bytes=len(calbody),
            sha256=sha256(calbody).hexdigest(),
            query_time_utc=self.rec["requested_at"],
        )
        for file, obj in (
            ("receipt", self.rec),
            ("target", self.target),
            (
                "index",
                {"source_queries": [row], "queried_at_utc": self.rec["received_at"]},
            ),
        ):
            (root / file).write_text(json.dumps(obj))
        command = [
            sys.executable,
            str(Path(__file__).with_name("keep_prior.py")),
            "--raw",
            str(root / "raw"),
            "--prior-id",
            "3",
            "--receipt",
            str(root / "receipt"),
            "--calendar-index",
            str(root / "index"),
            "--calendar-raw-dir",
            str(root),
            "--target",
            str(root / "target"),
            "--team",
            "11",
            "--private-dir",
            str(root / "kept"),
        ]
        one = subprocess.check_output(command)
        self.assertEqual(subprocess.check_output(command), one)
        self.assertEqual(
            json.loads(one)["prior"]["set_plays_by_team"],
            {"11": {"FromCorner": 1}, "12": {}},
        )
        original = json.loads(one)
        link = json.loads(
            (root / "kept" / "slot-receipts" / original["slot_receipt"]).read_bytes()
        )["binding"]
        self.assertEqual(link["calendar"]["sha256"], row["sha256"])
        self.assertEqual(link["target"]["cutoff"], self.target["cutoff"])
        (root / "kept" / "objects" / (row["sha256"] + ".bin")).unlink()
        self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
        self.assertFalse(
            (root / "kept" / "objects" / (row["sha256"] + ".bin")).exists()
        )
        (root / "teams-11.json").write_bytes(calbody + b" ")
        self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)

    def test_missing_hash_bound_object_stays_missing_after_restart(self):
        found = self.keep_once()
        (self.root / "objects" / (found["raw_sha256"] + ".bin")).unlink()
        with (
            patch("retain.now", return_value=self.cut + timedelta(days=1)),
            self.assertRaisesRegex(ValueError, "retained_proof_incomplete"),
        ):
            self.keep_once()

    def test_late_replay_requires_an_existing_receipt(self):
        first = self.keep_once()
        with patch("retain.now", return_value=self.cut + timedelta(days=1)):
            self.assertEqual(self.keep_once(), first)
        self.rec["requested_at"] = self.rec["received_at"]
        with (
            patch("retain.now", return_value=self.cut + timedelta(days=1)),
            self.assertRaisesRegex(ValueError, "late_first"),
        ):
            self.keep_once()

    def test_own_target_cannot_be_history(self):
        self.detail["general"]["matchId"] = "41"
        with self.assertRaisesRegex(ValueError, "previous_three"):
            self.keep_once()

    def test_wrong_prior_competition_teams_and_period(self):
        self.detail["general"]["leagueId"] = 59
        with self.assertRaisesRegex(ValueError, "competition"):
            self.keep_once()
        self.detail["general"]["leagueId"] = 47
        self.detail["header"]["teams"].reverse()
        with self.assertRaisesRegex(ValueError, "team_identity"):
            self.keep_once()
        self.detail["header"]["teams"].reverse()
        self.detail["header"]["status"]["reason"]["short"] = "AET"
        with self.assertRaisesRegex(ValueError, "regulation"):
            self.keep_once()

    def test_missing_xg_and_incomplete_shotmap(self):
        s = self.detail["content"]["shotmap"]["shots"]
        s[0]["expectedGoals"] = None
        with self.assertRaisesRegex(ValueError, "missing_shot_xg"):
            self.keep_once()
        s.pop(0)
        with self.assertRaisesRegex(ValueError, "incomplete_shotmap"):
            self.keep_once()

    def test_receipt_after_deadline_or_for_another_prior(self):
        self.rec["received_at"] = self.target["kickoff"]
        with self.assertRaisesRegex(ValueError, "unusable_receipt"):
            self.keep_once()
        self.rec["received_at"] = self.now.isoformat()
        self.rec["url"] = self.rec["url"].replace("matchId=3", "matchId=4")
        with self.assertRaisesRegex(ValueError, "unusable_receipt"):
            self.keep_once()

    def test_new_target_link_cannot_be_backfilled_using_an_existing_prior(self):
        from keep_prior import bind_slot

        saved = self.keep_once()
        raw = json.dumps(self.cal).encode()
        meta = dict(
            url="https://www.fotmob.com/api/data/teams?id=11",
            query_time_utc=self.rec["requested_at"],
            status=200,
            bytes=len(raw),
            sha256=sha256(raw).hexdigest(),
        )
        at = clock(self.rec["received_at"])
        first = bind_slot(self.root, saved, self.target, raw, meta, at, self.cut)
        with patch("retain.now", return_value=self.cut + timedelta(days=1)):
            self.assertEqual(
                first, bind_slot(self.root, saved, self.target, raw, meta, at, self.cut)
            )
            changed = dict(self.target, id="99")
            with self.assertRaisesRegex(ValueError, "late_first_slot_binding"):
                bind_slot(self.root, saved, changed, raw, meta, at, self.cut)

    def test_stale_calendar_cannot_hide_an_intervening_fixture(self):
        from keep_prior import check_calendar_time

        old = self.now - timedelta(days=2)
        check_calendar_time(self.cal, old, self.cut, "41")
        self.cal["fixtures"]["allFixtures"]["fixtures"].append(
            dict(
                id="4",
                status={
                    "utcTime": (self.now - timedelta(days=1)).isoformat(),
                    "finished": False,
                },
            )
        )
        with self.assertRaisesRegex(ValueError, "unresolved_intervening"):
            check_calendar_time(self.cal, old, self.cut, "41")

    def test_normal_reader_without_calendar_keeps_original_with_zero_eligible_slots(
        self,
    ):
        body = page(self.detail, self.rec)
        for name, obj in (("receipt", self.rec), ("target", self.target)):
            (self.root / name).write_text(json.dumps(obj))
        (self.root / "raw").write_bytes(body)
        cmd = [
            sys.executable,
            str(Path(__file__).with_name("keep_prior.py")),
            "--raw",
            str(self.root / "raw"),
            "--receipt",
            str(self.root / "receipt"),
            "--target",
            str(self.root / "target"),
            "--private-dir",
            str(self.root / "kept"),
            "--prior-id",
            "3",
        ]
        receipt = json.loads(subprocess.check_output(cmd))
        self.assertEqual(
            (receipt["status"], receipt["eligible_slots"]), ("raw_only", 0)
        )
        self.assertEqual(
            (
                self.root / "kept" / "objects" / (self.rec["sha256"] + ".bin")
            ).read_bytes(),
            body,
        )
        self.assertFalse((self.root / "kept" / "slot-receipts").exists())
        cmd[-1] = "41"
        self.assertNotEqual(subprocess.run(cmd, capture_output=True).returncode, 0)
