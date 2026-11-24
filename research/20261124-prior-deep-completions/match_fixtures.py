"""Bind the October unplayed roster to ESPN fixtures using only IDs, names, and times."""

import argparse
import csv
import gzip
import io
import json
import tarfile
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from build_inputs import HERE, git_file, sha


def token(name):
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", name.casefold()) if ch.isalnum()
    )


def write_csv(path, records, fields):
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)


def unique_club_ids(teams):
    keys = defaultdict(set)
    for ident, names in teams.items():
        for name in names:
            if len(name) >= 3:
                keys[name].add(ident)
    return keys


def match(restored, inputs, data, out):
    p = json.loads((HERE / "protocol.json").read_bytes())
    inputs_meta = json.loads((inputs / "input-manifest.json").read_bytes())["files"]
    t_rows = [
        json.loads(s)
        for s in git_file(data, p["data_git"], "normalized-results.jsonl").splitlines()
    ]
    target_ids = set()
    output = []
    audit = []
    prior_alias_bytes = git_file(
        data,
        p["data_git"],
        "workspace-updates/20261123-all-scope-kpi/forward-observations.tar.gz",
    )
    with tarfile.open(fileobj=io.BytesIO(prior_alias_bytes)) as arc:
        names_lookup = json.load(arc.extractfile("forward/model-aliases.json"))
    for code in p["covered_source"]:
        meta = next(
            v for v in inputs_meta if v["competition"] == code and v["season"] == 2026
        )
        wire = (restored / meta["path"]).read_bytes()
        if sha(wire) != meta["wire_sha256"]:
            raise ValueError("Wrong October schedule file")
        s = json.loads(gzip.decompress(wire))
        clubs = defaultdict(set)
        roster = []
        for f in s["dates"]:
            for role in ["h", "a"]:
                club = f[role]
                clubs[club["id"]].update(
                    token(club[k]) for k in ("title", "short_title") if club.get(k)
                )
            if (
                f["isResult"] is False
                and "2026-11-02" <= f["datetime"][:10] < "2026-11-22"
            ):
                roster.append(f)
        matches = []
        other = defaultdict(set)
        for r in t_rows:
            if r["competition"] != code or not "2026-10-29" <= r["date"] < "2026-11-26":
                continue
            ref = r["raw_reference"]
            raw = gzip.decompress(git_file(data, p["data_git"], ref["path"]))
            if sha(raw) != ref["sha256"]:
                raise ValueError("Wrong retained ESPN evidence")
            header = json.loads(raw)["header"]
            matches_raw = header["competitions"]
            if (
                header["league"]["slug"] != code
                or str(header["id"]) != r["match_id"].removeprefix("espn:")
                or len(matches_raw) != 1
            ):
                raise ValueError("Unverified ESPN identity")
            c = matches_raw[0]
            by_side = {t["homeAway"]: t["team"] for t in c["competitors"]}
            date = datetime.fromisoformat(c["date"].replace("Z", "+00:00"))
            if date != datetime.fromisoformat(r["kickoff_at"]):
                raise ValueError("Unverified kickoff")
            ids = []
            for role in ["home", "away"]:
                team = by_side[role]
                eid = str(team["id"])
                ids.append(eid)
                if r[role] != "espn:" + eid:
                    raise ValueError("Unverified team identity")
                other[eid].update(
                    token(str(team[k]))
                    for k in (
                        "name",
                        "displayName",
                        "shortDisplayName",
                        "abbreviation",
                        "slug",
                    )
                    if team.get(k)
                )
                if name := names_lookup.get(code, {}).get(eid):
                    other[eid].add(token(name))
            matches.append((r["match_id"], date, ids, ref["sha256"], by_side))
        left = unique_club_ids(clubs)
        right = unique_club_ids(other)
        mapping = defaultdict(set)
        for name, ids in left.items():
            if len(ids) == len(right.get(name, set())) == 1:
                mapping[next(iter(ids))].add(next(iter(right[name])))
        reverse = defaultdict(set)
        for understat, espn_ids in mapping.items():
            for espn in espn_ids:
                reverse[espn].add(understat)
        for f in roster:
            h, a = f["h"]["id"], f["a"]["id"]
            nominal = datetime.fromisoformat(f["datetime"]).replace(tzinfo=timezone.utc)
            found = [
                row
                for row in matches
                if abs((nominal - row[1]).total_seconds()) <= 72 * 3600
                and mapping[h] == {row[2][0]}
                and mapping[a] == {row[2][1]}
                and reverse[row[2][0]] == {h}
                and reverse[row[2][1]] == {a}
            ]
            brief = {
                "understat_match_id": f["id"],
                "competition": code,
                "understat_home_id": h,
                "understat_away_id": a,
                "home": f["h"]["title"],
                "away": f["a"]["title"],
                "scheduled": f["datetime"],
            }
            if len(found) != 1:
                audit.append(
                    dict(
                        **brief,
                        reason="missing/ambiguous clubs or kickoff",
                        possible_home_ids="|".join(sorted(mapping[h])),
                        possible_away_ids="|".join(sorted(mapping[a])),
                    )
                )
                continue
            mid, date, ids, digest, teams = found[0]
            if mid in target_ids:
                raise ValueError("Fixture resolved twice")
            target_ids.add(mid)
            output.append(
                dict(
                    **brief,
                    espn_event_id=mid,
                    espn_home_id=ids[0],
                    espn_away_id=ids[1],
                    espn_datetime=date.isoformat(),
                    espn_home=teams["home"]["displayName"],
                    espn_away=teams["away"]["displayName"],
                    stored_summary_sha256=digest,
                    identity_rule="BOTH_UNIQUE_ORDERED_CLUB_NAMES_OR_CODES_WITHIN_LEAGUE_AND_72H",
                )
            )
    out.mkdir(parents=True, exist_ok=True)
    write_csv(out / "bridges.csv", output, list(output[0]))
    write_csv(
        out / "bridge-gaps.csv",
        audit,
        [
            "understat_match_id",
            "competition",
            "understat_home_id",
            "understat_away_id",
            "home",
            "away",
            "scheduled",
            "reason",
            "possible_home_ids",
            "possible_away_ids",
        ],
    )
    print("Matched original fixtures", len(output), "missing or ambiguous", len(audit))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    for key in ("restored", "inputs", "data", "out"):
        ap.add_argument("--" + key, type=Path, required=True)
    args = ap.parse_args()
    match(args.restored, args.inputs, args.data, args.out)
