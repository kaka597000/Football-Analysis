"""Verify original team/market evidence and replay the early opportunity matrices from pinned Git objects."""

import argparse
from contextlib import contextmanager
import gzip
from hashlib import file_digest, sha256
import json
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile

from early import TARGETS, run
from opportunities import clock, require
from research.analysis_freeze import digest, verify
from research_program.market_baseline import quote_only_matrices
from research_program.three_arm_model import score_picks

DATA = "a6d45024cf6cf622f05e4b378ab53cfe2d6ae329"
PACK = "workspace-updates/20261105-nine-items/"
ROOT = "WCPP/reports/europe-20261104/"
ARCHIVE_SHA = "806770392ad20248af02bce6a64c91e8bb3bf206a752bff7503fe56f551ae9aa"
LOCK = Path(__file__).with_name("blind-lock.json")


@contextmanager
def evidence(repo, tmp, prefix=PACK, archive_sha=ARCHIVE_SHA):
    def blob(path):
        return subprocess.check_output(
            ["git", "-C", str(repo), "show", DATA + ":" + prefix + path]
        )

    manifest = json.loads(blob("manifest.json"))
    packed = tmp / "source.gz"
    with packed.open("wb") as dest:
        for part in manifest["ordered_parts"]:
            raw = blob(part["path"])
            require(
                sha256(raw).hexdigest() == part["sha256"] and len(raw) == part["bytes"],
                "archive_part",
            )
            dest.write(raw)
    with packed.open("rb") as f:
        require(
            file_digest(f, "sha256").hexdigest()
            == manifest["archive_sha256"]
            == archive_sha,
            "archive_digest",
        )
    with tempfile.TemporaryFile() as tarbytes, gzip.open(packed, "rb") as source:
        shutil.copyfileobj(source, tarbytes)
        tarbytes.seek(0)
        with tarfile.open(fileobj=tarbytes, mode="r:") as archive:
            yield archive


def packed_value(archive, name):
    member = archive.getmember(name)
    require(member.isfile(), "not_source_file")
    return json.load(archive.extractfile(member))


def market(fixture, mapped, capture, events, *, sport="soccer_uefa_champs_league"):
    q = fixture["total_quote"]
    eid = q["source_event_id"]
    mid = fixture["match_id"]
    require(
        q["verified"]
        and q["bookmaker"] == "Pinnacle"
        and q["match_id"] == mid
        and mapped["odds_api_match_status"]
        in ("EXACT_TEAM_AND_KICKOFF", "EXACT_KICKOFF_TEAM_LABEL_VARIANT")
        and mapped["odds_api_event_id"] == eid
        and mapped["espn_state"] == "STATUS_SCHEDULED"
        and (
            "espn:" + str(mapped["espn_home_team_id"]),
            "espn:" + str(mapped["espn_away_team_id"]),
        )
        == (fixture["home"], fixture["away"]),
        "market_crosswalk",
    )
    (event,) = [e for e in events if e["id"] == eid]
    require(
        event["sport_key"] == sport
        and clock(event["commence_time"])
        == clock(mapped["kickoff_utc"])
        == clock(fixture["kickoff_at"])
        and (event["home_team"], event["away_team"])
        == (mapped["odds_api_home_team"], mapped["odds_api_away_team"]),
        "market_event",
    )
    (book,) = [b for b in event["bookmakers"] if b["title"] == "Pinnacle"]
    wdl_names = {event["home_team"], "Draw", event["away_team"]}
    (wdl,) = [
        m for m in book["markets"] if {r["name"] for r in m["outcomes"]} == wdl_names
    ]
    (ou,) = [
        m
        for m in book["markets"]
        if {r["name"] for r in m["outcomes"]} == {"Over", "Under"}
    ]
    captured = clock(capture["queried_at_utc"])
    require(
        clock(q["query_time"]) == captured <= clock(fixture["cutoff_at"])
        and clock(ou["last_update"]) == clock(q["quoted_at"])
        and all(
            0 <= (captured - clock(m["last_update"])).total_seconds() <= 3600
            for m in (ou, wdl)
        )
        and q["source_file_sha256"] == capture["saved_file_sha256"],
        "market_clock_or_source",
    )
    prices = {r["name"]: r["price"] for r in wdl["outcomes"]}
    require(
        len(wdl["outcomes"]) == 3
        and set(prices) == {event["home_team"], "Draw", event["away_team"]},
        "wdl_sides",
    )
    h2h = dict(
        home=prices[event["home_team"]],
        draw=prices["Draw"],
        away=prices[event["away_team"]],
    )
    require(
        len(ou["outcomes"]) == 2
        and {r["name"]: (r["point"], r["price"]) for r in ou["outcomes"]}
        == {"Over": (q["line"], q["over_odds"]), "Under": (q["line"], q["under_odds"])},
        "total_pair",
    )
    total = dict(line=q["line"], over=q["over_odds"], under=q["under_odds"])
    return quote_only_matrices(h2h, total)[1].tolist(), q["line"]


def replay(repo):
    with tempfile.TemporaryDirectory() as work:
        tmp = Path(work)
        with evidence(repo, tmp) as archive:
            # Write only the designated numerical evidence members into this temporary directory.
            for member in archive:
                if not member.name.startswith(ROOT):
                    continue
                name = member.name[len(ROOT) :]
                if (
                    name == "ucl-evidence/recentxg_injury_rest.json"
                    or re.fullmatch(
                        r"ucl-evidence/raw/(teams|matchDetails)-[0-9]+\.json", name
                    )
                    or name in {f"fixtures/{mid}.json" for mid in TARGETS}
                ):
                    require(member.isfile(), "source_type")
                    dest = tmp / name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(archive.extractfile(member).read())
            records, coverage = run(tmp)
            original = json.dumps(
                records, separators=(",", ":"), allow_nan=False
            ).encode()
            require(
                sha256(original).hexdigest()
                == json.loads(LOCK.read_bytes())["outcome_free_matrix_bytes_sha256"],
                "blind_matrix_lock",
            )
            raw = archive.extractfile(ROOT + "ucl-odds.json").read()
            (capture,) = [
                c
                for c in packed_value(archive, ROOT + "collection-manifest.json")[
                    "odds_api_queries"
                ]
                if c["saved_file"] == "ucl-odds.json"
            ]
            require(
                capture["status"] == 200
                and sha256(raw).hexdigest() == capture["saved_file_sha256"],
                "market_capture",
            )
            events = json.loads(raw)
            crosswalk = packed_value(archive, ROOT + "fixture-index.json")["events"]
            for row in records:
                mid = row["id"].split(":")[1]
                f = json.loads((tmp / f"fixtures/{mid}.json").read_bytes())
                model = packed_value(archive, ROOT + f"models/{mid}.json")
                receipt = model["freeze_receipt"]
                identity = dict(
                    match_id=row["id"], stage="prematch", track="current-test-pair"
                )
                record = verify(
                    packed_value(
                        archive,
                        "WCPP/forecasts/issued-analyses/"
                        + digest(identity)
                        + "/000001.json",
                    )
                )
                require(
                    all(record[k] == v for k, v in identity.items())
                    and record["revision"] == 1
                    and record["previous_sha256"] is None
                    and record["evaluation_slot"]
                    == receipt["evaluation_slot"]
                    == "FIRST_PREMATCH"
                    and receipt["sha256"] == record["record_sha256"]
                    and receipt["freeze_id"] == record["freeze_id"]
                    and all(
                        f[k] == record["fixture"][k]
                        for k in (
                            "match_id",
                            "home",
                            "away",
                            "cohort",
                            "stage",
                            "cutoff_at",
                            "kickoff_at",
                        )
                    )
                    and clock(f["cutoff_at"])
                    <= clock(record["recorded_at"])
                    < clock(f["kickoff_at"]),
                    "first_matrix",
                )
                current = record["payload"]["current_model"]
                require(
                    current["market_numeric_adjustment_applied"] is False
                    and current["matrix_hash"]
                    == digest(current["matrix"])
                    == model["current_model"]["matrix_hash"]
                    and score_picks(current["matrix"])["primary"] == current["primary"],
                    "team_matrix",
                )
                (mapped,) = [r for r in crosswalk if str(r["espn_event_id"]) == mid]
                row["market"], row["line"] = market(f, mapped, capture, events)
                row.update(
                    team=current["matrix"],
                    home=f["home"],
                    away=f["away"],
                    main_asian_line_verified=f["total_quote"][
                        "asian_main_line_verified"
                    ],
                )
    return records, coverage


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-repo", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    args = p.parse_args()
    rows, coverage = replay(args.data_repo)
    with args.out.open("x") as f:
        json.dump(rows, f, separators=(",", ":"), allow_nan=False)
    print(json.dumps(coverage, indent=2, sort_keys=True))
