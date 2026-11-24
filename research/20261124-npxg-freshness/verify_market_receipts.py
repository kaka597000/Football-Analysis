"""Offline integrity and competition check for the retained October sample. No URLs, headers or raw feeds are copied."""

import csv
from argparse import ArgumentParser
from collections import Counter
from pathlib import Path

from audit import ROOT, csvout, jread, sha, utc


def capture_identity(r):
    if "capture" in r:
        return r["capture"]["sha256"], utc(r["capture"]["captured_at_utc"])
    if "raw_response_sha256" in r:
        if "capture_completed_utc" in r:
            return r["raw_response_sha256"], utc(r["capture_completed_utc"])
        stamps = {utc(x["capture_completed_utc"]) for x in r["fixtures"]}
        if len(stamps) != 1:
            raise ValueError("Ambiguous odds acquisition clock")
        return r["raw_response_sha256"], stamps.pop()
    if "raw_response" in r:
        return r["raw_response"]["sha256"], utc(r["captured_utc"]["completed"])
    return r["sha256"], utc(r["queried_at_utc"])


def run(path, out):
    folder = path.parent
    leagues = {
        "eng.1": "England Premier League",
        "esp.1": "Spain La Liga",
        "ger.1": "Germany Bundesliga",
        "ita.1": "Italy Serie A",
        "fra.1": "France Ligue 1",
    }
    candidates = {
        r["understat_id"]: r
        for r in jread(folder / "audit_summary.json")["rows_detail_for_audit"]
    }
    selected = []
    with path.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    for q in rows:
        summary = candidates[q["understat_id"]]
        source = Path(q["source_file"])
        receipt = Path(q["receipt_path"])
        h1, t = capture_identity(jread(receipt))
        if (
            sha(source.read_bytes()) != h1
            or h1 != q["raw_sha256"]
            or sha(receipt.read_bytes()) != q["receipt_sha256"]
            or t != utc(q["captured_at"])
        ):
            raise ValueError(
                "Source, completed acquisition clock or receipt has changed"
            )
        event = [
            e
            for e in jread(source)["events"]
            if str(e["event_id"]) == str(summary["event_id"])
        ]
        if len(event) != 1:
            raise ValueError("Ambiguous main-line identity")
        e = event[0]
        ou = e["markets"]["ou"]
        if (
            e["competition_name"] != leagues[q["competition"]]
            or e["ir"] is not False
            or e["score"] not in (None, "")
            or utc(e["start_ts"]) != utc(q["kickoff_at"])
            or [float(ou[k]) for k in ("line", "over", "under")]
            != [float(q[k]) for k in ("line", "over_price", "under_price")]
            or t > utc(summary["cutoff"])
        ):
            raise ValueError("Original prematch league, line or identity differs")
        if not q["quote_at"].startswith("UNKNOWN"):
            raise ValueError("Review unexpected bookmaker clock")
        data = {k: v for k, v in q.items() if k not in ("source_file", "receipt_path")}
        selected.append(
            data
            | {
                "quote_at": "",
                "clock_basis": "capture_only",
                "feed_event_id": summary["event_id"],
            }
        )
    csvout(out, selected)
    print(
        "Verified bound raw + receipt + pre-cutoff completed capture + competition",
        len(selected),
        dict(Counter(r["competition"] for r in selected)),
    )


if __name__ == "__main__":
    p = ArgumentParser(description=__doc__)
    p.add_argument(
        "--source",
        type=Path,
        default=ROOT / "source-audit/october-markets/october_t60_market_quotes.csv",
    )
    p.add_argument("--out", type=Path, default=ROOT / "output/verified-totals.csv")
    a = p.parse_args()
    run(a.source, a.out)
