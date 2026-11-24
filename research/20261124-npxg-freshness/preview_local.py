"""Check manually captured Understat data against an independent completed fixture calendar; never publish predictions."""

import argparse
import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from audit import (
    club_map,
    dump,
    linked_histories,
    observations_by_fixture,
    sha,
    side_coverage,
    unpack_native,
    utc,
)
from capture_local import BASE, LEAGUES, normalize_bytes


def local_versions(cache, code, season, deadline):
    snapshots = []
    reasons = []
    read_now = datetime.now(timezone.utc)
    for path in sorted((cache / "receipts").glob("*.json")):
        body = path.read_bytes()
        if path.stem != sha(body):
            raise ValueError("Receipt contents do not match its identity")
        r = json.loads(body)
        if r.get("kind") == "source_policy":
            reasons.append(r["state"])
            continue
        if r.get("competition") != code or r.get("season") != season:
            continue
        reasons.append(r.get("state"))
        if r.get("state") != "NATIVE_LEAGUE_DATA":
            continue
        observed = utc(r["received_at"])
        if observed > deadline:
            reasons.append("AFTER_THIS_CUTOFF")
            continue
        if (
            observed > read_now
            or utc(r["start_at"]) > observed
            or r.get("status") != 200
            or r.get("source")
            != BASE
            + "/getLeagueData/"
            + quote(LEAGUES[code], safe="")
            + "/"
            + str(season)
        ):
            raise ValueError("Local source, clock, or league mismatch")
        name = "objects/" + r["wire_sha256"] + ".bin"
        if r["raw"] != name:
            raise ValueError("Unexpected local object path")
        wire = (cache / name).read_bytes()
        decoded = normalize_bytes(wire, None)
        if sha(wire) != r["wire_sha256"] or sha(decoded) != r["decoded_sha256"]:
            raise ValueError("Local capture bytes differ")
        snap = unpack_native(decoded, code, observed, observed, sha(decoded))
        snap["receipt_sha"] = sha(body)
        snapshots.append(snap)
    return snapshots, reasons


def load_calendar(meta_path, code, season, deadline):
    meta = json.loads(meta_path.read_bytes())
    observed = utc(meta["received_at"])
    raw = (meta_path.parent / meta["file"]).read_bytes()
    if (
        not meta.get("calendar_provider")
        or "understat" in meta["calendar_provider"].casefold()
    ):
        raise ValueError("An independently sourced calendar is required")
    if (
        sha(raw) != meta["sha256"]
        or meta["competition"] != code
        or meta["season"] != season
    ):
        raise ValueError("Independent calendar identity or hash differs")
    if observed > deadline:
        return None
    if meta.get("complete_league_calendar_as_of") is not True:
        return None
    # The as-of calendar must account for all fixtures already played or scheduled in the league. Postponements and unresolved past statuses stay explicit.
    rows = {}
    ids = set()
    for r in csv.DictReader(raw.decode("utf-8-sig").splitlines()):
        k = r["home"], r["away"]
        event = r["fixture_id"]
        kick = utc(r["kickoff_at"])
        status = r["status"]
        if (
            status not in ("FT90", "SCHEDULED", "POSTPONED", "UNKNOWN")
            or k in rows
            or not event
            or event in ids
        ):
            raise ValueError("Ambiguous date/identity/status in calendar")
        ids.add(event)
        if status == "FT90" and kick + timedelta(minutes=90) > observed:
            raise ValueError("Calendar put a final score before the match finished")
        rows[k] = dict(
            day=kick.date().isoformat(),
            kickoff=kick,
            finished=status == "FT90",
            status=status,
            event_id=event,
            source=meta["sha256"],
        )
    return rows


def preview(cache, code, season, uid, calendar_meta, clock=None):
    check_time = clock or datetime.now(timezone.utc)
    if check_time > datetime.now(timezone.utc) + timedelta(seconds=1):
        raise ValueError("Preview clock cannot be in the future")
    versions, reasons = local_versions(cache, code, season, check_time)
    base = dict(
        competition=code,
        understat_id=uid,
        checked_at=check_time.isoformat(),
        source_receipt_states=sorted(set(reasons)),
        prediction_issued=False,
    )
    if not versions:
        return base | dict(status="NO_NATIVE_SNAPSHOT_BY_CHECK")
    if not calendar_meta:
        return base | dict(status="MISSING_INDEPENDENT_CALENDAR")
    cal = load_calendar(calendar_meta, code, season, check_time)
    if cal is None:
        return base | dict(status="CALENDAR_UNAVAILABLE_AT_CHECK")
    recent = max(versions, key=lambda x: x["observed"])
    mapping, _ = club_map(recent, cal)
    linked = linked_histories(cal, mapping, recent)
    if len(linked) != len(recent["pairs"]) or {
        (g["home"], g["away"]) for g in linked
    } != set(recent["pairs"]):
        return base | dict(status="INCOMPLETE_INDEPENDENT_CALENDAR")
    matched = [g for g in linked if g["id"] == uid]
    if len(matched) != 1:
        return base | dict(status="NO_UNIQUE_TARGET_IDENTITY")
    game = matched[0]
    cut = game["kickoff"] - timedelta(minutes=60)
    effective = min(check_time, cut)
    # Get the original calendar and feature receipts available by the *earlier* instant as well.
    if load_calendar(calendar_meta, code, season, effective) is None:
        return base | dict(status="CALENDAR_AFTER_CUTOFF")
    versions = [v for v in versions if v["observed"] <= effective]
    if not versions:
        return base | dict(status="SOURCE_AFTER_CUTOFF", cutoff=cut.isoformat())
    recent = max(versions, key=lambda x: x["observed"])
    mapping, _ = club_map(recent, cal)
    linked = linked_histories(cal, mapping, recent)
    again = [g for g in linked if g["id"] == uid]
    if len(again) != 1 or (again[0]["home"], again[0]["away"]) != (
        game["home"],
        game["away"],
    ):
        return base | dict(status="IDENTITY_NOT_BOUND_AT_CUTOFF")
    if (
        game.get("status") != "SCHEDULED"
        or recent["pairs"][(game["home"], game["away"])]["completed"]
    ):
        return base | dict(status="TARGET_NOT_PENDING_IN_CAPTURE")
    if any(
        g.get("status") in ("UNKNOWN", "SCHEDULED")
        and effective < g["kickoff"] < cut
        and (
            game["home"] in (g["home"], g["away"])
            or game["away"] in (g["home"], g["away"])
        )
        for g in linked
    ):
        return base | dict(
            status="INTERVENING_GAME_BEFORE_FINAL_CUTOFF", cutoff=cut.isoformat()
        )
    # For early previews, withhold any games that have not yet been completed and observed by the check.
    rows = [g for g in linked if g["status"] != "POSTPONED"]
    history = observations_by_fixture(versions)
    sides = [
        side_coverage(rows, game[k], effective, uid, history, True)
        for k in ("home", "away")
    ]
    return base | dict(
        status="BOTH_SIX"
        if all(s["complete"] for s in sides)
        else "INSUFFICIENT_EXACT_LAST_SIX",
        stage="EARLY_COVERAGE_PREVIEW" if check_time < cut else "CUTOFF_COVERAGE_ONLY",
        cutoff=cut.isoformat(),
        effective_cutoff=effective.isoformat(),
        sides=sides,
        authority="local capture receipts, not independent delivery or source publication",
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cache", required=True, type=Path)
    p.add_argument("--competition", required=True, choices=LEAGUES)
    p.add_argument("--season", type=int, required=True)
    p.add_argument("--understat-id", required=True)
    p.add_argument("--calendar-meta", type=Path)
    p.add_argument("--out", required=True, type=Path)
    a = p.parse_args()
    r = preview(a.cache, a.competition, a.season, a.understat_id, a.calendar_meta)
    dump(a.out, r)
    print(r["status"])
