"""Replay a fixed, already fitted WCPP-ML artifact with one pair of alternative gap inputs."""

import json
from functools import lru_cache
from pathlib import Path

import numpy as np
from archives import sha, utc
from calendars import EvidenceGap
from research_program.future_validation import quote_audit
from v6 import ml_model as ml


@lru_cache(maxsize=16)
def load_model(path, expected_hash):
    raw = Path(path).read_bytes()
    if sha(raw) != expected_hash:
        raise ValueError("Registered artifact bytes changed")
    a = json.loads(raw)
    if (
        a["version"] != ml.VERSION
        or a["config"] != ml.CONFIG
        or a["feature_names"] != ml.FEATURES
        or a["code_hash"] != ml.code_hash()
    ):
        raise ValueError("Original model ABI/source incompatible")
    return a, ml.lgb.Booster(model_str=a["booster_model"]), ml.legacy.digest(a)


def with_gaps(features, home, away):
    x = features.copy()
    own = ml.FEATURES.index("own_same_cohort_gap")
    opp = ml.FEATURES.index("opponent_same_cohort_gap")
    x[:, own] = [home, away]
    x[:, opp] = [away, home]
    return x


def pair_matrices(route, test, f, cut, calendars):
    if test.get("version") != ml.VERSION or not route.get("test_artifact_path"):
        raise EvidenceGap("NO_FIXED_V6_ARTIFACT")
    a, booster, digest = load_model(
        route["test_artifact_path"], route["test_artifact_sha256"]
    )
    if (
        digest != test["model_hash"]
        or test["code_hash"] != a["code_hash"]
        or a["cohort"] != f["cohort"]
        or test["input"] != f
    ):
        raise EvidenceGap("ORIGINAL_MODEL_OR_INPUT_MISMATCH")
    kick = utc(f["kickoff_at"])
    if (
        max(utc(a[k]) for k in ("built_at", "observed_at")) > utc(f["cutoff_at"])
        or utc(f["cutoff_at"]) > cut
        or a["train_end"] >= kick.date().isoformat()
        or not utc(f["cutoff_at"]) <= utc(test["generated_at"]) < kick
    ):
        raise EvidenceGap("MODEL_CLOCK_OR_FIT_OVERLAP")
    if ml.legacy.digest(test["matrix"]) != test["matrix_hash"]:
        raise ValueError("Frozen matrix checksum")
    rates = ml.legacy.rates(a["core"]["long"], f)[:2]
    original = np.asarray(test["ml"]["feature_values"], dtype=float)
    expected = ml.feature_matrix(a["history"], f, rates)
    if not np.allclose(expected, original, equal_nan=True, rtol=1e-11, atol=1e-12):
        raise ValueError("Original feature reconstruction failed")
    rho = a["core"]["long"]["nuisance"]["dixon_coles"]
    dc, _ = ml.legacy.distribution(a["core"], None, a["history"], f, "dc365")

    def mix(x):
        m, _ = ml.ml_matrix(booster, x, rates, rho)
        return (1 - a["alpha"]) * dc + a["alpha"] * m

    original_m = mix(original)
    error = float(np.max(np.abs(original_m - np.asarray(test["matrix"]))))
    if error > 1e-10:
        raise ValueError("Original matrix reconstruction failed")
    home, away = calendars
    control = mix(with_gaps(original, home["league_days"], away["league_days"]))
    changed = any(v["league_days"] != v["all_days"] for v in calendars)
    candidate = (
        mix(with_gaps(original, home["all_days"], away["all_days"]))
        if changed
        else control
    )
    return (
        control,
        candidate,
        {
            "artifact_sha256": route["test_artifact_sha256"],
            "model_hash": digest,
            "train_end": a["train_end"],
            "model_built_at": a["built_at"],
            "published_matrix_hash": test["matrix_hash"],
            "original_matrix_reproduction_max_abs": error,
            "published_gap_days": original[
                :, ml.FEATURES.index("own_same_cohort_gap")
            ].tolist(),
            "feature_days_changed": changed,
            "matrix_changed": bool(np.any(candidate != control)),
        },
    )


def market(packet, f):
    audit = quote_audit(f)
    if not audit["eligible"]:
        return None, ";".join(audit["reasons"])
    q = audit["quote"]
    h = q.get("source_file_sha256") or q.get("source_sha256")
    if h not in packet.blobs:
        return None, "MISSING_QUOTE_BYTES"
    ids = {
        v
        for v in (q.get("source_event_id"), (f.get("source_ids") or {}).get("the_odds"))
        if v
    }
    ids.update(
        s["provider_match_id"]
        for s in f.get("market_snapshots", [])
        if s.get("match_id") == f["match_id"]
        and s.get("source_sha256") == h
        and s.get("provider_match_id")
    )
    if len(ids) != 1:
        return None, "UNBOUND_MARKET_EVENT"
    event_id = next(iter(ids))
    raw = packet.json(h)
    events = [
        e
        for e in (raw if isinstance(raw, list) else [raw])
        if e.get("id") == event_id and utc(e["commence_time"]) == utc(f["kickoff_at"])
    ]
    if len(events) != 1:
        return None, "MARKET_EVENT_OR_KICKOFF_MISMATCH"
    for book in events[0]["bookmakers"]:
        if q["bookmaker"].casefold() not in {
            str(book.get(k, "")).casefold() for k in ("key", "title")
        }:
            continue
        for m in book["markets"]:
            if m["key"] != "totals" or utc(
                m.get("last_update") or book["last_update"]
            ) != utc(q["quoted_at"]):
                continue
            offered = {
                s["name"]: s["price"]
                for s in m["outcomes"]
                if s.get("point") == q["line"]
            }
            if offered == {"Over": q["over_odds"], "Under": q["under_odds"]}:
                return dict(
                    line=q["line"],
                    book=book["key"],
                    raw_sha256=h,
                    quoted_at=q["quoted_at"],
                    received_at=q["query_time"],
                ), None
    return None, "ORIGINAL_MARKET_PRICE_LINE_OR_CLOCK_MISMATCH"
