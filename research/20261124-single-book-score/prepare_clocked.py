"""Rebuild same-book prices from a frozen total, its response bytes and collection receipt."""

import argparse
import json
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

from competition_scope import permitted
from research.analysis_freeze import aware
from research_program.future_validation import quote_audit
from research_program.market_baseline import _paired_capture

from inputs import PROTOCOL, sha
from prepare_history import seal


def select_reader(path, include_labels=False):
    # These readers already authenticated the same native packets. Pin their actual bytes.
    hashes = {
        "archives.py": "92db16529a1796905d7cf9508c49e44106db304fbb0a84481110b7d939d1b8d7",
        "calendars.py": "1e7ca9df183022dc8f08304220e561aed2cf4422ea6c44dc0d1c918c3f04f9ab",
    }
    if include_labels:
        hashes.update({"grade.py": "76369571ee98a6108c9f5c21f2023a5841878393775b752abcb0fdb45ed1bafb",
                       "prepare.py": "c75d2dba27ca1f99b161bf649b67d00c7ae2662b6bcb371bc6a636546b64db36",
                       "rest_model.py": "84fc874f2daeff66082b8cde68f5db1bb17824c88bdd4019127c7683d63aef9d"})
    if any(sha(path / name) != expected for name, expected in hashes.items()):
        raise ValueError("Native archive reader edition differs")
    sys.path.insert(0, str(path.resolve()))


def norm(s):
    return "".join(c for c in unicodedata.normalize("NFKD", str(s or "").casefold()) if c.isalnum())


def receipt_index(packet):
    index = defaultdict(list)
    for file, member in packet.members.items():
        if "/markets/" not in file or not file.endswith(".meta.json"):
            continue
        meta = packet.json(member)
        h = meta.get("response_sha256_raw") or meta.get("response_sha256") or meta.get("sha256")
        source = urlsplit(meta.get("request_url_redacted") or meta.get("url") or meta.get("url_without_key") or "")
        if meta.get("http_status") != 200 or source.scheme != "https" or source.hostname != "api.the-odds-api.com" or h not in packet.blobs:
            continue
        started = meta.get("query_time_utc") or meta.get("query_time")
        if started:
            index[h].append((source.path.rstrip("/"), aware(started), aware(meta.get("completed_at_utc") or started), member))
    return index


def bound_names(packet, f, hashes):
    espn, fotmob = [packet.json(h) for h in hashes]
    event = espn["header"] if "header" in espn else next(e for e in espn["events"] if str(e["id"]) == f["match_id"][5:])
    game = event["competitions"][0]
    result = {}
    for side in ("home", "away"):
        team = next(t["team"] for t in game["competitors"] if t["homeAway"] == side)
        names = [f.get(side + "_name"), fotmob["general"][side + "Team"].get("name")]
        names.extend(team.get(key) for key in ("displayName", "shortDisplayName", "name", "abbreviation"))
        result[side] = {norm(n) for n in names if n}
    return result


def reconstruct(f, raw, receipts, names):
    """Rehydrate the public market matcher in memory; no mutation of the original packet."""
    q = f["total_quote"]
    h = q.get("source_file_sha256") or q.get("source_sha256")
    capture, cut = aware(q["query_time"]), aware(f["cutoff_at"])
    source = urlsplit(q["source_url"])
    expected_path = source.path.rstrip("/")
    hashes = sorted({digest for path, t, end, digest in receipts
                     if path == expected_path and t == capture and t <= end <= cut})
    if not hashes or source.hostname != "api.the-odds-api.com" or source.scheme != "https":
        raise ValueError("NO_MATCHING_SOURCE_RECEIPT")
    provider = (f.get("source_ids") or {}).get("the_odds")
    if not provider or provider != q.get("source_event_id"):
        raise ValueError("PROVIDER_EVENT_BRIDGE")
    events = [e for e in raw if e["id"] == provider]
    if len(events) != 1:
        raise ValueError("RAW_EVENT_ID")
    event = events[0]
    if aware(event["commence_time"]) != aware(f["kickoff_at"]) or expected_path != f"/v4/sports/{event['sport_key']}/odds":
        raise ValueError("PROVIDER_KICKOFF_OR_COMPETITION")
    teams = {side: event[side + "_team"] for side in ("home", "away")}
    if any(norm(name) not in names[side] or norm(name) in names[other]
           for side, other in (("home", "away"), ("away", "home")) for name in (teams[side],)):
        raise ValueError("NO_EXACT_SIDED_TEAM_ALIAS")
    books = [b for b in event["bookmakers"]
             if q["bookmaker"].casefold() in {b["key"].casefold(), b["title"].casefold()}]
    if len(books) != 1:
        raise ValueError("AMBIGUOUS_OR_ABSENT_BOOK")
    snapshots = []
    for market in books[0]["markets"]:
        key = market["key"]
        if key not in ("h2h", "totals"):
            continue
        wanted = {teams["home"]: "home", "Draw": "draw", teams["away"]: "away"} if key == "h2h" else {"Over": "over", "Under": "under"}
        sides = [v for v in market["outcomes"] if key == "h2h" or v.get("point") == q["line"]]
        if len(sides) != len(wanted) or set(v["name"] for v in sides) != wanted.keys():
            raise ValueError("MISSING_OR_DUPLICATE_PRICE")
        when = market.get("last_update") or books[0].get("last_update")
        if not when:
            raise ValueError("NO_SOURCE_PRICE_CLOCK")
        snapshots.append(dict(match_id=f["match_id"], bookmaker=q["bookmaker"], provider_match_id=provider,
            source_url=q["source_url"], source_sha256=h, query_time=q["query_time"], quoted_at=when,
            market="1x2" if key == "h2h" else "totals", line=None if key == "h2h" else q["line"],
            period="regulation_90", decimal_odds={wanted[v["name"]]: v["price"] for v in sides}))
    if sorted(v["market"] for v in snapshots) != ["1x2", "totals"]:
        raise ValueError("MISSING_OR_DUPLICATE_RAW_MARKET")
    pair, error = _paired_capture(dict(f, market_snapshots=[*(f.get("market_snapshots") or []), *snapshots]),
                                  dict(q, source_sha256=h, provider_match_id=provider))
    if error:
        raise ValueError(error)
    return dict(h2h=pair["h2h"]["decimal_odds"], total=dict(line=q["line"], **pair["total"]["decimal_odds"]),
                book=books[0]["key"], provider_id=provider, raw_sha256=h, receipts=hashes,
                quote_home_draw_away_at=pair["h2h"]["quoted_at"], quote_total_at=pair["total"]["quoted_at"],
                queried_at=q["query_time"])


def clocked_row(packet, receipts, first, r7):
    from calendars import target_detail, LEAGUES
    f = first["fixture"]
    row = {k: f.get(k) for k in ("match_id", "home", "away", "competition", "cohort", "stage", "kickoff_at", "cutoff_at")}
    row.update(freeze_id=first["freeze_id"], first_sha256=first["record_sha256"], packet=packet.tag, date=f["kickoff_at"][:10])
    try:
        if not permitted(f):
            raise ValueError("OUTSIDE_SCOPE")
        if not f.get("home") or not f.get("away"):
            raise ValueError("MISSING_TEAM_ID")
        kick, cut = aware(f["kickoff_at"]), aware(f["cutoff_at"])
        if not cut <= aware(first["recorded_at"]) < kick:
            raise ValueError("FIRST_FREEZE_CLOCK")
        if f["match_id"] in r7[0] or (f["home"], f["away"], kick) in r7[1]:
            raise ValueError("R7_OVERLAP")
        if f["competition"] not in LEAGUES:
            raise ValueError("NO_NATIVE_STAGE_READER")
        fm = str((f.get("source_ids") or {}).get("fotmob") or f.get("fotmob_fixture_id") or "")
        _, proofs = target_detail(packet, f, fm, cut)
        audit = quote_audit(f)
        if not audit["eligible"]:
            raise ValueError("+".join(audit["reasons"]))
        q = audit["quote"]
        h = q.get("source_file_sha256") or q.get("source_sha256")
        if h not in packet.blobs:
            raise ValueError("MISSING_RAW_MARKET_BYTES")
        row.update(reconstruct(f, packet.json(h), receipts[h], bound_names(packet, f, proofs)), identity_sha256=proofs)
    except ValueError as e:
        row["gap"] = str(e)
    return row


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("reader", "packets", "r7", "history", "out"):
        p.add_argument("--" + name, type=Path, required=True)
    args = p.parse_args()
    select_reader(args.reader)
    from archives import Packet, r7_roster
    r7 = r7_roster(args.r7)
    rows = []
    seen = {}
    for tag in PROTOCOL["clocked_replay"]["packet_sha256"]:
        packet = Packet(tag, args.packets / (tag + ".tar.gz"))
        receipts = receipt_index(packet)
        for mid, first in sorted(packet.firsts.items()):
            if mid in seen:
                if first["record_sha256"] != seen[mid]["first_sha256"]:
                    raise ValueError("Conflicting firsts across early packets")
                seen[mid].setdefault("also_in", []).append(tag)
                continue
            row = clocked_row(packet, receipts, first, r7)
            seen[mid] = row
            rows.append(row)
    fit = json.loads((args.history / "fit.json").read_text())
    lock = json.loads((args.history / "lock.json").read_text())
    if fit["weight"] != lock["lambda_fitted"] or fit["source_sha256"] != PROTOCOL["source_assets_sha256"]:
        raise ValueError("Historical fit differs")
    seal(rows, fit["weight"], args.out)


if __name__ == "__main__":
    main()
