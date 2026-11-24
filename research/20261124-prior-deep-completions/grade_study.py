"""Grade locked estimates, keeping research ranks, actual delivery, time tiers and protected queues separate."""

import argparse
import csv
import gzip
import io
import json
import math
import tarfile
import tempfile
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from build_inputs import HERE, git_file, sha
from market_receipts import validate as verify_selected_market_receipts
from model_study import write_rows
from optimization.evaluate import settle_ou
from research_program.paired_kpi_inference import compare
from research_program.replay import _finished_espn_label
from research_program.score_form import poisson_matrix
from research_program.three_arm_model import score_picks, top_three_scores

ARMS = ("base", "candidate")
LEAGUE_CODES = {
    "Premier League": "eng.1",
    "La Liga": "esp.1",
    "LaLiga": "esp.1",
    "Bundesliga": "ger.1",
    "Serie A": "ita.1",
    "Ligue 1": "fra.1",
}
SEED = 20261124


def utc(s):
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("A source clock requires a timezone")
    return dt.astimezone(timezone.utc)


def id_key(value):
    return "espn:" + str(value).removeprefix("espn:")


def load_csv(path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def protected(data, protocol):
    pkg = git_file(
        data,
        protocol["data_git"],
        "workspace-updates/20261123-prematch-literature/first-issues.tar.gz",
    )
    if sha(pkg) != protocol["r7_sha256"]:
        raise ValueError("R7 first-issue archive changed")
    with tarfile.open(fileobj=io.BytesIO(pkg)) as archive:
        rows = [json.load(archive.extractfile(m)) for m in archive if m.isfile()]
    r7 = {
        r["fixture"]["match_id"]
        for r in rows
        if r.get("track") == "research-prematch-continuation-20261123"
        and r.get("evaluation_slot") == "FIRST_PREMATCH"
    }
    prior = (HERE / "prior-61-ids.json").read_bytes()
    old = set(json.loads(prior))
    if len(r7) != 192 or len(old) != 61 or sha(prior) != protocol["prior61_sha256"]:
        raise ValueError("Protected fixture inventory changed")
    return old, r7


def label(data, commit, row, scratch):
    if (row["age"], row["gender"], row["stage"], row["score_basis"]) != (
        "senior",
        "male",
        "regular",
        "regulation_90",
    ):
        raise ValueError("Scope or score basis")
    ref = row["raw_reference"]
    raw = gzip.decompress(git_file(data, commit, ref["path"]))
    if sha(raw) != ref["sha256"] or utc(ref["captured_at"]) < utc(
        row["kickoff_at"]
    ) + timedelta(minutes=90):
        raise ValueError("Result hash or result capture time")
    rawfile = scratch / "summary.json"
    rawfile.write_bytes(raw)
    _finished_espn_label(row, {"url": ref["source_url"]}, rawfile)
    event = json.loads(raw)["header"]
    if event["league"]["slug"] != row["competition"]:
        raise ValueError("Cross-competition outcome")
    teams = event["competitions"][0]["competitors"]
    actual = []
    for role in ("home", "away"):
        team = next(t for t in teams if t["homeAway"] == role)
        halves = team.get("linescores") or []
        if len(halves) != 2:
            raise ValueError("Missing two normal-time halves")
        n = sum(int(t["displayValue"]) for t in halves)
        if (
            float(team.get("shootoutScore") or 0) > 0
            or n != int(team["score"])
            or n != row[role + "_goals"]
        ):
            raise ValueError("Regulation score mismatch")
        actual.append(n)
    return actual, ref["sha256"]


def market(quotes, result, cutoff):
    eligible = []
    for q in quotes:
        if (
            LEAGUE_CODES.get(q["competition"], q["competition"])
            != result["competition"]
            or id_key(q["match_id"]) != result["match_id"]
        ):
            continue
        if (
            not q.get("captured_at")
            or not q.get("kickoff_at")
            or utc(q["kickoff_at"]) != utc(result["kickoff_at"])
        ):
            continue
        capture = utc(q["captured_at"])
        line = float(q["line"])
        if (
            capture > cutoff
            or line <= 0
            or not (line * 4).is_integer()
            or not q["bookmaker"]
            or not q["source_ref"]
            or not q["source_sha256"]
        ):
            continue
        if not all(
            math.isfinite(float(q[x])) and float(q[x]) > 1
            for x in ("over_price", "under_price")
        ):
            continue
        qt = q["quote_at"]
        if qt and utc(qt) > capture:
            continue
        bk = q["bookmaker"].casefold()
        eligible.append(
            (
                (
                    0 if qt else 1,
                    {"pinnacle": 0, "bet365": 1}.get(bk, 2),
                    bk,
                    -capture.timestamp(),
                ),
                q,
            )
        )
    if not eligible:
        return None
    best = min(key for key, _ in eligible)
    selected = [q for key, q in eligible if key == best]
    if (
        len(
            {
                (float(q["line"]), float(q["over_price"]), float(q["under_price"]))
                for q in selected
            }
        )
        != 1
    ):
        return None
    return sorted(selected, key=lambda q: q["source_ref"])[0]


def decision(mu, line, actual):
    m = poisson_matrix(np.asarray(mu), (25, 25))
    pick = score_picks(m, line=line)
    pick3 = top_three_scores(m)
    side = pick["total"]["direction"] if pick["total"] else None
    out = dict(
        primary=pick3[0],
        top3=pick3,
        wdl=pick["wdl"],
        total_side=side,
        total_line=line,
        mean_goals=mu,
    )
    if actual is not None:
        out["score_log_loss"] = -math.log(float(m[tuple(actual)]))
        if side:
            out["settlement"] = settle_ou(sum(actual), line, side)
    return out


def grade_one(estimate, actual, q=None, kickoff=None):
    line = float(q["line"]) if q else None
    return dict(
        id="understat:" + estimate["id"],
        cohort=estimate["cohort"],
        date=(kickoff or estimate["date"])[:10],
        window=estimate["window"],
        home=estimate["home"],
        away=estimate["away"],
        signal_used=estimate["signal_used"],
        latest_dates=estimate["latest_dates"],
        history_ids=estimate["history_ids"],
        actual=actual,
        line=line,
        market_timing=("quote_and_capture" if q["quote_at"] else "capture_only")
        if q
        else None,
        market=q,
        forecast={k: decision(estimate[k], line, actual) for k in ARMS},
    )


def summarize(rows):
    if not rows:
        return None
    result = compare(
        rows, baseline="base", candidate="candidate", draws=1000, seed=SEED
    )
    known = [r for r in rows if r["actual"] is not None]
    loss = (
        {
            k: float(np.mean([r["forecast"][k]["score_log_loss"] for r in known]))
            for k in ARMS
        }
        if known
        else {}
    )
    grouped = defaultdict(list)
    for r in known:
        grouped[r["date"]].append(
            r["forecast"]["candidate"]["score_log_loss"]
            - r["forecast"]["base"]["score_log_loss"]
        )
    ci = None
    if len(grouped) > 1:
        blocks = [v for _, v in sorted(grouped.items())]
        picks = np.random.default_rng(SEED).integers(
            0, len(blocks), (1000, len(blocks))
        )
        diffs = np.array([sum(v) for v in blocks])[picks].sum(1) / np.array(
            [len(v) for v in blocks]
        )[picks].sum(1)
        ci = np.quantile(diffs, [0.025, 0.975]).tolist()
    ages = [
        (date.fromisoformat(r["date"]) - date.fromisoformat(day)).days
        for r in known
        for day in r.get("latest_dates", [])
        if day
    ]
    # Both research arms are independent Poisson; the sum has rate mu_home + mu_away.
    expected = {}
    for arm in ARMS:
        rates = [sum(r["forecast"][arm]["mean_goals"]) for r in known]
        expected[arm] = sum(
            1 - math.exp(-rate) * (1 + rate + rate**2 / 2 + rate**3 / 6)
            for rate in rates
        )
    result.update(
        score_log_loss=loss,
        score_log_loss_delta_ci=ci,
        signal_used=sum(r["signal_used"] for r in known),
        four_or_more_goals={
            "observed": sum(sum(r["actual"]) >= 4 for r in known),
            "expected": expected,
        },
        days_since_latest_club_appearance={
            "min": min(ages),
            "median": float(np.median(ages)),
            "max": max(ages),
        }
        if ages
        else None,
        score_decision_coverage={
            "actual_user_displayed_top1": None,
            "actual_user_displayed_top3": None,
            "paired_actual_first_display_matches": 0,
            "original_selected_best_three_batches": 0,
        },
    )
    return result


def grade(estimate_path, data, bridges_path, quotes_path, out):
    protocol = json.loads((HERE / "protocol.json").read_bytes())
    commit = protocol["data_git"]
    scope = (HERE.parents[1] / "configs/analysis-scope.json").read_bytes()
    if sha(scope) != protocol["scope_sha256"]:
        raise ValueError("Competition scope has changed")
    codes = [c["code"] for c in json.loads(scope)["competitions"]]
    old, r7 = protected(data, protocol)
    outcome = defaultdict(list)
    for ln in git_file(data, commit, "normalized-results.jsonl").splitlines():
        row = json.loads(ln)
        if row["competition"] in protocol["covered_source"]:
            outcome[row["match_id"]].append(row)
    bridges = defaultdict(list)
    for b in load_csv(bridges_path):
        if b.get("espn_event_id"):
            bridges[str(b["understat_match_id"])].append(b)
    quotes = defaultdict(list)
    for q in load_csv(quotes_path):
        quotes[id_key(q["match_id"])].append(q)
    allrows = [
        json.loads(s) for s in gzip.decompress(estimate_path.read_bytes()).splitlines()
    ]
    historical = []
    strict = []
    roster = []
    seen = set()
    with tempfile.TemporaryDirectory() as tmp:
        for e in allrows:
            if e["window"] == "historical":
                historical.append(grade_one(e, e["actual"]))
                continue
            update = {k: e[k] for k in ("cohort", "id", "home", "away", "scheduled")}
            update["espn_id"] = None
            state = "NO_UNIQUE_SOURCE_IDENTITY"
            mappings = bridges[e["id"]]
            if len(mappings) == 1:
                b = mappings[0]
                mid = id_key(b["espn_event_id"])
                update["espn_id"] = mid
                update["actual_kickoff"] = b["espn_datetime"]
                if mid in seen:
                    raise ValueError("Duplicate ESPN identity in scheduled roster")
                seen.add(mid)
                if mid in old or mid in r7:
                    state = "PRIOR_61_EXCLUDED" if mid in old else "R7_EXCLUDED"
                elif len(outcome[mid]) != 1:
                    state = "NO_UNIQUE_VERIFIED_RESULT"
                else:
                    r = outcome[mid][0]
                    kick = utc(r["kickoff_at"])
                    cut = kick - timedelta(hours=1)
                    nominal = datetime.fromisoformat(e["scheduled"]).replace(
                        tzinfo=timezone.utc
                    )
                    if r["competition"] != e["cohort"] or (
                        id_key(b["espn_home_id"]),
                        id_key(b["espn_away_id"]),
                    ) != (r["home"], r["away"]):
                        raise ValueError(
                            "Outcome differs from independently ordered teams"
                        )
                    if (
                        (
                            b["understat_home_id"],
                            b["understat_away_id"],
                            b["competition"],
                        )
                        != (e["home_id"], e["away_id"], e["cohort"])
                        or utc(b["espn_datetime"]) != kick
                        or b["stored_summary_sha256"] != r["raw_reference"]["sha256"]
                    ):
                        raise ValueError(
                            "Bridge differs from the original fixture or retained event"
                        )
                    if (
                        abs((kick - nominal).total_seconds()) > 72 * 3600
                        or cut <= utc(protocol["input_backup_time"])
                        or kick >= utc("2026-11-22T00:00:00Z")
                    ):
                        state = "ACTUAL_TIME_OUTSIDE_WINDOW"
                    else:
                        try:
                            actual, source_hash = label(data, commit, r, Path(tmp))
                        except ValueError as ex:
                            state = "UNVERIFIED_REGULATION: " + str(ex)
                        else:
                            q = market(quotes[mid], r, cut)
                            g = grade_one(e, actual, q, r["kickoff_at"])
                            g.update(
                                espn_id=mid,
                                result_ref=r["raw_reference"]["path"],
                                result_sha256=source_hash,
                                kickoff_at=r["kickoff_at"],
                                cutoff_at=cut.isoformat(),
                                identity_basis=b.get("identity_rule", ""),
                            )
                            strict.append(g)
                            state = "SCORED"
            update["status"] = state
            roster.append(update)
    info = {
        k: dict(Counter(r["status"] for r in roster if r["cohort"] == k)) for k in codes
    }
    report = {
        "historical": summarize(historical),
        "source_timed": summarize(strict),
        "registered_schedule": len(roster),
        "checked_market_receipts": verify_selected_market_receipts(
            data, commit, strict
        ),
        "roster_by_competition": info,
        "independent_signal_coverage": {
            k: (
                "VERIFIED_NATIVE_FIELDS"
                if k in protocol["covered_source"]
                else "NO_ARCHIVED_PRIOR_DEEP_SOURCE"
            )
            for k in codes
        },
        "protected": {
            "r7": len(r7),
            "prior": len(old),
            "r7_archive_sha256": protocol["r7_sha256"],
        },
        "market_tier": {
            tier: summarize([r for r in strict if r["market_timing"] == tier])
            for tier in ("quote_and_capture", "capture_only")
        },
        "by_competition": {
            k: {
                s: summarize([r for r in group if r["cohort"] == k])
                for s, group in [("historical", historical), ("source_timed", strict)]
            }
            for k in codes
        },
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "study.json").write_text(
        json.dumps(
            report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
        )
        + "\n"
    )
    (out / "roster.json").write_text(
        json.dumps(roster, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )
    write_rows(out / "paired.jsonl.gz", historical + strict)
    print(
        "Diagnostic",
        len(historical),
        "source-timed",
        len(strict),
        "roster",
        dict(Counter(r["status"] for r in roster)),
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    for key in ("estimates", "data", "bridges", "quotes", "out"):
        ap.add_argument("--" + key, type=Path, required=True)
    a = ap.parse_args()
    grade(a.estimates, a.data, a.bridges, a.quotes, a.out)
