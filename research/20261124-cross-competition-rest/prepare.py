"""Write a complete input/forecast ledger. This command has no results input."""

import argparse
import json
from collections import Counter
from pathlib import Path

import numpy as np
from archives import (
    PACKETS,
    Packet,
    add_nov5_rounds,
    add_phase_sources,
    output,
    r7_roster,
    sha,
    utc,
)
from calendars import LEAGUES, EvidenceGap, target_detail, team_calendar
from rest_model import market, pair_matrices

HERE = Path(__file__).resolve().parent
PROTOCOL_SHA = "bd821af3b1d05934caad8692d2c7662a44dd31f272b90b6362182b69672b9642"
DOMESTIC = set(LEAGUES) | {"aus.1", "kor.1", "jpn.1"}


def collect(packet, first, registry, excluded, seen, out):
    f = first["fixture"]
    mid = f["match_id"]
    co = f["competition"]
    cut = utc(f["cutoff_at"])
    kick = utc(f["kickoff_at"])
    row = {
        k: f.get(k)
        for k in (
            "match_id",
            "kickoff_at",
            "cutoff_at",
            "cohort",
            "competition",
            "stage",
            "home",
            "away",
        )
    }
    row.update(
        packet=packet.tag,
        first_sha256=first["record_sha256"],
        period="early" if kick.date().isoformat() <= "2026-11-15" else "late",
    )
    if co not in DOMESTIC or f["stage"] != "regular":
        return dict(row, status="OUTSIDE_STUDY_SCOPE")
    if not f.get("home") or not f.get("away"):
        return dict(row, status="ORIGINAL_TEAM_IDENTITY_MISSING")
    if mid in excluded[0] or (f["home"], f["away"], kick) in excluded[1]:
        return dict(row, status="R7_EXCLUDED")
    if not "2026-10-12" <= kick.date().isoformat() <= "2026-11-23":
        return dict(row, status="OUTSIDE_STUDY_WINDOW")
    if mid in seen:
        if seen[mid] != (first["record_sha256"], f["home"], f["away"], kick):
            raise ValueError("Conflicting firsts across selected packets")
        return dict(row, status="DUPLICATE_FIRST")
    seen[mid] = (first["record_sha256"], f["home"], f["away"], kick)
    pair = packet.pairs.get(mid)
    if not pair or not pair["payload"].get("test_model"):
        return dict(row, status="NO_ORIGINAL_TEST_PAIR")
    fx = pair["fixture"]
    test = pair["payload"]["test_model"]
    if (
        any(
            f[k] != fx[k]
            for k in ("home", "away", "cohort", "competition", "stage", "neutral")
        )
        or utc(fx["kickoff_at"]) != kick
        or not all(
            utc(v["fixture"]["cutoff_at"]) <= utc(v["recorded_at"]) < kick
            for v in (first, pair)
        )
        or utc(fx["cutoff_at"]) > cut
        or not cut < kick
    ):
        return dict(row, status="ORIGINAL_IDENTITY_OR_CLOCK_CONFLICT")
    row.update(pair_sha256=pair["record_sha256"], original_model_cutoff=fx["cutoff_at"])
    if test.get("version") != "WCPP-ML-0.1":
        return dict(row, status="NO_FIXED_V6_ARTIFACT")
    if co not in LEAGUES:
        return dict(row, status="NO_RETAINED_NATIVE_STAGE_ADAPTER")
    if f["cohort"] not in registry:
        return dict(row, status="NO_REGISTERED_ARTIFACT")
    fm = str(
        f.get("fotmob_fixture_id")
        or (f.get("source_ids") or {}).get("fotmob")
        or fx.get("fotmob_fixture_id")
        or (fx.get("source_ids") or {}).get("fotmob")
        or ""
    )
    try:
        clubs, row["identity_sha256"] = target_detail(packet, f, fm, cut)
        row["calendars"] = {}
        for side, team in zip(("home", "away"), clubs):
            refs = packet.available("team", team, cut)
            if not refs:
                row["calendars"][side] = {"error": "NO_PRE_CUTOFF_TEAM_CALENDAR"}
                continue
            at, h = refs[0]
            try:
                row["calendars"][side] = team_calendar(
                    packet, packet.json(h), at, h, f, team, fm, clubs, cut
                )
            except EvidenceGap as err:
                row["calendars"][side] = {"error": str(err)}
        if any("error" in s for s in row["calendars"].values()):
            raise EvidenceGap("INCOMPLETE_BILATERAL_CALENDAR")
        b, c, row["model"] = pair_matrices(
            registry[f["cohort"]], test, fx, cut, list(row["calendars"].values())
        )
        row["market"], row["market_gap"] = market(packet, f)
        filename = mid.replace(":", "-") + ".npy"
        with (out / filename).open("xb") as stream:
            np.save(stream, np.stack([b, c]), allow_pickle=False)
        row.update(
            status="LOCKED_PAIR",
            matrix_file=filename,
            matrix_file_sha256=sha((out / filename).read_bytes()),
        )
    except EvidenceGap as err:
        row["status"] = str(err)
    return row


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--packets",
        type=Path,
        required=True,
        help="Directory with nov6.tar.gz, nov7.tar.gz, nov8.tar.gz and nov22.tar.gz",
    )
    ap.add_argument(
        "--europe-review",
        type=Path,
        required=True,
        help="November 6 retained earlier round archive",
    )
    ap.add_argument(
        "--reports", type=Path, required=True, help="Historical WCPP reports root"
    )
    ap.add_argument("--registry", type=Path, required=True)
    ap.add_argument("--r7", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    if sha((HERE / "protocol.json").read_bytes()) != PROTOCOL_SHA:
        raise ValueError("Protocol changed")
    registry = json.loads(args.registry.read_text())["models"]
    excluded = r7_roster(args.r7)
    args.out.mkdir(parents=True, exist_ok=False)
    records = []
    seen = {}
    for tag in PACKETS:
        packet = Packet(tag, args.packets / (tag + ".tar.gz"))
        add_phase_sources(packet, args.reports)
        add_nov5_rounds(packet, args.europe_review)
        for mid, first in sorted(packet.firsts.items()):
            records.append(collect(packet, first, registry, excluded, seen, args.out))
        print(
            tag,
            dict(Counter(r["status"] for r in records if r["packet"] == tag)),
            flush=True,
        )
    output(args.out / "roster.json", records)
    output(
        args.out / "lock.json",
        dict(
            protocol_sha256=PROTOCOL_SHA,
            roster_sha256=sha((args.out / "roster.json").read_bytes()),
            r7_exclusion_sha256=sha(args.r7.read_bytes()),
            packets=PACKETS,
        ),
    )


if __name__ == "__main__":
    main()
