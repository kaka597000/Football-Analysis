"""Bound the Nov 17 Nations panel from the original, digest-bound provider inventory."""

import argparse
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import subprocess
import tarfile

from opportunities import clock, require
from research.analysis_freeze import verify
from replay import DATA

PACK = "workspace-updates/20261117-nations-xg-research/nations-xg-evidence.tar.gz"
SHA = "1f5722db6e66467f3979c612817fa867fb0f9946e1fe36864c9f86566772d591"
ROOT = "research/nations-xg-20261117/"


def inventory(repo):
    blob = subprocess.check_output(["git", "-C", str(repo), "show", DATA + ":" + PACK])
    require(sha256(blob).hexdigest() == SHA, "late_archive_digest")
    with tarfile.open(fileobj=BytesIO(blob)) as arc:

        def read(member, expected=None):
            raw = arc.extractfile(ROOT + member).read()
            require(
                expected is None or sha256(raw).hexdigest() == expected,
                "native_body_digest",
            )
            return json.loads(raw)

        allrows = read("data/xg-records.json")["rows"]
        canonical_count = len(read("canonical-history.json"))
        good = []
        observed = [
            v for v in allrows if v["quality"].get("status") == "STATS_CAPTURED"
        ]
        for row in observed:
            q = row["quality"]
            read("data/raw/" + Path(row["source_path"]).name, row["source_sha256"])
            if (
                q.get("global_estimated") is False
                and q.get("shot_counts") is not None
                and q["shot_counts"] == q["stat_shots"]
            ):
                good.append(row)
        targets = range(401861189, 401861197)
        captures = read("data/future/captures.json")["requests"]
        ids = set()
        counts = []
        paired = 0
        for mid in targets:
            record = verify(read(f"forward/freezes/espn-{mid}.json"))
            f = record["fixture"]
            cut = clock(f["cutoff_at"])
            require(
                record["match_id"] == f["match_id"] == f"espn:{mid}"
                and record["revision"] == 1
                and record["evaluation_slot"] == "FIRST_PREMATCH"
                and record["previous_sha256"] is None
                and cut <= clock(record["recorded_at"]) < clock(f["kickoff_at"]),
                "later_first_issue",
            )
            name = f"espn-summary-{mid}.json"
            (cap,) = [v for v in captures if Path(v["raw_path"]).name == name]
            require(
                cap["http_status"] == 200
                and cap["body_saved"]
                and clock(cap["ended_at"]) <= cut,
                "identity_clock",
            )
            header = read("data/future/raw/" + name, cap["raw_sha256"])["header"]
            (game,) = header["competitions"]
            teams = {
                p["homeAway"]: "espn:" + str(p["team"]["id"])
                for p in game["competitors"]
            }
            require(
                str(header["id"]) == str(game["id"]) == str(mid)
                and clock(game["date"]) == clock(f["kickoff_at"])
                and teams == {"home": f["home"], "away": f["away"]},
                "later_target",
            )
            side_counts = []
            for team in teams.values():
                eligible = {
                    r["match_id"]
                    for r in good
                    if team in (r["home"], r["away"])
                    and clock(r["kickoff_at"]) < clock(r["observed_at"]) <= cut
                }
                ids.update(eligible)
                side_counts.append(len(eligible))
            counts.extend(side_counts)
            paired += min(side_counts) >= 3
    return dict(
        targets=len(targets),
        target_teams=len(counts),
        canonical_match_history=canonical_count,
        provider_opportunity_inventory=len(allrows),
        source_captures=len(observed),
        nonestimated_count_matched=len(good),
        unique_priors=len(ids),
        teams_at_least_one=sum(n >= 1 for n in counts),
        max_priors_per_team=max(counts),
        teams_at_least_three=sum(n >= 3 for n in counts),
        potential_both_three=paired,
        level="upper bound before exact calendar order and source-family matching",
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-repo", required=True, type=Path)
    print(json.dumps(inventory(ap.parse_args().data_repo), sort_keys=True, indent=2))
