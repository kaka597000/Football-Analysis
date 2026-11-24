"""Fit one fixed, local-only NPxG replacement and settle an outcome-separated archive replay."""

import argparse
import csv
import io
import itertools
import json
import math
import statistics
from collections import defaultdict, deque
from datetime import date
from pathlib import Path

import numpy as np
from optimization.evaluate import settle_ou
from research_program.paired_kpi_inference import compare
from research_program.score_form import features, poisson_matrix, summarize
from research_program.three_arm_model import score_picks, top_three_scores
from sklearn.linear_model import PoissonRegressor

from audit import (
    DATA,
    DIV,
    FD,
    SLUG,
    additional_native,
    args_for,
    csvout,
    dump,
    eligible_snapshot,
    jread,
    original_seasons,
    read_git,
    sha,
    utc,
)


def perspective(g, i, field):
    idx = 1 - i
    return dict(
        id=g["id"],
        date=g["date"],
        gf=g["goals"][i],
        ga=g["goals"][idx],
        xgf=g[field][i],
        xga=g[field][idx],
    )


def form(h, a, day):
    today = date.fromisoformat(day)
    return features(summarize(h, today), summarize(a, today))


def training(seasons, code):
    xs = []
    ys = []
    valid = 0
    n = 0
    for year in range(2020, 2025):
        past = defaultdict(lambda: deque(maxlen=6))
        for day, group in itertools.groupby(
            seasons[code, year]["completed"], lambda g: g["date"]
        ):
            games = list(group)
            for g in games:
                if g["date"] >= "2025-07-01":
                    raise ValueError("Training crosses the fixed end")
                xs.append(form(list(past[g["home_id"]]), list(past[g["away_id"]]), day))
                ys.extend(g["goals"])
                n += 1
            for g in games:
                if any(v is None for v in g["npx"]):
                    raise ValueError("Training pair lacks recorded non-penalty inputs")
                valid += 1
                for i, tid in enumerate((g["home_id"], g["away_id"])):
                    past[tid].appendleft(perspective(g, i, "npx"))
    model = PoissonRegressor(alpha=0.01, max_iter=1000, tol=1e-9).fit(
        np.vstack(xs), np.asarray(ys)
    )
    return dict(
        intercept=float(model.intercept_),
        weights=model.coef_.tolist(),
        matches=n,
        native_pairs=valid,
    )


def prior_for(rows, tid, day):
    games = [g for g in rows if tid in (g["home_id"], g["away_id"]) and g["date"] < day]
    return list(reversed(games[-6:]))


def sidevec(games, tid, k):
    return [perspective(g, int(tid == g["away_id"]), k) for g in games]


def choose_rates(control, baseline_features, candidate, candidate_features):
    base = np.exp(
        control["intercept"] + baseline_features @ np.asarray(control["base_weights"])
    )
    if candidate is None and candidate_features is None:
        return base, base.copy()
    if candidate is None or candidate_features is None:
        raise ValueError(
            "An eligible candidate needs both the fitted model and its six-game features"
        )
    return base, np.exp(
        candidate["intercept"] + candidate_features @ np.asarray(candidate["weights"])
    )


def predict_targets(args):
    seasons = original_seasons(args)
    extra = additional_native(args)
    targets = jread(args.out / "windows.json")
    old = (args.prior / "replay/fit.json").read_bytes()
    hashes = jread(
        args.prior
        / "source/research/20261124-prior-deep-completions/evidence/run-hashes.json"
    )
    if sha(old) != hashes["reproduced_files_sha256"]["replay/fit.json"]:
        raise ValueError("Control coefficients differ from previous filing")
    control = json.loads(old)
    fitted = {code: training(seasons, code) for code in SLUG}
    estimates = []
    active_windows = {r["window"] for r in targets if r["eligible"]}
    for g in targets:
        # The November 25 fixtures are an input-coverage audit. Do not re-score the old window if the candidate has no complete pair anywhere in it.
        if g["window"] not in active_windows:
            continue
        code = g["competition"]
        versions = [seasons[code, 2026]] + [
            x for x in extra if x["competition"] == code
        ]
        snap = eligible_snapshot(versions, utc(g["cutoff"]), g["window"] == "november")
        if snap is None or snap["sha"] != g["native_sha"]:
            raise ValueError("Selected edition or cutoff conflict")
        tids = [g["home_id"], g["away_id"]]
        day = g["kickoff"][:10]
        past = [prior_for(snap["completed"], tid, day) for tid in tids]
        b, c = control[code], fitted[code]
        x = form(*[sidevec(side, tid, "xg") for side, tid in zip(past, tids)], day)
        xc = None
        if g["eligible"]:
            for side, expected in zip(past, g["sides"]):
                if [r["id"] for r in side] != [
                    r["id"] for r in expected["slots"]
                ] or any(any(v is None for v in r["npx"]) for r in side):
                    raise ValueError("Not the six independently dated prior games")
            xc = form(
                *[sidevec(side, tid, "npx") for side, tid in zip(past, tids)], day
            )
        base, alt = choose_rates(b, x, c if g["eligible"] else None, xc)
        estimates.append(
            dict(
                window=g["window"],
                id=g["id"],
                competition=code,
                home=g["home"],
                away=g["away"],
                home_id=tids[0],
                away_id=tids[1],
                kickoff=g["kickoff"],
                cutoff=g["cutoff"],
                raw_sha=g["native_sha"],
                eligible=g["eligible"],
                history_ids=[[r["id"] for r in side] for side in past],
                base=base.tolist(),
                candidate=alt.tolist(),
            )
        )
    dump(args.out / "research-fit.json", fitted)
    dump(args.out / "ungraded-estimates.json", estimates)
    print(
        "Pre-grade identity",
        sha((args.out / "ungraded-estimates.json").read_bytes()),
        "fixtures",
        len(estimates),
    )


def probability(mu, actual, line):
    m = poisson_matrix(np.array(mu), (25, 25))
    picks = score_picks(m, line=line)
    top3 = top_three_scores(m)
    out = dict(
        primary=top3[0],
        top3=top3,
        wdl=picks["wdl"],
        score_log_loss=-math.log(float(m[tuple(actual)])),
        total_side=None,
        total_line=line,
    )
    if line is not None:
        side = picks["total"]["direction"]
        out.update(total_side=side, settlement=settle_ou(sum(actual), line, side))
    return out


def summarize_graded(rows):
    if not rows:
        return None
    kpi = compare(
        rows, baseline="base", candidate="candidate", draws=1000, seed=20261124
    )
    kpi["mean_score_log_loss"] = {
        arm: statistics.mean(r["forecast"][arm]["score_log_loss"] for r in rows)
        for arm in ("base", "candidate")
    }
    kpi["activated"] = sum(r["signal_used"] for r in rows)
    return kpi


def totals_inputs(path):
    if not path:
        return {}
    with path.open(encoding="utf-8-sig") as f:
        quotes = list(csv.DictReader(f))
    byid = defaultdict(list)
    for q in quotes:
        byid[(q["competition"], q["understat_id"])].append(q)
    return byid


def select_line(qs, e):
    valid = []
    cut = utc(e["cutoff"])
    for q in qs:
        if (
            utc(q["kickoff_at"]) != utc(e["kickoff"])
            or not q["bookmaker"]
            or not q["captured_at"]
            or not q["raw_sha256"]
            or not q["receipt_sha256"]
        ):
            continue
        c = utc(q["captured_at"])
        qt = q["quote_at"]
        line = float(q["line"])
        if (
            c > cut
            or (qt and utc(qt) > c)
            or not all(float(q[k]) > 1 for k in ["over_price", "under_price"])
            or not (line * 4).is_integer()
            or line <= 0
        ):
            continue
        name = q["bookmaker"].casefold()
        rank = (
            int(not qt),
            {"pinnacle": 0, "bet365": 1}.get(name, 2),
            name,
            -c.timestamp(),
        )
        valid.append((rank, q))
    if not valid:
        return None
    key = min(k for k, q in valid)
    best = [q for k, q in valid if k == key]
    if (
        len(
            {
                (float(q["line"]), float(q["over_price"]), float(q["under_price"]))
                for q in best
            }
        )
        != 1
    ):
        return None
    return best[0]


def grade(args):
    raw = (args.out / "ungraded-estimates.json").read_bytes()
    estimates = json.loads(raw)
    identity = list(
        csv.DictReader((args.out / "identity.csv").open(encoding="utf-8-sig"))
    )
    mp = {(r["competition"], r["calendar_name"]): r["understat_id"] for r in identity}
    scores = {}
    receipts = jread(args.out / "calendars.json")
    for row in receipts:
        code = row["competition"]
        csvraw = read_git(args.data, DATA, FD + "2627_" + DIV[code] + ".csv")
        if sha(csvraw) != row["ft_sha"]:
            raise ValueError("Score receipt differs")
        for s in csv.DictReader(io.StringIO(csvraw.decode("utf-8-sig"))):
            from datetime import datetime

            dk = datetime.strptime(s["Date"], "%d/%m/%Y").date().isoformat()
            h, a = int(s["FTHG"]), int(s["FTAG"])
            ft = "H" if h > a else "A" if a > h else "D"
            if ft != s["FTR"] or min(h, a) < 0:
                raise ValueError("Final league score fails result agreement")
            k = (code, mp[code, s["HomeTeam"]], mp[code, s["AwayTeam"]], dk)
            if k in scores:
                raise ValueError("Ambiguous score label")
            scores[k] = [h, a]
    qs = totals_inputs(args.quotes)
    rows = []
    for e in estimates:
        actual = scores.get(
            (e["competition"], e["home_id"], e["away_id"], e["kickoff"][:10])
        )
        q = select_line(qs.get((e["competition"], e["id"]), ()), e)
        line = float(q["line"]) if q else None
        if actual is None:
            raise ValueError("Missing exactly bound regular-season final")
        rows.append(
            dict(
                id="understat:" + e["id"],
                cohort=e["competition"],
                window=e["window"],
                date=e["kickoff"][:10],
                cutoff=e["cutoff"],
                home=e["home"],
                away=e["away"],
                actual=actual,
                signal_used=e["eligible"],
                line=line,
                quote=q,
                forecast={
                    arm: probability(e[arm], actual, line)
                    for arm in ("base", "candidate")
                },
            )
        )
    report = dict(
        estimate_sha256=sha(raw),
        official_displayed_pair_fixtures=0,
        official_selected_three_batches=0,
        windows={
            w: summarize_graded([r for r in rows if r["window"] == w])
            for w in ("october", "november")
        },
        active_only=summarize_graded([r for r in rows if r["signal_used"]]),
        by_league={
            k: summarize_graded([r for r in rows if r["cohort"] == k]) for k in SLUG
        },
        total_tiers={
            tier: summarize_graded(
                [r for r in rows if r["quote"] and bool(r["quote"]["quote_at"]) == full]
            )
            for tier, full in [("two_clocks", True), ("capture_only", False)]
        },
    )
    dump(args.out / "diagnostic.json", report)
    dump(args.out / "settled-rows.json", rows)
    csvout(
        args.out / "paired-results.csv",
        [
            dict(
                competition=r["cohort"],
                id=r["id"],
                home=r["home"],
                away=r["away"],
                kickoff_day=r["date"],
                cutoff=r["cutoff"],
                actual="-".join(map(str, r["actual"])),
                feature_active=r["signal_used"],
                base_first=r["forecast"]["base"]["primary"],
                candidate_first=r["forecast"]["candidate"]["primary"],
                base_three=" / ".join(r["forecast"]["base"]["top3"]),
                candidate_three=" / ".join(r["forecast"]["candidate"]["top3"]),
                base_wdl=r["forecast"]["base"]["wdl"],
                candidate_wdl=r["forecast"]["candidate"]["wdl"],
                line=r["line"],
                base_ou=r["forecast"]["base"]["total_side"],
                candidate_ou=r["forecast"]["candidate"]["total_side"],
            )
            for r in rows
        ],
    )
    print("Scored offline", len(rows), "actual separately delivered pairs", 0)


if __name__ == "__main__":
    p = args_for(argparse.ArgumentParser(description=__doc__))
    p.add_argument("stage", choices=["predict", "grade"])
    p.add_argument("--quotes", type=Path)
    a = p.parse_args()
    (predict_targets if a.stage == "predict" else grade)(a)
