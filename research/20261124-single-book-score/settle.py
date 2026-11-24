"""Grade only the two registered, sealed ensembles. Inputs are local archives."""

import argparse
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

import numpy as np
from competition_scope import policy
from optimization.evaluate import settle_ou
from research.analysis_freeze import aware
from research_program.three_arm_model import score_picks, top_three_scores

from inputs import HERE, PROTOCOL, PROTOCOL_SHA, dump, identities, read_csvs, sha, wdl
from prepare_clocked import select_reader

ARMS = ("baseline", "candidate")
BINARY = ("top1", "top3", "wdl", "full_win")
LOSSES = ("score_log_loss", "wdl_log_loss")


def assess(matrix, score, line):
    selection = score_picks(matrix, line=line)
    three = top_three_scores(matrix)
    actual = "-".join(map(str, score))
    side = wdl(score)
    direction = selection["total"]["direction"]
    result = settle_ou(sum(score), line, direction)
    return dict(first=selection["primary"], three=three, direction=selection["wdl"], total_pick=direction,
                top1=selection["primary"] == actual, top3=actual in three,
                wdl=selection["wdl"] == side, full_win=(result == "FULL_WIN") if result != "PUSH" else None,
                settlement=result, score_log_loss=-math.log(float(matrix[tuple(score)])),
                wdl_log_loss=-math.log(selection["wdl_probabilities"][side]))


def day_bootstrap(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[row["day"]].append(row)
    days = sorted(groups)
    if len(days) < 2:
        return dict(days=len(days), intervals=None)
    draws = np.random.default_rng(20261124).integers(0, len(days), size=(2000, len(days)))
    intervals = {}
    for key in (*BINARY, *LOSSES):
        sizes, sums = [], []
        for day in days:
            delta = [float(r["candidate"][key]) - float(r["baseline"][key]) for r in groups[day]
                     if all(r[arm][key] is not None for arm in ARMS)]
            sizes.append(len(delta))
            sums.append(sum(delta))
        n = np.asarray(sizes)[draws].sum(axis=1)
        total = np.asarray(sums)[draws].sum(axis=1)
        intervals[key] = np.quantile(total[n > 0] / n[n > 0], [.025, .975]).tolist() if (n > 0).any() else None
    return dict(days=len(days), draws=2000, intervals=intervals)


def metrics(rows, intervals=False):
    out = {"n": len(rows), "binary": {}, "losses": {}}
    for key, pick in zip(BINARY, ("first", "three", "direction", "total_pick")):
        pairs = [(r["baseline"], r["candidate"]) for r in rows if all(r[arm][key] is not None for arm in ARMS)]
        out["binary"][key] = {
            "n": len(pairs), "baseline": sum(a[key] for a, _ in pairs),
            "candidate": sum(b[key] for _, b in pairs),
            "gained": sum(b[key] and not a[key] for a, b in pairs),
            "lost": sum(a[key] and not b[key] for a, b in pairs),
            "picks_changed": sum(a[pick] != b[pick] for a, b in pairs),
        }
    for key in LOSSES:
        out["losses"][key] = {arm: float(np.mean([r[arm][key] for r in rows])) if rows else None for arm in ARMS}
    out["settlement"] = {arm: dict(Counter(r[arm]["settlement"] for r in rows)) for arm in ARMS}
    if intervals:
        out["day_block_candidate_minus_baseline_95"] = day_bootstrap(rows)
    return out


def verify_lock(tier, directory):
    expected = json.loads((HERE / "forecast-lock.json").read_text())[tier]
    actual = json.loads((directory / "lock.json").read_text())
    if (actual != expected or sha(HERE / "protocol.json") != PROTOCOL_SHA or
        sha(directory / "roster.json") != expected["roster_sha256"] or
        sha(directory / "matrices.npy") != expected["matrices_sha256"]):
        raise ValueError("Evaluation inputs differ from sealed predictions")
    return json.loads((directory / "roster.json").read_text())


def labels_clocked(args, roster):
    select_reader(args.reader, include_labels=True)
    import grade as native
    raw = native.git_bytes(args.data, "normalized-results.jsonl")
    if hashlib.sha256(raw).hexdigest() != native.RESULTS_SHA:
        raise ValueError("Retained results changed")
    normalized = defaultdict(list)
    selected = {r["match_id"] for r in roster if "offset" in r}
    for record in raw.splitlines():
        value = json.loads(record)
        if value["match_id"] in selected:
            normalized[value["match_id"]].append(value)
    refs = native.archived_refs(args.early, args.weekend, args.later)
    found = {}
    with TemporaryDirectory() as tmp:
        for r in roster:
            if r["match_id"] in selected:
                goals, sources, reasons = native.label(r, normalized, refs, args.data, Path(tmp) / "result.json")
                found[r["match_id"]] = (goals, sources if goals is not None else reasons)
    return found


def run(args, tier, directory, roster, labels):
    scored, gaps = [], Counter()
    with (directory / "matrices.npy").open("rb") as stream:
        for r in roster:
            if "offset" not in r:
                gaps[r["gap"]] += 1
                continue
            score, sources = labels[r["match_id"]]
            if score is None:
                gaps["NO_CERTIFIED_RESULT"] += 1
                continue
            stream.seek(r["offset"])
            pair = np.load(stream, allow_pickle=False)
            day = (aware(r["kickoff_at"]).astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
                   if tier == "clocked" else r["date"])
            odds = r["h2h"]
            tied = sum(price == min(odds.values()) for price in odds.values()) > 1
            item = dict(match_id=r["match_id"], competition=r["competition"], day=day, book=r["book"],
                        label=score, evidence=sources, line=r["total"]["line"], tied_favourites=tied)
            item.update((name, assess(m, score, item["line"])) for name, m in zip(ARMS, pair, strict=True))
            scored.append(item)
    dump(args.out / (tier + "-graded.json"), scored)
    results = metrics(scored, intervals=True)
    results.update(registered=len(roster), paired=sum("offset" in r for r in roster), gaps=dict(gaps))
    results["by_competition"] = {}
    for config in policy()["competitions"]:
        code = config["code"]
        targets = [r for r in roster if r["competition"] == code]
        evaluated = [r for r in scored if r["competition"] == code]
        breakdown = metrics(evaluated) if evaluated else {"n": 0}
        breakdown.update(registered=len(targets), paired=sum("offset" in r for r in targets))
        results["by_competition"][code] = breakdown
    results["by_book"] = dict(Counter(r["book"] for r in scored))
    # Both transforms preserve order. Inspect floating-point argmax on equal-price sides
    # separately, retaining the pre-registered repository choices in all primary metrics.
    tied = [r for r in scored if r["tied_favourites"]]
    changed = [r for r in scored if r["baseline"]["direction"] != r["candidate"]["direction"]]
    results["wdl_tie_audit"] = dict(joint_shortest_price=len(tied), changed_at_tied_price=sum(
        r["tied_favourites"] for r in changed), changed_at_unique_price=sum(not r["tied_favourites"] for r in changed))
    return results


def coverage_report(target, tiers):
    columns = ["competition", "tier", "registered", "paired", "settled"]
    for key in BINARY:
        columns += [key + "_" + k for k in ("n", "baseline", "candidate", "gained", "lost")]
    with target.open("x", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        for co in policy()["competitions"]:
            for name, tier in tiers.items():
                v = tier["by_competition"][co["code"]]
                row = dict(competition=co["code"], tier=name, registered=v["registered"], paired=v["paired"], settled=v["n"])
                if v["n"]:
                    row.update({key + "_" + col: v["binary"][key][col] for key in BINARY
                                for col in ("n", "baseline", "candidate", "gained", "lost")})
                writer.writerow(row)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for key in ("archive", "reader", "data", "early", "weekend", "later", "history", "clocked", "out"):
        p.add_argument("--" + key, type=Path, required=True)
    args = p.parse_args()
    h = verify_lock("history", args.history)
    c = verify_lock("clocked", args.clocked)
    if sha(args.archive) != PROTOCOL["source_assets_sha256"]:
        raise ValueError("Historical evidence changed")
    args.out.mkdir(parents=True, exist_ok=False)
    raw = identities(read_csvs(args.archive, PROTOCOL["development_seasons"], True))
    historical = {r["match_id"]: (r["label"], [r["source_member"], r["source_line"]]) for r in raw}
    a = run(args, "history", args.history, h, historical)
    b = run(args, "clocked", args.clocked, c, labels_clocked(args, c))
    coverage_report(args.out / "coverage.csv", {"archived_no_quote_clock": a, "archived_clocked": b})
    signals = b["binary"]
    loss = b["losses"]["score_log_loss"]
    qualifies = (b["n"] >= 100 and signals["top1"]["candidate"] > signals["top1"]["baseline"]
                 and signals["top3"]["candidate"] >= signals["top3"]["baseline"]
                 and loss["candidate"] <= loss["baseline"])
    dump(args.out / "results.json", dict(historical_no_quote_clock=a, historical_clocked=b,
         new_forward_fixtures=0, actual_displayed_pairs=0, actual_shortlist_batches=0,
         decision="ELIGIBLE_FOR_UNSEEN_REVIEW_ONLY" if qualifies else "STOP_THIS_RECIPE"))


if __name__ == "__main__":
    main()
