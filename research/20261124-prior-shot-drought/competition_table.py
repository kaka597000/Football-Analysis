"""Publish competition coverage without exposing the source's match-level records."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path

from history import HERE, read_rows


def write_coverage(historical, clocked, inputs, output):
    scope = json.loads((HERE.parents[1] / "configs/analysis-scope.json").read_bytes())
    reasons = {}
    for r in inputs:
        reasons.setdefault(r["league"], Counter())[r["status"]] += 1
    with output.open("w", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["competition", "name", "panel", "roster", "predictions", "scored", "activated",
                         "first_base", "first_candidate", "three_base", "three_candidate",
                         "wdl_base", "wdl_candidate", "ou_nonpush", "ou_wins_base", "ou_wins_candidate",
                         "coverage_status"])
        for competition in scope["competitions"]:
            code, name = competition["code"], competition["name"]
            for panel, report in (("historical", historical), ("clocked", clocked)):
                cohort = report["by_competition"].get(code)
                if cohort is None:
                    writer.writerow([code,name,panel,0,0,0,0,*[""]*9,
                                     "no_source_panel" if panel=="historical" else "not_in_original_batch"])
                    continue
                m = cohort["metrics"]
                hits = [m[k][a] if m[k]["n"] else "" for k in ("first","matrix_three","wdl")
                        for a in ("base","candidate")]
                notes = ("retained_domestic_archive" if panel=="historical" else
                         ", ".join(f"{k}={v}" for k,v in sorted(reasons[code].items())))
                writer.writerow([code,name,panel,*[cohort[k] for k in ("roster","forecast","scored","activated")],
                                 *hits,m["total_full_win"]["n"],
                                 *[m["total_full_win"][a] if m["total_full_win"]["n"] else "" for a in ("base","candidate")],
                                 notes])


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    for name in ("historical","clocked","clocked-inputs","out"):
        p.add_argument("--"+name, required=True, type=Path)
    args = p.parse_args()
    write_coverage(json.loads(args.historical.read_bytes()), json.loads(args.clocked.read_bytes()),
                   read_rows(args.clocked_inputs), args.out)
