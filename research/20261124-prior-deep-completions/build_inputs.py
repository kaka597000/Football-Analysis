"""Validate the pinned October source bytes and extract only fixture and prior team observations."""

import argparse
import gzip
import hashlib
import json
import math
import subprocess
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = "WCPP/data/staging/systematic-20261015/understat"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def git_file(repo, rev, name):
    return subprocess.check_output(["git", "-C", str(repo), "show", f"{rev}:{name}"])


def read_snapshot(restored, index, name):
    key = f"{BASE}/{name}"
    data = (restored / key).read_bytes()
    if index[key] != sha(data):
        raise ValueError(f"Archived file digest differs for {key}")
    return data


def season_rows(payload, fetched_at):
    teams = payload["teams"]
    schedule = payload["dates"]
    pairs = {(g["h"]["id"], g["a"]["id"]) for g in schedule}
    if len(pairs) != len(schedule) or len(pairs) != len(teams) * (len(teams) - 1):
        raise ValueError(
            "Expected the complete home-and-away regular league fixture grid"
        )
    by_date = {}
    for tid, team in teams.items():
        for row in team["history"]:
            key = tid, row["date"], row["h_a"]
            if key in by_date:
                raise ValueError(f"Ambiguous team-history key {key}")
            by_date[key] = row
    done, pending = [], []
    for game in schedule:
        dt, home, away = game["datetime"], game["h"]["id"], game["a"]["id"]
        if not all(
            teams[s]["title"] == game[side]["title"]
            for s, side in ((home, "h"), (away, "a"))
        ):
            raise ValueError("Team identity conflict in fixture")
        common = dict(
            id=str(game["id"]),
            date=dt[:10],
            scheduled=dt,
            home_id=home,
            away_id=away,
            home=teams[home]["title"],
            away=teams[away]["title"],
        )
        if game["isResult"] is False:
            pending.append(common)
            continue
        if (
            game["isResult"] is not True
            or datetime.fromisoformat(dt).replace(tzinfo=timezone.utc) >= fetched_at
        ):
            raise ValueError("Completed fixture has no prior-time basis")
        h, a = by_date[home, dt, "h"], by_date[away, dt, "a"]
        goals = [int(game["goals"][s]) for s in ("h", "a")]
        if [h["scored"], a["scored"]] != goals or [a["missed"], h["missed"]] != goals:
            raise ValueError(
                "Goals conflict between both team histories and the fixture"
            )
        xg = [float(game["xG"][s]) for s in ("h", "a")]
        if not all(
            math.isclose(x, y, abs_tol=0.0002)
            for x, y in zip(xg * 2, [h["xG"], a["xG"], a["xGA"], h["xGA"]])
        ):
            raise ValueError("xG conflict between fixture and team histories")
        deep = [h.get("deep"), a.get("deep")]
        if not all(type(x) is int and x >= 0 for x in deep) or deep != [
            a.get("deep_allowed"),
            h.get("deep_allowed"),
        ]:
            deep = [None, None]
        done.append(dict(**common, goals=goals, xg=xg, deep=deep))
    return {"completed": done, "scheduled": pending}


def build(data_repo, restored, index_path):
    protocol = json.loads((HERE / "protocol.json").read_bytes())
    raw_index = (
        index_path.read_bytes()
        if index_path
        else git_file(
            data_repo,
            protocol["input_backup_commit"],
            "workspace-backup-20261102/files.json",
        )
    )
    if sha(raw_index) != protocol["backup_index_sha256"]:
        raise ValueError("Backup manifest digest differs")
    index = {
        r["path"]: r["sha256"]
        for r in json.loads(raw_index)
        if r["path"].startswith(BASE + "/")
    }
    audit_bytes = read_snapshot(restored, index, "source-audit.json")
    audit = json.loads(audit_bytes)
    cohorts = {label: code for code, label in protocol["covered_source"].items()}
    seasons = {}
    manifests = []
    for request in audit["requests"]:
        league, year = request["league"], int(request["season_start_year"])
        path = f"{league}/{year}/league-data.response.gz"
        wire = read_snapshot(restored, index, path)
        decoded = gzip.decompress(wire)
        if (
            request["http_status"] != 200
            or sha(wire) != request["wire_sha256"]
            or sha(decoded) != request["decoded_sha256"]
        ):
            raise ValueError(f"Original source receipt differs for {path}")
        when = datetime.fromisoformat(request["fetched_at_beijing"])
        if when >= datetime.fromisoformat(
            protocol["input_backup_time"].replace("Z", "+00:00")
        ):
            raise ValueError("Capture is newer than the immutable snapshot")
        key = f"{cohorts[league]}:{year}"
        if key in seasons:
            raise ValueError("Repeated league-season")
        payload = json.loads(decoded)
        s = season_rows(payload, when)
        sides = [row for team in payload["teams"].values() for row in team["history"]]
        seasons[key] = s
        manifests.append(
            dict(
                competition=cohorts[league],
                team_observations=len(sides),
                audited_npxg_pairs=sum(
                    all(type(row.get(k)) in (float, int) for k in ("npxG", "npxGA"))
                    for row in sides
                ),
                season=year,
                path=f"{BASE}/{path}",
                source=request["requested_url"],
                wire_sha256=sha(wire),
                decoded_sha256=sha(decoded),
                capture_at=when.isoformat(),
                completed=len(s["completed"]),
                unplayed=len(s["scheduled"]),
                verified_deep=sum(g["deep"][0] is not None for g in s["completed"]),
            )
        )
    if set(seasons) != {
        f"{c}:{y}" for c in cohorts.values() for y in range(2020, 2027)
    }:
        raise ValueError("Incomplete five-league seven-season vintage")
    identity = dict(
        backup_commit=protocol["input_backup_commit"],
        index_sha256=sha(raw_index),
        capture_audit_sha256=sha(audit_bytes),
        files=sorted(manifests, key=lambda v: (v["competition"], v["season"])),
    )
    return seasons, identity


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", required=True, type=Path)
    ap.add_argument(
        "--restored", required=True, type=Path, help="Restored root containing WCPP/"
    )
    ap.add_argument(
        "--index", type=Path, help="Optional local copy of the pinned backup files.json"
    )
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()
    seasons, identity = build(args.data, args.restored, args.index)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "team-inputs.json.gz").write_bytes(
        gzip.compress(
            json.dumps(seasons, sort_keys=True, separators=(",", ":")).encode(), mtime=0
        )
    )
    (args.out / "input-manifest.json").write_text(
        json.dumps(identity, indent=2, ensure_ascii=False) + "\n"
    )
    print(f"{len(seasons)} club league-seasons verified")
