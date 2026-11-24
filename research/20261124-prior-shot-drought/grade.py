"""Paired retrospective metrics and unconditional error-path denominators."""
import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path

import numpy as np

from history import HERE, read_rows
from optimization.evaluate import settle_ou
from study import file_hash, forecasts, matrix, read_lock, save


METRICS = ("first", "matrix_three", "wdl", "total_full_win")
SETTLEMENTS = ("FULL_WIN", "HALF_WIN", "PUSH", "HALF_LOSS", "FULL_LOSS")


def error_path(first, actual):
    picked = [int(n) for n in first.split("-")]
    if picked == list(actual):
        return "exact"
    if any((n == 0) != (y == 0) for n, y in zip(picked, actual)):
        return "wrong_blank_status"
    return "positive_counts_only"


def outcomes(entry, label, panel):
    actual = label["score"]
    if (not isinstance(actual, (tuple, list)) or len(actual) != 2
            or any(type(n) is not int or not 0 <= n < 31 for n in actual)):
        raise ValueError("two regulation goals within matrix support required")
    h, a = actual
    line = label.get("line") if panel in ("historical", "training") else entry["line"]
    if line is not None and line != entry["line"]:
        raise ValueError("graded line differs from sealed line")
    exact, wdl = f"{h}-{a}", "home" if h > a else "away" if a > h else "draw"
    row = dict(date=entry["date"], league=entry["league"], activated=entry["activated"])
    for name in ("base", "candidate"):
        f = entry[name]
        grid = matrix(entry["rates"], f["p_zero"])
        observed = sha256(np.ascontiguousarray(grid.round(12), dtype="<f8")).hexdigest()
        if observed != f["matrix_sha256"]:
            raise ValueError("reconstructed matrix does not match seal")
        total = settle_ou(h + a, line, f["total"]) if line is not None else None
        pzero = f["p_zero"]
        row[name] = dict(hits=[exact == f["top"][0], exact in f["top"], wdl == f["wdl"],
                               total == "FULL_WIN" if total not in (None, "PUSH") else None],
                         picks=[f["top"][0], f["top"], f["wdl"], f["total"] if line is not None else None],
                         settlement=total, error=error_path(f["top"][0], actual),
                         score_loss=float(-np.log(grid[h, a])),
                         zero_loss=float(-np.mean([np.log(p if y == 0 else 1-p) for p,y in zip(pzero,actual)])))
        pred = [int(x) for x in f["top"][0].split("-")]
        item = row[name]
        item["picked_blank_but_scored"] = sum(x == 0 and y > 0 for x, y in zip(pred, actual))
        item["picked_goal_but_blank"] = sum(x > 0 and y == 0 for x, y in zip(pred, actual))
        item["direction_correct_score_miss"] = wdl == f["wdl"] and exact != f["top"][0]
    return row


def summary(rows):
    stats = {}
    for i, name in enumerate(METRICS):
        pairs = [r for r in rows if all(r[a]["hits"][i] is not None for a in ("base", "candidate"))]
        pairs_ab = [(r["base"]["hits"][i], r["candidate"]["hits"][i]) for r in pairs]
        stats[name] = dict(n=len(pairs), base=sum(a for a,b in pairs_ab), candidate=sum(b for a,b in pairs_ab),
                           gained=sum(not a and b for a,b in pairs_ab), lost=sum(a and not b for a,b in pairs_ab),
                           changed=sum(r["base"]["picks"][i] != r["candidate"]["picks"][i] for r in pairs))
    days = defaultdict(lambda: np.zeros(2, dtype=int))
    for row in rows:
        a, b = int(row["base"]["hits"][0]), int(row["candidate"]["hits"][0])
        days[row["date"]] += np.array([1, b - a])
    first_interval = None
    if len(days) >= 2:
        series = np.array([days[t] for t in sorted(days)])
        idx = np.random.default_rng(20261124).integers(len(days), size=(2000, len(days)))
        n, net = series[idx].sum(axis=1).T
        first_interval = np.quantile(100*net/n, [.025,.975]).tolist()
    quality = {}
    for name in ("base","candidate"):
        arm = [r[name] for r in rows]
        errors = Counter(r["error"] for r in arm)
        settlements = Counter(r["settlement"] for r in arm)
        quality[name] = {
            "score_log_loss": float(np.mean([r["score_loss"] for r in arm])) if arm else None,
            "team_zero_log_loss": float(np.mean([r["zero_loss"] for r in arm])) if arm else None,
            "error_path": {k:errors[k] for k in ("exact","wrong_blank_status","positive_counts_only")},
            "picked_blank_but_scored_sides": sum(r["picked_blank_but_scored"] for r in arm),
            "picked_goal_but_blank_sides": sum(r["picked_goal_but_blank"] for r in arm),
            "wdl_correct_but_score_misclassified": sum(r["direction_correct_score_miss"] for r in arm),
            "settlements": {k:settlements[k] for k in SETTLEMENTS},
            "unpriced":settlements[None]
        }
    return dict(scored=len(rows), dates=len(days), metrics=stats, quality=quality,
                first_delta_pp_ci95=first_interval)


def grade(entries, labels, panel):
    lookup = {r["id"]:r for r in labels}
    ids = {r["id"] for r in entries}
    if len(lookup) != len(labels) or len(ids) != len(entries) or set(lookup) - ids:
        raise ValueError("duplicate or unexpected fixture during scoring")
    if panel == "historical" and set(lookup) != ids:
        raise ValueError("historical label roster incomplete")
    graded = [outcomes(r, lookup[r["id"]], panel) for r in entries
              if r["base"] is not None and r["id"] in lookup and lookup[r["id"]]["score"] is not None]
    def covered(group, records):
        s = summary(group)
        return dict(roster=len(records), forecast=sum(r["base"] is not None for r in records),
                    activated=sum(r["activated"] for r in records), **s)
    return {
        "panel":panel, "overall":covered(graded,entries),
        "active":summary([r for r in graded if r["activated"]]),
        "by_competition":{league:covered([r for r in graded if r["league"]==league],
                                        [r for r in entries if r["league"]==league])
                          for league in sorted({r["league"] for r in entries})},
        "delivered_paired":{"first":0, "ordered_three":0, "preselected_three_batches":0},
    }


if __name__ == "__main__":
    p=argparse.ArgumentParser()
    sub=p.add_subparsers(dest="mode",required=True)
    early=sub.add_parser("training-diagnosis")
    early.add_argument("--train",type=Path,required=True)
    early.add_argument("--fit",type=Path,required=True)
    late=sub.add_parser("score")
    late.add_argument("--panel",choices=["historical","clocked"],required=True)
    late.add_argument("--forecasts",type=Path,required=True)
    late.add_argument("--labels",type=Path,required=True)
    late.add_argument("--lock",type=Path,required=True)
    for parser in (early,late):
        parser.add_argument("--out",type=Path,required=True)
    args=p.parse_args()
    if args.mode == "training-diagnosis":
        raw=read_rows(args.train)
        fitted=json.loads(args.fit.read_bytes())
        if fitted["training_inputs_sha256"] != file_hash(args.train):
            raise ValueError("training bytes differ from fitted artifact")
        features=[{k:v for k,v in r.items() if k not in ("score","observed_line")} for r in raw]
        forecasts_early=forecasts(features,fitted)
        train_labels=[dict(id=r["id"],score=r["score"],line=r["observed_line"]) for r in raw]
        result=grade(forecasts_early,train_labels,"training")
    else:
        sealed=read_lock(args.lock,args.forecasts,args.panel)
        result=grade(sealed,read_rows(args.labels),args.panel)
        result["forecast_sha256"]=file_hash(args.forecasts)
        result["labels_sha256"]=file_hash(args.labels)
    save(args.out,result)
    print(json.dumps(result["overall"],ensure_ascii=False))
