"""Use #78 retention for bytes already received normally; report one prior, never a full pair."""

import argparse
from datetime import timedelta
from hashlib import sha256
import json
import re
from pathlib import Path

from located import page_detail, profile


def keep(body, receipt, calendar, team, target, cutoff, root, *, calendar_received):
    from intake import valid_receipt
    from opportunities import clock, pair, recent_three, require
    from retain import retain

    rows = calendar["fixtures"]["allFixtures"]["fixtures"]
    target_rows = [r for r in rows if str(r["id"]) == str(target["id"])]
    require(len(target_rows) == 1, "target_not_in_calendar")
    sides = (str(target["home"]), str(target["away"]))
    require(team in sides and pair(target_rows[0]) == sides, "target_sides")
    require(
        target_rows[0]["tournament"]["leagueId"] == target["league_id"],
        "target_competition",
    )
    kickoff = clock(target["kickoff"])
    require(cutoff < kickoff, "target_cutoff")
    history = recent_three(calendar, team, str(target["id"]), sides, kickoff, cutoff)
    oldest = clock(history[-1]["status"]["utcTime"]) if len(history) == 3 else None
    check_calendar_time(calendar, calendar_received, cutoff, str(target["id"]), oldest)
    detail = page_detail(body)
    fid = str(detail["general"]["matchId"])
    matches = [r for r in history if str(r["id"]) == fid]
    require(len(matches) == 1, "not_a_previous_three_fixture")
    require(
        valid_receipt({receipt["url"]: receipt}, receipt["url"], fid, cutoff),
        "unusable_receipt",
    )
    found = profile(
        detail,
        matches[0],
        clock(receipt["received_at"]),
        cutoff,
        str(target["id"]),
        team,
    )
    proof = retain(root, body, receipt, fid, cutoff)
    return {
        "prior": found,
        "native_target": str(target["id"]),
        "team": team,
        "position_in_calendar": history.index(matches[0]) + 1,
        "raw_sha256": receipt["sha256"],
        "retention_receipt": proof.name,
        "coverage_unit": "one prior slot",
    }


def bind_slot(root, saved, target, cal_body, cal_receipt, indexed_at, cutoff):
    """Retain the exact calendar and this use of the prior. Never create missing links late."""
    from opportunities import clock, require
    from retain import now, publish_once

    digest = sha256(cal_body).hexdigest()
    require(digest == cal_receipt["sha256"], "calendar_digest")
    calendar = root / "objects" / (digest + ".bin")
    coordinates = {
        k: target[k] for k in ("id", "home", "away", "league_id", "kickoff", "cutoff")
    }
    binding = {
        "schema": 1,
        "target": coordinates,
        "team": saved["team"],
        "position": saved["position_in_calendar"],
        "prior": saved["prior"]["prior"],
        "prior_raw_sha256": saved["raw_sha256"],
        "prior_receipt": saved["retention_receipt"],
        "calendar": {
            k: cal_receipt[k]
            for k in ("url", "query_time_utc", "status", "bytes", "sha256")
        },
        "calendar_indexed_at": indexed_at.isoformat(),
    }
    body = json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()
    identity = sha256(body).hexdigest()
    receipt = root / "slot-receipts" / (identity + ".json")
    if not receipt.exists():
        require(indexed_at <= now() <= cutoff, "late_first_slot_binding")
        publish_once(calendar, cal_body)
        require(
            sha256(calendar.read_bytes()).hexdigest() == digest,
            "stored_calendar_digest",
        )
        at = now()
        require(indexed_at <= at <= cutoff, "late_first_slot_binding")
        publish_once(
            receipt,
            json.dumps(
                {"binding": binding, "local_verified_at": at.isoformat()},
                sort_keys=True,
            ).encode(),
        )
    original = json.loads(receipt.read_bytes())
    require(
        original["binding"] == binding
        and indexed_at <= clock(original["local_verified_at"]) <= cutoff
        and calendar.is_file()
        and sha256(calendar.read_bytes()).hexdigest() == digest,
        "retained_calendar_or_binding_incomplete",
    )
    return receipt.name


def check_calendar_time(calendar, received, cutoff, target_id, oldest=None):
    """A calendar predating a now-finished or possibly finished intervening game cannot certify recency."""
    from opportunities import clock, require

    for row in calendar["fixtures"]["allFixtures"]["fixtures"]:
        status = row["status"]
        if (
            str(row["id"]) == target_id
            or status.get("cancelled")
            or status.get("awarded")
        ):
            continue
        start = clock(status["utcTime"])
        name = row.get("tournament", {}).get("name", "")
        if re.search(
            r"friendly|friendlies|youth|under.?\d+|\bu[- ]?\d{2}\b|\breserves?\b",
            name,
            re.I,
        ):
            continue
        if start > cutoff or start < (oldest or cutoff - timedelta(days=45)):
            continue
        earliest_end = start + timedelta(minutes=90)
        if status.get("finished"):
            require(earliest_end < received, "calendar_finish_before_observation")
        elif earliest_end <= cutoff:
            raise ValueError("unresolved_intervening_fixture")


def main():
    from opportunities import check_source, clock, require
    from retain import retain

    p = argparse.ArgumentParser(description=__doc__)
    for key in ("raw", "receipt", "target", "private-dir"):
        p.add_argument("--" + key, type=Path, required=True)
    p.add_argument(
        "--prior-id",
        required=True,
        help="previous native match ID from the normal reading list",
    )
    p.add_argument("--calendar-index", type=Path)
    p.add_argument("--calendar-raw-dir", type=Path)
    p.add_argument("--team", help="native FotMob team ID for a calendar binding")
    args = p.parse_args()
    target = json.loads(args.target.read_bytes())
    cutoff = clock(target["cutoff"])
    require(
        args.prior_id.isdecimal()
        and args.prior_id != str(target["id"])
        and cutoff < clock(target["kickoff"]),
        "target_cannot_be_a_prior",
    )
    raw, original_receipt = args.raw.read_bytes(), json.loads(args.receipt.read_bytes())
    proof = retain(args.private_dir, raw, original_receipt, args.prior_id, cutoff)
    if not (args.calendar_index and args.calendar_raw_dir and args.team):
        print(
            json.dumps(
                {
                    "status": "raw_only",
                    "reason": "verified_calendar_not_supplied",
                    "eligible_slots": 0,
                    "retention_receipt": proof.name,
                    "raw_sha256": original_receipt["sha256"],
                },
                sort_keys=True,
            )
        )
        return
    index = json.loads(args.calendar_index.read_bytes())
    indexed_at = clock(index["queried_at_utc"])
    name = "teams-" + args.team + ".json"
    matches = [r for r in index["source_queries"] if Path(r["raw_file"]).name == name]
    require(len(matches) == 1, "calendar_receipt")
    calendar = check_source(
        matches[0], args.calendar_raw_dir, "teams", args.team, cutoff, indexed_at
    )
    result = keep(
        raw,
        original_receipt,
        calendar,
        args.team,
        target,
        cutoff,
        args.private_dir,
        calendar_received=clock(matches[0]["query_time_utc"]),
    )
    result["status"] = "calendar_bound_local_slot"
    result["slot_receipt"] = bind_slot(
        args.private_dir,
        result,
        target,
        (args.calendar_raw_dir / name).read_bytes(),
        matches[0],
        indexed_at,
        cutoff,
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
