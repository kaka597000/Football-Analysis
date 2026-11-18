"""Source-timed team scoring market adjustment to an immutable same-fixture score grid."""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit, logit, logsumexp

from publication import deliver
from research.analysis_freeze import aware, digest, save, verify
from storage_lock import exclusive_lock
from . import three_arm_review as settlement
from .three_arm_model import _matrix, score_picks


VERSION = "WCPP-TEAM-SCORING-MARKETS-1"
PARENT = Path("workspace-updates/20261118-transformer/forward")
DEFAULT_BATCH = Path("workspace-updates/20261118-market-signals/team-candidate")
ARMS = ("frozen_market_baseline", "prematch_team_and_btts_adjustment")


def read(path):
    return json.loads(path.read_bytes())


def encode(value):
    return (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()


def cutoff_now():
    return datetime.now(timezone.utc)


def market_key(q):
    if q["market"] == "btts" and q["team"] is None and q["line"] is None:
        return ("btts", None)
    line = q["line"]
    if (
        q["market"] == "team_total"
        and q["team"] in ("home", "away")
        and type(line) in (int, float)
        and line >= 0.5
        and line % 1 == 0.5
    ):
        return (q["team"], line)
    return None


def select_quotes(quotes, fixture, baseline_market, cutoff):
    groups = defaultdict(lambda: defaultdict(list))
    skipped = Counter()
    for q in quotes:
        if q["match_id"] != fixture["match_id"]:
            continue
        if (
            q["provider_match_id"] != baseline_market["provider_match_id"]
            or aware(q["kickoff_at"]) != aware(fixture["kickoff_at"])
            or q["provider"] != baseline_market["provider"]
        ):
            raise ValueError("market event differs from original fixture")
        key = market_key(q)
        if key is None:
            skipped["unsupported_market_or_line"] += 1
            continue
        sides = ("yes", "no") if key[0] == "btts" else ("over", "under")
        odds = q["decimal_odds"]
        if set(odds) != set(sides) or not all(np.isfinite(odds[k]) and odds[k] > 1 for k in sides):
            skipped["incomplete_prices"] += 1
            continue
        stamp, request, capture = (aware(q[k]) for k in ("quoted_at", "requested_at", "captured_at"))
        if not (request <= capture <= cutoff and stamp <= capture and cutoff - stamp <= timedelta(hours=1)):
            skipped["unavailable_or_stale"] += 1
            continue
        groups[q["bookmaker"]][key].append(q)

    choices = []
    for book, features in groups.items():
        rows = []
        for _, variants in sorted(features.items()):
            latest = max(aware(q["quoted_at"]) for q in variants)
            tied = [q for q in variants if aware(q["quoted_at"]) == latest]
            if len({digest(q["decimal_odds"]) for q in tied}) != 1:
                skipped["conflicting_latest_prices"] += 1
                continue
            rows.append(max(tied, key=lambda q: (aware(q["captured_at"]), q["source_sha256"])))
        if rows:
            priority = (
                -len({market_key(q)[0] for q in rows}),
                -sum(q["line"] == 0.5 for q in rows),
                book != baseline_market["bookmaker"],
                -max(aware(q["quoted_at"]).timestamp() for q in rows),
                book,
            )
            choices.append((priority, rows))
    return min(choices, key=lambda x: x[0])[1] if choices else [], dict(skipped)


def adjust(matrix, quotes):
    """Solve the convex KL projection in the low-dimensional market dual."""
    m = _matrix(matrix)
    h, a = np.indices(m.shape)
    support = m > 0
    features, masks, targets = [], [], []
    for q in quotes:
        key = market_key(q)
        if key is None:
            raise ValueError("unhandled market in adjustment")
        family, line = key
        mask = (h > 0) & (a > 0) if family == "btts" else (h if family == "home" else a) > line
        binary = mask[support]
        if not binary.any() or binary.all():
            continue
        sides = ("yes", "no") if family == "btts" else ("over", "under")
        inv = np.reciprocal([float(q["decimal_odds"][side]) for side in sides])
        target = inv[0] / inv.sum()
        targets.append(target)
        masks.append(binary)
        features.append(
            {
                "market": q["market"],
                "team": q["team"],
                "line": line,
                "family": family,
                "market_probability": float(target),
                "original_probability": float(m[mask].sum()),
            }
        )
    if not masks:
        return m.copy(), []
    events = np.asarray(masks, dtype=float)
    targets = np.asarray(targets)
    counts = Counter(s["family"] for s in features)
    weights = np.array([1.0 / counts[s["family"]] for s in features])
    prior = np.log(m[support])

    def dual(theta):
        log_m = prior + theta @ events
        partition = logsumexp(log_m)
        local = logit(targets) - theta / weights
        value = partition + weights @ (np.log1p(-targets) + np.logaddexp(0.0, local))
        grad = events @ np.exp(log_m - partition) - expit(local)
        return value, grad

    fit = minimize(
        dual,
        np.zeros(len(events)),
        method="L-BFGS-B",
        jac=True,
        options={"gtol": 1e-11, "ftol": 1e-15, "maxiter": 250},
    )
    if np.max(np.abs(fit.jac)) > 1e-7:
        raise ValueError("market projection did not converge")
    posterior = prior + fit.x @ events
    result = np.zeros_like(m)
    result[support] = np.exp(posterior - logsumexp(posterior))
    for feature, val, weight in zip(features, events @ result[support], weights, strict=True):
        feature.update(adjusted_probability=float(val), weight=float(weight))
    return result, features


def picks(matrix, line):
    matrix = _matrix(matrix)
    result = score_picks(matrix, line=line)
    order = np.argsort(-matrix.ravel(), kind="stable")[:3]
    result["top3"] = ["%d-%d" % np.unravel_index(index, matrix.shape) for index in order]
    result["matrix"] = matrix.tolist()
    result["matrix_sha256"] = digest(result["matrix"])
    return result


def verified_quotes(data, run):
    from data_sources.team_market_quotes import load_targets

    targets = {r["match_id"]: r for r in load_targets(data, data / PARENT / "plan.json")}
    if read(run / "targets.json") != list(targets.values()):
        raise ValueError("source run does not cover original registered fixture identities")
    return verified_source_quotes(run, targets)


def verified_source_quotes(run, targets):
    from data_sources.team_market_quotes import normalize_event

    rows = read(run / "quotes.json")
    raw_quotes = set()
    for meta in read(run / "requests.json"):
        if not meta.get("source_file") or not meta.get("source_sha256"):
            continue
        path = (run / meta["source_file"]).resolve()
        if not path.is_relative_to(run.resolve()):
            raise ValueError("raw event outside archived run")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != meta["source_sha256"]:
            raise ValueError("raw quote event hash mismatch")
        if meta.get("status") not in (None, 200, "200", "OK"):
            continue
        evidence, _ = normalize_event(targets[meta["match_id"]], json.loads(raw), meta)
        raw_quotes.update(digest(q) for q in evidence)
    if len(rows) != len({digest(q) for q in rows}) or {digest(q) for q in rows} != raw_quotes:
        raise ValueError("retained quote cannot be reproduced from paired prices in original raw event")
    return rows


def bases(data, batch):
    plan = verify(read(data / PARENT / "plan.json"))
    protocol = read(batch / "protocol.json")
    registration = read(batch.parent / "registration.json")
    if (
        protocol["version"] != VERSION
        or protocol["parent_plan"] != plan["record_sha256"]
        or registration["forward_plan_parent"] != plan["record_sha256"]
        or registration["forward_arms"]["team_markets"] != list(ARMS)
    ):
        raise ValueError("registered parent or comparison changed")
    snapshots = [verify(read(f)) for f in (data / PARENT / "freezes").glob("*.json")]
    originals = {r["match_id"]: r for r in snapshots}
    if len(originals) != len(snapshots) or set(originals) != set(registration["forward_ids"]):
        raise ValueError("original population changed")
    for mid, record in originals.items():
        if (
            record["evaluation_slot"] != "FIRST_PREMATCH"
            or record["payload"]["plan_sha256"] != plan["record_sha256"]
            or record["fixture"]["match_id"] != mid
        ):
            raise ValueError("wrong original freeze")
    return originals, digest(protocol)


def issued(batch, originals):
    records = {}
    for path in sorted((batch / "records").glob("*/*.json")):
        r = verify(read(path))
        mid = r["match_id"]
        if (
            mid in records
            or mid not in originals
            or r["stage"] != "prematch"
            or r["evaluation_slot"] != "FIRST_PREMATCH"
            or r["payload"]["base_freeze_sha256"] != originals[mid]["record_sha256"]
            or not aware(r["fixture"]["cutoff_at"])
            <= aware(r["recorded_at"])
            < aware(r["fixture"]["kickoff_at"])
        ):
            raise ValueError("team candidate first issue or chronology conflict")
        records[mid] = r
    return records


def issue(data, batch, run):
    batch.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(batch / ".team-market.lock"):
        originals, protocol_sha = bases(data, batch)
        quotes = verified_quotes(data, run)
        inputs_sha = digest(quotes)
        output = batch / "runs" / (run.name + ".json")
        prior = issued(batch, originals)
        if output.exists():
            saved = read(output)
            if saved["input_sha256"] != inputs_sha or saved["protocol_sha256"] != protocol_sha:
                raise ValueError("same team-market run id with different inputs")
            return saved
        output.parent.mkdir(parents=True, exist_ok=True)
        now = cutoff_now()
        rows = []
        for mid, original in sorted(originals.items()):
            f, payload = original["fixture"], original["payload"]
            line = payload["arms"]["market_baseline"]["total"]["line"]
            row = {"match_id": mid, "base_freeze_sha256": original["record_sha256"]}
            rows.append(row)
            if mid in prior:
                row.update(status="PREVIOUSLY_ISSUED", first_sha256=prior[mid]["record_sha256"])
                continue
            if now >= aware(f["kickoff_at"]):
                row["status"] = "PAST_KICKOFF"
                continue
            market = payload["market"]
            selected, excluded = select_quotes(quotes, f, market, now)
            row["excluded"] = excluded
            m = payload["arms"]["market_baseline"]["matrix"]
            candidate, features = adjust(m, selected)
            if not features:
                row["status"] = "NO_ELIGIBLE_MARKET"
                continue
            base = picks(m, line)
            enhanced = picks(candidate, line)
            flags = {k: base[k] != enhanced[k] for k in ("primary", "wdl", "top3")}
            flags["total"] = base["total"]["direction"] != enhanced["total"]["direction"]
            out = {
                "version": VERSION,
                "promotion": False,
                "protocol_sha256": protocol_sha,
                "base_freeze_sha256": original["record_sha256"],
                "base_recorded_at": original["recorded_at"],
                "base_market": market,
                "quote_run": str(run.relative_to(data)),
                "quotes": selected,
                "feature_probabilities": features,
                "selections_changed": flags,
                "arms": dict(zip(ARMS, (base, enhanced))),
            }
            receipt = save(
                out,
                dict(f, cutoff_at=now.isoformat()),
                stage="prematch",
                track="team-scoring-market-study",
                root=batch / "records",
                idempotency_key=digest([mid, VERSION]),
            )
            row.update(
                status="ISSUED", first_sha256=receipt["sha256"], features=features, selections_changed=flags
            )
        report = {
            "version": VERSION,
            "as_of": now.isoformat(),
            "input_sha256": inputs_sha,
            "protocol_sha256": protocol_sha,
            "run": str(run.relative_to(data)),
            "population": len(rows),
            "status_counts": dict(Counter(r["status"] for r in rows)),
            "rows": rows,
        }
        deliver(output, encode(report))
        return report


def review(data, batch):
    originals, protocol_sha = bases(data, batch)
    first = issued(batch, originals)
    fixtures = {mid: rec["fixture"] for mid, rec in originals.items()}
    return review_registered(data, batch, protocol_sha, fixtures, first)


def review_registered(data, batch, protocol_sha, fixtures, first):
    results, issues = settlement._results(data, fixtures, cutoff_now())
    rows = []
    for mid, f in sorted(fixtures.items()):
        rec, actual = first.get(mid), results.get(mid)
        outcomes = {}
        if rec and actual:
            for arm, forecast in rec["payload"]["arms"].items():
                outcome = settlement._outcome(forecast, actual)
                outcome["score_top3"] = f"{actual['home']}-{actual['away']}" in forecast["top3"]
                outcomes[arm] = outcome
        rows.append(
            {
                "match_id": mid,
                **settlement._matchday(f),
                "result": actual,
                "source_issues": issues.get(mid, []),
                "first_sha256": rec["record_sha256"] if rec else None,
                "outcomes": outcomes,
            }
        )
    metrics = (*settlement.METRICS, "score_top3")
    report = {
        "version": VERSION,
        "protocol_sha256": protocol_sha,
        "population": len(rows),
        "first_issues": len(first),
        "settled_first_issues": sum(bool(r["outcomes"]) for r in rows),
        "rows": rows,
        "arms": {},
        "paired": {key: settlement._paired(rows, *ARMS, key) for key in metrics},
    }
    for arm in ARMS:
        observations = [r["outcomes"][arm] for r in rows if r["outcomes"]]
        report["arms"][arm] = {
            k: {
                "n": sum(row[k] is not None for row in observations),
                "hits": sum(row[k] is True for row in observations),
            }
            for k in metrics
        }
        for key in ("score_log_loss", "wdl_brier"):
            report["arms"][arm][key] = (
                np.mean([r[key] for r in observations]).item() if observations else None
            )
        report["arms"][arm]["settlements"] = dict(Counter(r["total_settlement"] for r in observations))
    with exclusive_lock(batch / ".team-review.lock"):
        from publication import atomic_json

        (batch / "reviews").mkdir(parents=True, exist_ok=True)
        deliver(batch / "reviews" / (digest(report) + ".json"), encode(report))
        atomic_json(batch / "review.json", report)
    return report


def replay(data, batch):
    originals, protocol_sha = bases(data, batch)
    first = issued(batch, originals)
    channels = {}
    coverage = Counter()
    changes = Counter(primary=0, wdl=0, total=0, top3=0)
    for mid, frozen in sorted(first.items()):
        orig, report = originals[mid], frozen["payload"]
        if report["protocol_sha256"] != protocol_sha:
            raise ValueError("original research recipe differs")
        run = data / report["quote_run"]
        if run not in channels:
            channels[run] = verified_quotes(data, run)
        base = orig["payload"]["arms"]["market_baseline"]
        selected, _ = select_quotes(
            channels[run], orig["fixture"], orig["payload"]["market"], aware(frozen["fixture"]["cutoff_at"])
        )
        matrix, features = adjust(base["matrix"], selected)
        if (
            selected != report["quotes"]
            or not features
            or len(features) != len(report["feature_probabilities"])
        ):
            raise ValueError("market observations disagree with first issue")
        for got, saved in zip(features, report["feature_probabilities"], strict=True):
            for k, val in got.items():
                if isinstance(val, float):
                    np.testing.assert_allclose(val, saved[k], rtol=1e-9, atol=1e-10)
                elif val != saved[k]:
                    raise ValueError("market marginal identity differs")
            coverage[(got["family"], got["line"])] += 1
        for name, prediction in zip(ARMS, (base["matrix"], matrix), strict=True):
            saved = report["arms"][name]
            restored = picks(prediction, base["total"]["line"])
            np.testing.assert_allclose(saved["matrix"], restored["matrix"], rtol=1e-9, atol=1e-12)
            if (
                saved["matrix_sha256"] != digest(saved["matrix"])
                or any(saved[k] != restored[k] for k in ("wdl", "primary", "top3"))
                or saved["total"]["direction"] != restored["total"]["direction"]
                or saved["total"]["line"] != base["total"]["line"]
            ):
                raise ValueError("frozen score grid or decisions failed to reproduce")
        a, b = (report["arms"][name] for name in ARMS)
        flags = {k: a[k] != b[k] for k in ("wdl", "primary", "top3")}
        flags["total"] = a["total"]["direction"] != b["total"]["direction"]
        if flags != report["selections_changed"]:
            raise ValueError("changed selections disagree with first issue")
        changes.update(flags)
    return {
        "population": len(originals),
        "replayed_fixtures": len(first),
        "replayed_grids": 2 * len(first),
        "market_coverage": [
            {"family": key[0], "line": key[1], "n": n} for key, n in sorted(coverage.items())
        ],
        "changed_selections": dict(changes),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    for operation in ("issue", "review", "replay"):
        cmd = sub.add_parser(operation)
        cmd.add_argument("--state-dir", type=Path, required=True)
        cmd.add_argument("--batch", type=Path, default=DEFAULT_BATCH)
        if operation == "issue":
            cmd.add_argument("--quotes-run", type=Path, required=True)
    args = parser.parse_args()
    data = args.state_dir.resolve()
    batch = data / args.batch
    if args.operation == "issue":
        report = issue(data, batch, data / args.quotes_run)
    elif args.operation == "review":
        report = review(data, batch)
    else:
        print(json.dumps(replay(data, batch), indent=2))
        return
    print(
        json.dumps(
            {
                k: v
                for k, v in report.items()
                if k in ("population", "first_issues", "status_counts", "settled_first_issues", "run")
            }
        )
    )


if __name__ == "__main__":
    main()
