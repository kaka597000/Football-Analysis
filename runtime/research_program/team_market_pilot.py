"""Independent England first issues with observed BTTS and both team totals at 0.5."""

import argparse
from datetime import datetime, timedelta
import gzip
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np

from prediction_engine import ROOT, file_sha256, legacy
from research.analysis_freeze import aware, digest, save, verify
from storage_lock import exclusive_lock
from . import team_market_candidate as team
from . import three_arm_model as grid


BATCH = team.DEFAULT_BATCH / "england"
REGISTRY = "923e34fc44cda33fe965498a24a2435abb37a01a80c539108d8ec20d490bf013"
TRACK = "england-btts-both-teams-scoring"
# Pinned NumPy/SciPy, different x86 BLAS kernels: replayed individual probabilities
# differed by at most 9e-11; differences between two separate KL fits by 1.6e-9.
PROBABILITY_ATOL = 5e-10
ABLATION_ATOL = 5e-9
read = team.read


def verified_raw(base, meta):
    f = (base / meta["source_file"]).resolve()
    if not f.is_relative_to(base.resolve()):
        raise ValueError("source outside input archive")
    raw = f.read_bytes()
    if f.suffix == ".gz":
        raw = gzip.decompress(raw)
    if hashlib.sha256(raw).hexdigest() != meta["source_sha256"]:
        raise ValueError("source checksum differs")
    return json.loads(raw)


def materials(data, batch):
    registration = read(batch / "registration.json")
    run = data / registration["run"]
    targets = read(run / "targets.json")
    requests = read(run / "requests.json")
    summaries = {s["match_id"]: s for s in read(batch / "identity/sources.json")}
    ordered = sorted(
        targets, key=lambda f: (aware(f["kickoff_at"]), f["provider_home"], f["provider_match_id"])
    )
    if (
        registration["discovered_fixture_ids"] != [f["match_id"] for f in ordered]
        or registration["fixture_ids"] != [f["match_id"] for f in ordered[:2]]
        or registration["fixture_ids"] != [meta["match_id"] for meta in requests]
        or set(summaries) != set(registration["fixture_ids"])
    ):
        raise ValueError("England pilot population changed")
    targets = {f["match_id"]: f for f in targets}
    coverage = read(run / "coverage.json")
    if coverage["discovery_competition"] != "eng.1" or len(requests) != 2:
        raise ValueError("different capture competition or quota")
    discovery = read(run / "discovery-receipt.json")
    catalog = next(m for m in discovery["requests"] if m["source_file"] == "discovery-events.json")
    events = {e["id"]: e for e in verified_raw(run, catalog)}
    fixtures = {}
    for mid in summaries:
        f, meta = targets[mid], summaries[mid]
        v = verified_raw(batch / "identity", meta)
        c = v["header"]["competitions"][0]
        ids = {row["homeAway"]: "espn:" + str(row["team"]["id"]) for row in c["competitors"]}
        event = events[f["provider_match_id"]]
        if (
            mid != "espn:" + str(c["id"])
            or aware(c["date"]) != aware(f["kickoff_at"])
            or [f[k] for k in ("home", "away")] != [ids[k] for k in ("home", "away")]
            or type(c.get("neutralSite")) is not bool
            or c["status"]["type"]["state"] != "pre"
            or event["sport_key"] != f["sport_key"]
            or aware(event["commence_time"]) != aware(f["kickoff_at"])
            or [event["home_team"], event["away_team"]] != [f["provider_home"], f["provider_away"]]
        ):
            raise ValueError("ESPN and provider fixture identity, venue or phase changed")
        source = {
            "query_time": meta["captured_at"],
            "source_tier": "L2",
            "source_url": meta["source_url"],
            "response_sha256": meta["source_sha256"],
            "supports_field": ["identity", "venue", "format"],
        }
        fixtures[mid] = dict(f, neutral=c["neutralSite"], sources=[source])
    storage = data / "future/bundle-v2/objects"
    if file_sha256(storage / REGISTRY) != REGISTRY:
        raise ValueError("active registry checksum mismatch")
    route = read(storage / REGISTRY)["models"]["eng.1"]
    asset = storage / route["artifact_sha256"]
    if (
        file_sha256(asset) != route["artifact_sha256"]
        or route.get("current_engine", "legacy_weighted_anchor") != "legacy_weighted_anchor"
    ):
        raise ValueError("English active forecast artifact differs")
    inputs = {
        "registry_sha256": REGISTRY,
        "active_artifact_sha256": route["artifact_sha256"],
        "active_implementation_sha256": file_sha256(ROOT / "v4/main_model.py"),
        "selection_protocol_sha256": digest(registration),
        "adjustment_protocol_sha256": digest(read(batch.parent / "protocol.json")),
    }
    return (
        fixtures,
        run,
        team.verified_source_quotes(run, targets),
        {r["match_id"]: r for r in requests},
        read(asset)["anchor"],
        inputs,
    )


def mainlines(event, meta, fixture, cutoff):
    """Keep complete same-book standard 1X2 and match-total markets from this event."""
    main = {}
    for book in event["bookmakers"]:
        main_offers = [r for r in book["markets"] if r["key"] in ("h2h", "totals")]
        offers = {r["key"]: r for r in main_offers}
        if len(main_offers) != 2 or set(offers) != {"h2h", "totals"}:
            continue
        h2h, totals = offers["h2h"], offers["totals"]
        if not all(
            aware(x["last_update"]) <= aware(meta["captured_at"]) <= cutoff
            and cutoff - aware(x["last_update"]) <= timedelta(hours=1)
            for x in offers.values()
        ):
            continue
        wdl = {o["name"]: o["price"] for o in h2h["outcomes"]}
        sides = {o["name"]: o for o in totals["outcomes"]}
        names = (fixture["provider_home"], "Draw", fixture["provider_away"])
        if (
            set(wdl) != set(names)
            or len(h2h["outcomes"]) != 3
            or set(sides) != {"Over", "Under"}
            or len(totals["outcomes"]) != 2
            or sides["Over"]["point"] != sides["Under"]["point"]
        ):
            continue
        market = {
            "bookmaker": book["key"],
            "provider": "the-odds-api",
            "provider_match_id": event["id"],
            "match_id": fixture["match_id"],
            "h2h": dict(zip(("home", "draw", "away"), (wdl[k] for k in names))),
            "total": {
                "line": sides["Over"]["point"],
                "over": sides["Over"]["price"],
                "under": sides["Under"]["price"],
            },
            "h2h_quoted_at": h2h["last_update"],
            "total_quoted_at": totals["last_update"],
            "captured_at": meta["captured_at"],
            "source_sha256": meta["source_sha256"],
            "source_url": meta["source_url"],
        }
        grid._quote_values(market, cutoff)
        main[book["key"]] = market
    return main


def forecast(fixture, meta, run, quotes, anchor, inputs, cutoff):
    f = dict(fixture, cutoff_at=cutoff.isoformat())
    if aware(f["sources"][0]["query_time"]) > cutoff:
        raise ValueError("verified venue not yet known")
    event = verified_raw(run, meta)
    books = mainlines(event, meta, f, cutoff)
    complete = []
    for book, market in sorted(books.items()):
        q = [
            row
            for row in quotes
            if row["bookmaker"] == book and (row["market"] == "btts" or row["line"] == 0.5)
        ]
        selected, _ = team.select_quotes(q, f, market, cutoff)
        if {team.market_key(r) for r in selected} == {("btts", None), ("home", 0.5), ("away", 0.5)}:
            complete.append((book, market, selected))
    if not complete:
        return None
    book, market, selected = complete[0]

    # For replay after kickoff, execute the historical engine at the stored issue
    # cutoff; the surrounding freeze enforces the actual non-backdatable issuance.
    class FrozenClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cutoff.astimezone(tz)

    with patch.object(legacy, "datetime", FrozenClock):
        current = legacy.predict(anchor, f)
    if digest(current["matrix"]) != current["matrix_hash"]:
        raise ValueError("active Current grid failed its native hash")
    line = market["total"]["line"]
    base = grid._market_matrix(np.asarray(current["matrix"]), grid._quote_values(market, cutoff))
    candidate, features = team.adjust(base, selected)
    ablations = {}
    for q in selected:
        family = team.market_key(q)[0]
        without, _ = team.adjust(base, [r for r in selected if team.market_key(r)[0] != family])
        decision = team.picks(without, line)
        ablations[family] = {
            "max_cell_probability_difference_from_full": float(np.max(abs(without - candidate))),
            "primary_without": decision["primary"],
            "top3_without": decision["top3"],
            "wdl_without": decision["wdl"],
            "total_without": decision["total"]["direction"],
        }
    return {
        "inputs": inputs,
        "base_method": "active English Current projected to same-book 1X2 and match total",
        "cutoff_at": f["cutoff_at"],
        "bookmaker": book,
        "eligible_books": [r[0] for r in complete],
        "match_total_market": market,
        "quotes": selected,
        "features": features,
        "leave_one_out": ablations,
        "native_current": {
            "version": current["version"],
            "matrix_sha256": current["matrix_hash"],
            "primary": current["primary"],
        },
        "arms": dict(zip(team.ARMS, (team.picks(base, line), team.picks(candidate, line)))),
    }


def records(batch, fixtures):
    first = {}
    for p in (batch / "records").glob("*/*.json"):
        r = verify(read(p))
        mid = r["match_id"]
        if (
            mid in first
            or mid not in fixtures
            or r["track"] != TRACK
            or r["evaluation_slot"] != "FIRST_PREMATCH"
            or r["stage"] != "prematch"
            or aware(r["fixture"]["kickoff_at"]) != aware(fixtures[mid]["kickoff_at"])
            or not aware(r["fixture"]["cutoff_at"])
            <= aware(r["recorded_at"])
            < aware(fixtures[mid]["kickoff_at"])
        ):
            raise ValueError("invalid England first issue")
        first[mid] = r
    return first


def run(data, batch, operation):
    fixtures, path, quotes, requests, anchor, inputs = materials(data, batch)
    if operation == "review":
        return team.review_registered(
            data, batch, inputs["selection_protocol_sha256"], fixtures, records(batch, fixtures)
        )
    if operation == "replay":
        first = records(batch, fixtures)
        checks = 0
        for mid, r in first.items():
            restored = forecast(
                fixtures[mid], requests[mid], path, quotes, anchor, inputs, aware(r["fixture"]["cutoff_at"])
            )
            saved = r["payload"]
            for k in ("inputs", "bookmaker", "eligible_books", "match_total_market", "quotes"):
                if restored[k] != saved[k]:
                    raise ValueError("replay mismatch for " + k)
            if (
                restored["native_current"]["primary"] != saved["native_current"]["primary"]
                or restored["native_current"]["version"] != saved["native_current"]["version"]
            ):
                raise ValueError("active Current identity differs")
            for expected, actual in zip(saved["features"], restored["features"], strict=True):
                for k in ("market", "family", "team", "line", "weight", "market_probability"):
                    if expected[k] != actual[k]:
                        raise ValueError("replayed feature identity differs")
                for k in ("original_probability", "adjusted_probability"):
                    np.testing.assert_allclose(expected[k], actual[k], rtol=0, atol=PROBABILITY_ATOL)
            if restored["leave_one_out"].keys() != saved["leave_one_out"].keys():
                raise ValueError("leave-one-market-out families differ")
            for family, values in restored["leave_one_out"].items():
                ref = saved["leave_one_out"][family]
                metric = "max_cell_probability_difference_from_full"
                np.testing.assert_allclose(ref[metric], values[metric], rtol=0, atol=ABLATION_ATOL)
                if any(ref[k] != v for k, v in values.items() if k != metric):
                    raise ValueError("leave-one-market-out selections differ")
            for name in team.ARMS:
                arm, other = restored["arms"][name], saved["arms"][name]
                np.testing.assert_allclose(arm["matrix"], other["matrix"], rtol=0, atol=PROBABILITY_ATOL)
                for side in ("over", "under"):
                    expected = other["total"]["settlements"][side]
                    actual = arm["total"]["settlements"][side]
                    if expected.keys() != actual.keys():
                        raise ValueError("total settlement categories differ")
                    np.testing.assert_allclose(
                        [actual[k] for k in expected], list(expected.values()), rtol=0, atol=PROBABILITY_ATOL
                    )
                if (
                    other["matrix_sha256"] != digest(other["matrix"])
                    or any(arm[k] != other[k] for k in ("primary", "top3", "wdl"))
                    or any(arm["total"][k] != other["total"][k] for k in ("direction", "line", "objective"))
                ):
                    raise ValueError("frozen selections differ")
                checks += 1
        return {
            "population": len(fixtures),
            "first_issues": len(first),
            "real_both_team_05_and_btts": len(first),
            "replayed_grids": checks,
        }
    with exclusive_lock(batch / ".pilot-issue.lock"):
        first = records(batch, fixtures)
        status = {}
        for mid, f in fixtures.items():
            if mid in first:
                status[mid] = "PREVIOUSLY_ISSUED"
                continue
            now = team.cutoff_now()
            if now >= aware(f["kickoff_at"]):
                status[mid] = "PAST_KICKOFF"
                continue
            pred = forecast(f, requests[mid], path, quotes, anchor, inputs, now)
            if pred is None:
                status[mid] = "NO_FRESH_SAME_BOOK_COMPLETE_PANEL"
                continue
            receipt = save(
                pred,
                dict(f, cutoff_at=now.isoformat()),
                stage="prematch",
                track=TRACK,
                root=batch / "records",
                idempotency_key=digest([mid, TRACK]),
            )
            status[mid] = receipt["sha256"]
        return {
            "population": len(fixtures),
            "first_issues": len(records(batch, fixtures)),
            "status": status,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("issue", "review", "replay"))
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    data = args.state_dir.resolve()
    result = run(data, data / BATCH, args.operation)
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}, indent=2))


if __name__ == "__main__":
    main()
