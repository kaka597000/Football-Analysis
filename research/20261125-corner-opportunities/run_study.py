"""Reconstruct Current, seal both distributions, then grade the sealed forecast hashes."""

import argparse
from collections import Counter, defaultdict
import csv
from datetime import date, datetime
from hashlib import file_digest, sha256
import io
import json
import math
from pathlib import Path
import sys
import tarfile

import numpy as np

from corners import PriorGames, adjusted, decisions, fit, hits, score
from v2.model import digest, fit_baseline, rates

# Original v4 distribution uses sibling modules under its CLI's import convention.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "runtime" / "v4"))
from main_model import distribution

HERE = Path(__file__).resolve().parent
PROTOCOL = HERE / "protocol.json"
P = json.loads(PROTOCOL.read_bytes())


def encode(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode()


def file_hash(path):
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def save(path, raw):
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError(f"output changed: {path.name}")
    else:
        with path.open("xb") as f:
            f.write(raw)


def csv_season(archive, manifest, division, season):
    filename = f"{season}_{division}.csv"
    spec = manifest["closing_book_raw_csv"][filename]
    raw = archive.extractfile(spec["path"]).read()
    if spec["season"] != season or spec["league"] != division or sha256(raw).hexdigest() != spec["sha256"]:
        raise ValueError("wrong history file")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")
    rows = []
    seen = set()
    for r in csv.DictReader(io.StringIO(text)):
        if not r.get("Date") and not r.get("HomeTeam"):
            continue
        ds = r["Date"].strip()
        day = datetime.strptime(ds, "%d/%m/%Y" if len(ds.split("/")[-1]) == 4 else "%d/%m/%y").date()
        home, away = r["HomeTeam"].strip(), r["AwayTeam"].strip()
        if (
            r["Div"] != division
            or day.year not in (2000 + int(season[:2]), 2000 + int(season[2:]))
            or not home
            or not away
            or home == away
            or (home, away) in seen
        ):
            raise ValueError("wrong league, season or ambiguous fixture")
        seen.add((home, away))
        rows.append(dict(day=day, home=home, away=away, raw=r))
    return rows


def blocks(archive, manifest, seasons):
    for season in seasons:
        origin = date(2000 + int(season[2:]), 2, 1)
        for div, league in P["league_codes"].items():
            rows = csv_season(archive, manifest, div, season)
            yield origin, league, rows


def targets(rows, origin, league):
    for g in rows:
        day = g["day"]
        if (day.year, day.month) == (origin.year, origin.month):
            mid = digest([league, day.isoformat(), g["home"], g["away"]])
            yield mid, g


def early_fit(archive, manifest):
    observations = []
    counts = Counter()
    for origin, league, rows in blocks(archive, manifest, P["fit_seasons"]):
        state = PriorGames(rows, origin)
        baseline = fit_baseline(state.training)
        for mid, g in targets(rows, origin, league):
            counts["roster"] += 1
            h, a, missing = rates(baseline, g)
            x = state.signal(g["home"], g["away"])
            y = score(g["raw"])
            if missing or x is None or y is None:
                continue
            observations.extend(zip((h, a), x, y))
            counts["paired"] += 1
    return {"beta": fit(observations), "games": dict(counts), "side_observations": len(observations)}


def seal_row(folder, row, base, x, beta, line):
    challenger = adjusted(base, x, beta)
    buf = io.BytesIO()
    np.save(buf, np.stack((base, challenger)), allow_pickle=False)
    raw = buf.getvalue()
    sha = sha256(raw).hexdigest()
    save(folder / (sha + ".npy"), raw)
    return dict(
        row,
        signal=x,
        matrix_file=sha,
        baseline=decisions(base, line),
        candidate=decisions(challenger, line),
        line=line,
    )


def make_seal(asset, retained, folder):
    if file_hash(asset) != P["archive_sha256"]:
        raise ValueError("wrong history archive")
    folder.mkdir(parents=True, exist_ok=True)
    with tarfile.open(asset) as archive:
        manifest = json.load(archive.extractfile("ASSET-MANIFEST.json"))
        fitted = early_fit(archive, manifest)
        rows = []
        for origin, league, season in blocks(archive, manifest, P["test_seasons"]):
            state = PriorGames(season, origin)
            core = {"baseline": fit_baseline(state.training)}
            for mid, g in targets(season, origin, league):
                item = dict(
                    panel="historical",
                    id=mid,
                    league=league,
                    day=g["day"].isoformat(),
                    cutoff=origin.isoformat(),
                )
                baseline, meta = distribution(core, None, [], g, "baseline")
                if meta["unseen_teams"]:
                    rows.append(dict(item, status="unseen_team"))
                    continue
                x = state.signal(g["home"], g["away"])
                item["status"] = "active" if x is not None else "fallback_missing_signal"
                rows.append(seal_row(folder, item, baseline, x, fitted["beta"], 2.5))
    for item in json.loads(retained.read_bytes()):
        item = dict(item, panel="retained")
        source = item.pop("matrix", None)
        if source is None:
            rows.append(item)
            continue
        if digest(source) != item["matrix_sha256"]:
            raise ValueError("retained matrix hash mismatch")
        base = np.asarray(source, dtype=float)
        sealed = seal_row(folder, item, base, item["signal"], fitted["beta"], item["line"])
        ref = sealed["baseline"]
        if (
            ref["scores"] != item["original_three"]
            or ref["scores"][0] != item["original_first"]
            or ref["wdl"] != item["original_wdl"]
            or item["line"] is not None
            and ref["total"] != item["original_total"]
        ):
            raise ValueError("Current decisions differ from the original first pair")
        rows.append(sealed)
    if len(rows) != len({r["id"] for r in rows}):
        raise ValueError("duplicate target across panels")
    body = encode(sorted(rows, key=lambda r: r["id"]))
    save(folder / "sealed.json", body)
    lock = {
        "protocol_sha256": file_hash(PROTOCOL),
        "retained_sha256": file_hash(retained),
        "forecast_sha256": sha256(body).hexdigest(),
        "fit": fitted,
        "population": dict(Counter(r["panel"] for r in rows)),
        "statuses": {
            p: dict(Counter(r["status"] for r in rows if r["panel"] == p)) for p in ("historical", "retained")
        },
    }
    save(folder / "forecast-lock.json", encode(lock) + b"\n")
    return lock


def quoted(raw):
    try:
        prices = [float(raw[k]) for k in ("B365>2.5", "B365<2.5")]
    except (KeyError, TypeError, ValueError):
        return False
    return all(math.isfinite(p) and p > 1 for p in prices)


def historical_labels(asset):
    labels = {}
    with tarfile.open(asset) as archive:
        manifest = json.load(archive.extractfile("ASSET-MANIFEST.json"))
        for origin, league, season in blocks(archive, manifest, P["test_seasons"]):
            for mid, g in targets(season, origin, league):
                labels[mid] = (score(g["raw"]), 2.5 if quoted(g["raw"]) else None)
    return labels


def summary(population, paired, intervals=False):
    counts = Counter(r["status"] for r in population)
    out = dict(population=len(population), paired=len(paired), statuses=dict(counts))
    if not paired:
        return out
    for m in ("first", "three", "wdl", "full_win"):
        good = [r for r in paired if all(r[side][m] is not None for side in ("baseline", "candidate"))]
        b = sum(r["baseline"][m] for r in good)
        c = sum(r["candidate"][m] for r in good)
        gained = sum(not r["baseline"][m] and r["candidate"][m] for r in good)
        key = {"first": "scores", "three": "scores", "wdl": "wdl", "full_win": "total"}[m]

        def changed(r):
            first, second = (r["picks"][side][key] for side in ("baseline", "candidate"))
            if m == "first":
                return first[0] != second[0]
            return set(first) != set(second) if m == "three" else first != second

        result = {
            "n": len(good),
            "baseline": b,
            "candidate": c,
            "gained": gained,
            "lost": b + gained - c,
            "choices_changed": sum(changed(r) for r in good),
        }
        if intervals:
            grouped = defaultdict(lambda: [0, 0])
            for r in good:
                d = grouped[r["day"]]
                d[0] += int(r["candidate"][m]) - int(r["baseline"][m])
                d[1] += 1
            if len(grouped) >= 5:
                ar = np.asarray([grouped[d] for d in sorted(grouped)])
                draws = np.random.default_rng(20261125).integers(len(ar), size=(2000, len(ar)))
                chunks = ar[draws].sum(axis=1)
                result["date_cluster_95_pp"] = list(
                    np.quantile(100 * chunks[:, 0] / chunks[:, 1], [0.025, 0.975])
                )
        out[m] = result
    for arm in ("baseline", "candidate"):
        ranks = Counter(
            "first" if r[arm]["first"] else "second_or_third" if r[arm]["three"] else "outside_three"
            for r in paired
        )
        out[arm] = {
            "error_paths": dict(ranks),
            "mean_log_loss": float(np.mean([r[arm]["log_loss"] for r in paired])),
            "total_outcomes": dict(Counter(r[arm]["settlement"] or "missing" for r in paired)),
        }
    out["rank_transition"] = dict(
        Counter(
            ("first" if r["baseline"]["first"] else "backups" if r["baseline"]["three"] else "outside")
            + "->"
            + ("first" if r["candidate"]["first"] else "backups" if r["candidate"]["three"] else "outside")
            for r in paired
        )
    )
    return out


def grade(asset, folder, lock_path, retained, labels_file, out):
    lock = json.loads(lock_path.read_bytes())
    if (
        file_hash(PROTOCOL) != lock["protocol_sha256"]
        or file_hash(asset) != P["archive_sha256"]
        or file_hash(retained) != lock["retained_sha256"]
        or file_hash(folder / "sealed.json") != lock["forecast_sha256"]
    ):
        raise ValueError("protocol, data or prediction lock mismatch")
    rows = json.loads((folder / "sealed.json").read_bytes())
    receipt = json.loads(labels_file.read_bytes())
    if receipt["prepared_sha256"] != lock["retained_sha256"] or receipt["data_commit"] != P["data_pin"]:
        raise ValueError("outcomes refer to a different roster or data source")
    archive_labels = receipt["results"]
    eligible = {r["id"] for r in rows if r["panel"] == "retained" and "matrix_file" in r}
    if not set(archive_labels) <= eligible:
        raise ValueError("results include fixtures outside the retained comparison")
    history_labels = historical_labels(asset)
    paired = []
    missing = []
    for row in rows:
        if "matrix_file" not in row:
            continue
        mid = row["id"]
        if row["panel"] == "historical":
            label, line = history_labels[mid]
        else:
            label, line = archive_labels.get(mid, {}).get("score"), row["line"]
        if label is None:
            missing.append(mid)
            continue
        path = folder / (row["matrix_file"] + ".npy")
        if file_hash(path) != row["matrix_file"]:
            raise ValueError("sealed distribution differs")
        base, cand = np.load(path, allow_pickle=False)
        picks = {s: row[s] for s in ("baseline", "candidate")}
        paired.append(
            {
                "id": mid,
                "league": row["league"],
                "day": row["day"],
                "panel": row["panel"],
                "status": row["status"],
                "truth": label,
                "line": line,
                "picks": picks,
                "baseline": hits(picks["baseline"], label, line, base),
                "candidate": hits(picks["candidate"], label, line, cand),
            }
        )
    report = {
        "lock_sha256": file_hash(lock_path),
        "certified_labels_sha256": file_hash(labels_file),
        "fit": lock["fit"],
        "missing_scores": missing,
        "panels": {},
        "paired_actual_display": {"first": 0, "three": 0, "preselected_three_batches": 0},
    }
    for panel in ("historical", "retained"):
        pop = [r for r in rows if r["panel"] == panel]
        sample = [r for r in paired if r["panel"] == panel]
        report["panels"][panel] = dict(
            all=summary(pop, sample, True),
            active=summary(
                [r for r in pop if r["status"] == "active"], [r for r in sample if r["status"] == "active"]
            ),
            by_league={
                lg: summary([r for r in pop if r["league"] == lg], [r for r in sample if r["league"] == lg])
                for lg in sorted({r["league"] for r in pop})
            },
            by_year={
                y: summary([r for r in pop if r["day"][:4] == y], [r for r in sample if r["day"][:4] == y])
                for y in sorted({r["day"][:4] for r in pop})
            },
            days=sorted({r["day"] for r in sample}),
        )
    a = report["panels"]["historical"]["all"]
    b = report["panels"]["retained"]["all"]
    passes = (
        a["first"].get("date_cluster_95_pp", [0])[0] > 0
        and all(a[m]["candidate"] >= a[m]["baseline"] for m in ("three", "wdl", "full_win"))
        and a["candidate"]["mean_log_loss"] <= a["baseline"]["mean_log_loss"]
        and b["paired"] > 0
        and b["first"]["candidate"] > b["first"]["baseline"]
        and b["three"]["candidate"] >= b["three"]["baseline"]
    )
    report["decision"] = "consider_distinct_prospective_review" if passes else "stop_recipe"
    out.mkdir(parents=True, exist_ok=True)
    save(
        out / "results.json",
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2).encode() + b"\n",
    )
    save(out / "paired-private.jsonl", b"".join(encode(r) + b"\n" for r in paired))
    return {
        p: {
            m: v
            for m, v in d["all"].items()
            if m in ("population", "paired", "first", "three", "wdl", "full_win", "baseline", "candidate")
        }
        for p, d in report["panels"].items()
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode", choices=("prepare", "grade"))
    p.add_argument("--asset", type=Path, required=True)
    p.add_argument("--retained", type=Path, required=True)
    p.add_argument("--forecasts", type=Path, required=True)
    p.add_argument("--lock", type=Path)
    p.add_argument("--labels", type=Path)
    p.add_argument("--out", type=Path)
    args = p.parse_args()
    if args.mode == "prepare":
        result = make_seal(args.asset, args.retained, args.forecasts)
    else:
        result = grade(args.asset, args.forecasts, args.lock, args.retained, args.labels, args.out)
    print(json.dumps(result, sort_keys=True))
