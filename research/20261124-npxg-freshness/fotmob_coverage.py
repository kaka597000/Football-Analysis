"""Join retained FotMob FT npxG to independently identified last-six slots, without reading target scores."""

import argparse
import csv
from collections import Counter, defaultdict
from datetime import date
from math import isfinite
from pathlib import Path
from statistics import median

from audit import (
    ROOT,
    SLUG,
    args_for,
    csvout,
    dump,
    jread,
    key,
    reference_calendar,
    utc,
)

LEAGUES = {"eng.1": "47", "esp.1": "87", "ger.1": "54", "ita.1": "55", "fra.1": "53"}


def read_rows(path):
    with path.open(encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def verified_teams(records, clubs, calendar):
    aliases = defaultdict(set)
    for c in clubs:
        for name in (c["calendar_name"], c["understat_name"]):
            aliases[key(name)].add(c["understat_id"])
    names = defaultdict(set)
    for r in records:
        for role in ("home", "away"):
            names[r[role + "_fotmob_team_id"]].add(r[role + "_name"])
    match = {}
    proof = {}
    for tid, values in names.items():
        cand = set().union(*(aliases[key(v)] for v in values))
        if len(cand) == 1:
            match[tid] = cand.pop()
            proof[tid] = (
                "unique same-league spelling in independently bound club crosswalk"
            )
    for _ in range(len(names)):
        votes = defaultdict(lambda: defaultdict(set))
        for r in records:
            instant = utc(r["kickoff_utc"])
            h = r["home_fotmob_team_id"]
            a = r["away_fotmob_team_id"]
            for unknown, known, ui, ki in ((h, a, 0, 1), (a, h, 1, 0)):
                if unknown in match or known not in match:
                    continue
                for pair in calendar.get(instant, []):
                    if pair[ki] == match[known]:
                        votes[unknown][pair[ui]].add((known, r["fotmob_match_id"]))
        change = {}
        for tid, cs in votes.items():
            if len(cs) != 1:
                continue
            identity, obs = next(iter(cs.items()))
            if len({o for o, _ in obs}) >= 2:
                change[tid] = identity
                proof[tid] = (
                    "ordered same UTC kickoff against distinct verified opponents "
                    + ",".join(sorted(g for _, g in obs))
                )
        if not change:
            break
        match.update(change)
    if len(set(match.values())) != len(match):
        raise ValueError("Overlapping FotMob team aliases")
    return match, proof


def compatible_record(r):
    if (
        r["finished_90_no_et_marker"] != "True"
        or r["full_time_reason"] != "FT"
        or not r["source_commit_time_utc"]
        or not r["member_raw_sha256"]
    ):
        return False
    if r["captured_at_utc"] and (
        r["meta_sha256_matches_raw"] != "True" or not r["meta_file_sha256"]
    ):
        return False
    vals = [
        float(r["native_" + col + "_" + side])
        for col, side in [
            ("npxg", "home"),
            ("npxg", "away"),
            ("npxga", "away"),
            ("npxga", "home"),
        ]
    ]
    return all(isfinite(v) and v >= 0 for v in vals) and vals[:2] == vals[2:]


def summarize(slots):
    grouped = defaultdict(list)
    for s in slots:
        grouped[s["competition"], s["target"], s["side"]].append(s)
    team_fresh = []
    complete = 0
    by_state = Counter(s["state"] for s in slots)
    sides = Counter()
    for (code, target, side), xs in grouped.items():
        hits = [x for x in xs if x["verified"]]
        if hits:
            team_fresh.append(
                (
                    date.fromisoformat(xs[0]["kickoff"][:10])
                    - max(date.fromisoformat(r["prior_date"]) for r in hits)
                ).days
            )
        sides[len(hits)] += 1
    games = {(s["competition"], s["target"]) for s in slots}
    for code, target in games:
        complete += all(
            sum(r["verified"] for r in grouped[code, target, side]) == 6
            for side in ("home", "away")
        )
    return dict(
        needed_slots=len(slots),
        verified_slots=sum(s["verified"] for s in slots),
        distinct_verified_matches=len({r["fotmob_id"] for r in slots if r["verified"]}),
        states=dict(by_state),
        teams=len(grouped),
        complete_sides=sides[6],
        fixtures=len(games),
        complete_fixtures=complete,
        latest_observed_median_days_only_on_sides_with_one_or_more=median(team_fresh)
        if team_fresh
        else None,
        age_observed_sides=len(team_fresh),
        count_by_observed_history=dict(sorted(sides.items())),
    )


def run(args):
    native = read_rows(args.fotmob_canonical)
    ids = read_rows(args.out / "identity.csv")
    windows = [w for w in jread(args.out / "windows.json") if w["window"] == "november"]
    independent, _ = reference_calendar(args)
    matches = {}
    proofs = []
    inventory = Counter()
    for code, lgid in LEAGUES.items():
        rows = [
            r
            for r in native
            if r["competition"] == code
            and r["league_id"] == lgid
            and compatible_record(r)
        ]
        clubs = [c for c in ids if c["competition"] == code]
        byname = {c["calendar_name"]: c["understat_id"] for c in clubs}
        real = defaultdict(list)
        for (h, a), g in independent[code].items():
            if g["finished"] and g["kickoff"] and h in byname and a in byname:
                real[g["kickoff"]].append((byname[h], byname[a]))
        teams, basis = verified_teams(rows, clubs, real)
        for t, u in sorted(teams.items()):
            proofs.append(
                dict(competition=code, fotmob_id=t, understat_id=u, proof=basis[t])
            )
        for r in rows:
            h, a = (
                teams.get(r["home_fotmob_team_id"]),
                teams.get(r["away_fotmob_team_id"]),
            )
            t = utc(r["kickoff_utc"])
            if not h or not a:
                inventory["unverified_team_crosswalk"] += 1
                continue
            if (h, a) not in real.get(t, ()):
                inventory["not_in_independent_completed_calendar_at_exact_utc"] += 1
                continue
            k = (code, h, a, t.date().isoformat())
            if k in matches:
                raise ValueError(
                    "Multiple FotMob source identities match one ordered fixture"
                )
            matches[k] = r
            inventory["independent_ordered_match_exact_utc"] += 1
    slots = []
    for w in windows:
        for role, s in zip(("home", "away"), w["sides"]):
            for p in s["slots"]:
                # Preserve order using the independent calendar's original home/away for the previous event.
                previous = [
                    (k, v)
                    for k, v in independent[w["competition"]].items()
                    if v["day"] == p["day"]
                ]
                cmap = {
                    c["calendar_name"]: c["understat_id"]
                    for c in ids
                    if c["competition"] == w["competition"]
                }
                ordered = [
                    (cmap[h], cmap[a])
                    for (h, a), v in previous
                    if h in cmap
                    and a in cmap
                    and {cmap[h], cmap[a]} == {p["team"], p["opponent"]}
                ]
                r = (
                    matches.get((w["competition"], *ordered[0], p["day"]))
                    if len(ordered) == 1
                    else None
                )
                state = "NO_VERIFIED_NATIVE_PAIR"
                verified = False
                if r:
                    bound = utc(r["source_commit_time_utc"])
                    capture = (
                        utc(r["captured_at_utc"]) if r["captured_at_utc"] else None
                    )
                    verified = max(bound, capture or bound) <= utc(w["cutoff"])
                    state = (
                        "AFTER_CUTOFF"
                        if not verified
                        else ("CAPTURE_AND_GIT" if capture else "GIT_BOUND_ONLY")
                    )
                slots.append(
                    dict(
                        competition=w["competition"],
                        target=w["id"],
                        side=role,
                        rank=p["rank"],
                        prior_native_id=p["id"],
                        prior_date=p["day"],
                        kickoff=w["kickoff"],
                        cutoff=w["cutoff"],
                        verified=verified,
                        state=state,
                        fotmob_id=r["fotmob_match_id"] if r else None,
                        member_sha256=r["member_raw_sha256"] if r else None,
                        captured_at=r["captured_at_utc"] if r else None,
                        known_by=r["source_commit_time_utc"] if r else None,
                        understat_also_available=p["covered"],
                    )
                )
    csvout(args.out / "fotmob-slots.csv", slots)
    csvout(args.out / "fotmob-identities.csv", proofs)
    info = dict(
        canonical_distinct=len(native),
        exact_query_time=sum(bool(r["captured_at_utc"]) for r in native),
        inventory=dict(inventory),
        november=summarize(slots),
        by_league={
            k: summarize([r for r in slots if r["competition"] == k]) for k in SLUG
        },
        method="Single provider only, verified raw native FT without extra-time, unique ordered clubs and exact independently checked kickoff. Both receipt and archival upper bound before cutoff for exact captures. Git bound shown separately when an exact query time was not recorded.",
    )
    dump(args.out / "fotmob-coverage.json", info)
    print(info["november"])


if __name__ == "__main__":
    p = args_for(argparse.ArgumentParser(description=__doc__))
    p.add_argument(
        "--fotmob-canonical",
        type=Path,
        default=ROOT / "source-audit/fotmob/canonical-fields.csv",
    )
    run(p.parse_args())
