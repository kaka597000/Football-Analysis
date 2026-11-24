"""Read-only coverage of a retained rendered-page summary at its actual handoff clock."""

import argparse
from collections import Counter
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

from opportunities import clock, require


def valid_receipt(receipts, url, match_id, cutoff):
    receipt = receipts.get(url)
    u = urlsplit(url)
    if not (
        receipt
        and receipt["url"] == url
        and u.scheme == "https"
        and u.netloc == "www.fotmob.com"
        and re.fullmatch(r"(?:/[a-zA-Z-]+)?/matches/[^/]+/[^/]+", u.path)
        and parse_qs(u.query).get("matchId") == [str(match_id)]
        and receipt["http_status"] == 200
        and re.fullmatch(r"[a-f0-9]{64}", receipt["sha256"])
        and clock(receipt["requested_at"]) <= clock(receipt["received_at"]) <= cutoff
    ):
        return None
    return receipt


def metric(row, key):
    pair = row.get("metrics_for_against", {}).get(key)
    return (
        isinstance(pair, list)
        and len(pair) == 2
        and all(type(v) in (int, float) and isfinite(v) and v >= 0 for v in pair)
    )


def inventory(bundle, cutoff, objects=None):
    fields = (
        "expected_goals_non_penalty",
        "total_shots",
        "ShotsOnTarget",
        "shots_inside_box",
        "big_chance",
    )
    slots, teams, fixtures = Counter(), Counter(), Counter()
    events, qualified, originals = set(), {k: set() for k in fields}, set()
    require(bundle["per_team_recent_window"] == 3, "window_mismatch")
    for e in bundle["events"]:
        tid = str(e["fotmob_match_id"])
        require(
            tid not in events and str(e["home"]["id"]) != str(e["away"]["id"]),
            "duplicate_fixture_or_team",
        )
        events.add(tid)
        kickoff = clock(e["kickoff_utc"])
        require(
            cutoff < kickoff
            and e["uefa_prematch_status"] == "Upcoming"
            and valid_receipt(bundle["receipts"], e["fotmob_match_url"], tid, cutoff),
            "preview_identity_or_clock",
        )
        counts = []
        for side in ("home", "away"):
            t = e[side]
            expected = sorted(
                t["last5"], key=lambda v: clock(v["kickoff"]), reverse=True
            )[:3]
            history = t["recent_process"]
            require(
                len(expected) == len(history) == 3
                and len({r["fixture"] for r in expected}) == 3,
                "incomplete_or_duplicate_schedule",
            )
            count = Counter()
            for r, s in zip(history, expected):
                fid = str(r["fixture"])
                require(
                    fid != tid
                    and fid == str(s["fixture"])
                    and r["source_url"] == s["url"]
                    and clock(r["kickoff"]) == clock(s["kickoff"]) < cutoff
                    and s["team_side"] in ("home", "away")
                    and s[s["team_side"]] == t["name"]
                    and s["away" if s["team_side"] == "home" else "home"]
                    == r["opponent"],
                    "schedule_identity",
                )
                slots["planned"] += 1
                receipt = valid_receipt(
                    bundle["receipts"], r["source_url"], fid, cutoff
                )
                if not receipt or r.get("receipt_at") != receipt["received_at"]:
                    continue
                if isinstance((r.get("shotmap") or {}).get("shots"), list):
                    slots["native_vector_field"] += 1
                if objects is not None:
                    p = objects / (receipt["sha256"] + ".bin")
                    if p.is_file():
                        require(
                            sha256(p.read_bytes()).hexdigest() == receipt["sha256"],
                            "raw_digest",
                        )
                        originals.add(receipt["sha256"])
                for field in fields:
                    if metric(r, field):
                        slots[field] += 1
                        count[field] += 1
                        qualified[field].add(fid)
            counts.append(count)
            teams.update(k for k in fields if count[k] == 3)
        fixtures.update(k for k in fields if all(c[k] == 3 for c in counts))
    return dict(
        fixtures=len(events),
        teams=2 * len(events),
        planned_slots=slots["planned"],
        by_field={
            k: dict(
                slots=slots[k],
                unique_priors=len(qualified[k]),
                complete_teams=teams[k],
                complete_fixtures=fixtures[k],
            )
            for k in fields
        },
        verified_content_addressed_originals=len(originals)
        if objects is not None
        else None,
        slots_with_native_vector_field=slots["native_vector_field"],
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--summary", type=Path, required=True)
    p.add_argument("--cutoff", required=True)
    p.add_argument(
        "--objects",
        type=Path,
        help="Optional, locally retained originals named by SHA256.bin",
    )
    args = p.parse_args()
    result = inventory(
        json.loads(args.summary.read_bytes()), clock(args.cutoff), args.objects
    )
    print(json.dumps(result, indent=2, sort_keys=True))
