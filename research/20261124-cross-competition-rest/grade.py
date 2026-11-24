"""Settle the previously sealed matrices using archived full-time evidence."""

import argparse
import gzip
import json
import math
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path
from subprocess import check_output
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlsplit

import numpy as np
from archives import output, sha, utc
from optimization.evaluate import settle_ou
from prepare import PROTOCOL_SHA
from research_program.replay import _finished_espn_label
from research_program.three_arm_model import score_picks, top_three_scores

DATA = "a6d45024cf6cf622f05e4b378ab53cfe2d6ae329"
RESULTS_SHA = "1a5e101ce7bb39934552034e92d8a2a864ccad291d886037cfa3cdb6aaa487bf"


def git_bytes(root, name):
    return check_output(["git", "-C", str(root), "show", DATA + ":" + name])


def final(raw, url, captured, row, expected, scratch):
    doc = json.loads(raw)
    u = urlsplit(url)
    co = row["competition"]
    mid = row["match_id"]
    if (
        u.scheme != "https"
        or u.hostname != "site.api.espn.com"
        or utc(captured) < utc(row["kickoff_at"]) + timedelta(minutes=90)
    ):
        raise ValueError("outcome origin/time")
    if u.path.endswith("/" + co + "/summary") and parse_qs(u.query).get("event") == [
        mid[5:]
    ]:
        head = doc["header"]
        if head.get("league", {}).get("slug") != co:
            raise ValueError("outcome competition")
    elif u.path.endswith("/" + co + "/scoreboard") and co in {
        l.get("slug") for l in doc["leagues"]
    }:
        matches = [e for e in doc["events"] if str(e["id"]) == mid[5:]]
        if len(matches) != 1:
            raise ValueError("outcome identity")
        head = matches[0]
    else:
        raise ValueError("outcome route")
    cs = head["competitions"]
    if len(cs) != 1:
        raise ValueError("ambiguous outcome")
    goals = {}
    for t in cs[0]["competitors"]:
        score = t["score"]
        v = score.get("displayValue") if isinstance(score, dict) else score
        if not str(v).isdecimal():
            raise ValueError("outcome goals")
        goals[t["homeAway"]] = int(v)
        halves = t.get("linescores")
        if halves:
            if len(halves) != 2 or sum(
                int(p.get("displayValue", p.get("value"))) for p in halves
            ) != int(v):
                raise ValueError("half/total score conflict")
    score = (goals["home"], goals["away"])
    if expected is not None and score != expected:
        raise ValueError("normalized/raw conflict")
    authenticated = dict(row, home_goals=score[0], away_goals=score[1])
    scratch.write_bytes(raw)
    _finished_espn_label(authenticated, {"url": url}, scratch)
    return score


def archived_refs(early, weekend, later):
    refs = defaultdict(list)

    def add(raw_path, url, at, h):
        if urlsplit(url).hostname != "site.api.espn.com":
            return
        raw = raw_path.read_bytes()
        if sha(raw) != h:
            raise ValueError("Supplemental result checksum")
        doc = json.loads(raw)
        ids = (
            [doc["header"]["id"]]
            if "header" in doc
            else [v["id"] for v in doc.get("events", [])]
        )
        for id_ in ids:
            refs["espn:" + str(id_)].append((raw, url, at, h))

    f = early / "source_responses.json"
    if (
        sha(f.read_bytes())
        != "ecd185f21a86e63ff47f63a1948a3f8c816c45256cb0ea498aaeac7fa0dccb15"
    ):
        raise ValueError("First review receipt")
    for r in json.loads(f.read_text()):
        v = r["espn"]
        if v.get("http_status") == 200:
            add(
                early / "raw" / Path(v["raw_path"]).name,
                v["requested_url"],
                v["query_time_utc"],
                v["sha256"],
            )
    for f in sorted(weekend.glob("*.json.meta.json")):
        v = json.loads(f.read_text())
        if v["http_status"] == 200:
            add(
                f.with_suffix("").with_suffix(""),
                v["url"],
                v["query_time"],
                v["sha256"],
            )
    f = later / "espn-query-results.json"
    if (
        sha(f.read_bytes())
        != "2462a2802af15a28f5c45f551e0ae1bc47e5376b47f223fbce092aa52b49651c"
    ):
        raise ValueError("Later review receipt")
    for v in json.loads(f.read_text())["matches"]:
        if v.get("http_status") == 200:
            add(
                later / "raw-espn" / Path(v["raw_path"]).name,
                v["source_url"],
                v["query_time_utc"],
                v["sha256"],
            )
    return refs


def label(row, normalized, refs, data, scratch):
    found = defaultdict(list)
    reasons = Counter()

    def attempt(raw, url, at, h, expected=None):
        try:
            score = final(raw, url, at, row, expected, scratch)
        except (ValueError, KeyError, TypeError) as e:
            reasons[str(e)] += 1
            return
        found[score].append(h)

    for v in normalized.get(row["match_id"], []):
        if (
            v["gender"] != "male"
            or v["age"] != "senior"
            or v["score_basis"] != "regulation_90"
            or any(
                v[k] != row[k]
                for k in ("cohort", "competition", "stage", "home", "away")
            )
            or utc(v["kickoff_at"]) != utc(row["kickoff_at"])
        ):
            reasons["normalized target mismatch"] += 1
            continue
        ref = v["raw_reference"]
        h = ref["sha256"]
        if ref["path"] != "raw/" + h + ".json.gz":
            raise ValueError("Result content path mismatch")
        raw = gzip.decompress(git_bytes(data, ref["path"]))
        if sha(raw) != h:
            raise ValueError("Normalized raw result checksum")
        attempt(
            raw,
            ref["source_url"],
            ref["captured_at"],
            h,
            (v["home_goals"], v["away_goals"]),
        )
    for raw, url, at, h in refs.get(row["match_id"], []):
        attempt(raw, url, at, h)
    if len(found) != 1 or "normalized/raw conflict" in reasons:
        return None, [], dict(reasons, distinct_certified_scores=len(found))
    score, hashes = next(iter(found.items()))
    return score, sorted(set(hashes)), dict(reasons)


def hits(matrix, goals, line):
    score = "-".join(map(str, goals))
    wdl = "home" if goals[0] > goals[1] else "away" if goals[1] > goals[0] else "draw"
    picks = score_picks(matrix, line=line)
    top3 = top_three_scores(matrix)
    total = picks["total"]
    outcome = settle_ou(sum(goals), line, total["direction"]) if total else None
    return dict(
        primary=picks["primary"],
        three=top3,
        wdl_pick=picks["wdl"],
        total_pick=total["direction"] if total else None,
        top1=picks["primary"] == score,
        top3=score in top3,
        wdl=wdl == picks["wdl"],
        ou=outcome == "FULL_WIN" if outcome not in (None, "PUSH") else None,
        settled=outcome,
        logloss=-math.log(max(1e-15, float(matrix[goals]))),
    )


def metrics(rows):
    result = {"n": len(rows)}
    for name in ("top1", "top3", "wdl", "ou"):
        pairs = [
            (r["control"][name], r["candidate"][name])
            for r in rows
            if r["control"][name] is not None and r["candidate"][name] is not None
        ]
        result[name] = dict(
            n=len(pairs),
            control=sum(a for a, b in pairs),
            candidate=sum(b for a, b in pairs),
            gained=sum(b and not a for a, b in pairs),
            lost=sum(a and not b for a, b in pairs),
        )
    for arm in ("control", "candidate"):
        result[arm + "_score_logloss"] = (
            sum(r[arm]["logloss"] for r in rows) / len(rows) if rows else None
        )
        result[arm + "_ou_settlement"] = dict(
            Counter(r[arm]["settled"] or "NO_LINE" for r in rows)
        )
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for k in ("lock", "data", "early", "weekend", "later", "out"):
        p.add_argument("--" + k, type=Path, required=True)
    args = p.parse_args()
    receipt = json.loads((args.lock / "lock.json").read_text())
    rb = (args.lock / "roster.json").read_bytes()
    original = json.loads((Path(__file__).parent / "input-lock.json").read_text())
    if (
        receipt != original
        or receipt["protocol_sha256"] != PROTOCOL_SHA
        or sha(rb) != receipt["roster_sha256"]
    ):
        raise ValueError("Unsealed roster/recipe")
    roster = json.loads(rb)
    preds = {}
    for r in roster:
        if r["status"] == "LOCKED_PAIR":
            path = args.lock / r["matrix_file"]
            if sha(path.read_bytes()) != r["matrix_file_sha256"]:
                raise ValueError("Matrix checksum")
            preds[r["match_id"]] = np.load(path, allow_pickle=False)
    raw = git_bytes(args.data, "normalized-results.jsonl")
    if sha(raw) != RESULTS_SHA:
        raise ValueError("Labels edition")
    normalized = defaultdict(list)
    for line in raw.splitlines():
        v = json.loads(line)
        normalized[v["match_id"]].append(v)
    refs = archived_refs(args.early, args.weekend, args.later)
    rows = []
    pending = {}
    with TemporaryDirectory() as tmp:
        for r in roster:
            mid = r["match_id"]
            if mid not in preds:
                continue
            score, sources, reasons = label(
                r, normalized, refs, args.data, Path(tmp) / "label.json"
            )
            if score is None:
                pending[mid] = reasons
                continue
            b, c = preds[mid]
            line = r["market"]["line"] if r["market"] else None
            rows.append(
                dict(
                    match_id=mid,
                    competition=r["competition"],
                    period=r["period"],
                    kickoff_at=r["kickoff_at"],
                    changed=r["model"]["feature_days_changed"],
                    matrix_changed=r["model"]["matrix_changed"],
                    label=score,
                    label_hashes=sources,
                    control=hits(b, score, line),
                    candidate=hits(c, score, line),
                )
            )
    groups = {"all": rows, "corrected": [r for r in rows if r["changed"]]}
    for period in ("early", "late"):
        subset = [r for r in rows if r["period"] == period]
        groups[period] = subset
        groups[period + "_corrected"] = [r for r in subset if r["changed"]]
    comp = {
        co: metrics([r for r in rows if r["competition"] == co])
        for co in sorted(
            {r["competition"] for r in roster if r["status"] != "OUTSIDE_STUDY_SCOPE"}
        )
    }
    args.out.mkdir(parents=True, exist_ok=False)
    output(args.out / "event-scores.json", rows)
    output(
        args.out / "metrics.json",
        dict(
            roster_sha256=receipt["roster_sha256"],
            packets=dict(Counter(r["packet"] for r in roster)),
            status=dict(Counter(r["status"] for r in roster)),
            groups={k: metrics(v) for k, v in groups.items()},
            competitions=comp,
            pending=pending,
            delivered_candidate_triples=0,
            prospective_pairs=0,
        ),
    )


if __name__ == "__main__":
    main()
