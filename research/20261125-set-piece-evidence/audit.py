"""Reopen #78's pinned shotmaps, then join the original graded matrices to their coverage."""

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import australia
import early
import grade
from opportunities import check_source, clock, native_pair, recent_three, require
from replay import ROOT, evidence, packed_value

from located import profile


def slots_for(target, cutoff, provider_id, source, teams, calendar_names=None):
    ids = native_pair(target)
    require(
        str(target["general"]["matchId"]) == provider_id and ids == tuple(teams),
        "target_identity",
    )
    fixture_time = clock(target["general"]["matchTimeUTCDate"])
    require(
        not target["general"]["started"] and cutoff < fixture_time,
        "target_not_prematch",
    )
    slots = []
    for i, team in enumerate(ids):
        cal, _ = source("teams", team, calendar_names[i] if calendar_names else None)
        prior_games = recent_three(cal, team, provider_id, ids, fixture_time, cutoff)
        for rank in range(3):
            slot = dict(
                side=("home", "away")[i],
                rank=rank + 1,
                team=team,
                status="calendar_short",
            )
            if rank < len(prior_games):
                prior = prior_games[rank]
                slot["prior"] = str(prior["id"])
                try:
                    detail, received = source("matchDetails", slot["prior"], None)
                    result = profile(detail, prior, received, cutoff, provider_id, team)
                except ValueError as exc:
                    slot["status"] = str(exc)
                else:
                    slot.update(
                        result, status="complete", received_at=received.isoformat()
                    )
            slots.append(slot)
    return slots


def ucl_slots(repo, records):
    with TemporaryDirectory() as path:
        tmp = Path(path)
        with evidence(repo, tmp) as arc:
            data = tmp / "ucl-evidence"
            data.mkdir()
            idx = "ucl-evidence/recentxg_injury_rest.json"
            (tmp / idx).write_bytes(arc.extractfile(ROOT + idx).read())
            index, receipts = early.read_index(tmp)
            index_at = clock(index["queried_at_utc"])
            matches = {row["id"]: row for row in records}
            out = {}
            for mapped in index["target_matches"]:
                mid = "espn:" + str(mapped["espn_event_id"])
                row = matches[mid]
                cutoff = clock(row["cutoff"])
                require(clock(mapped["utc"]) == clock(row["kickoff"]), "target_kickoff")
                teams = [
                    str(mapped[side + "_fotmob_team_id"]) for side in ("home", "away")
                ]
                provider_id = str(mapped["fotmob_match_id"])

                def source(kind, fid, _name):
                    name = f"{kind}-{fid}.json"
                    require(name in receipts, "no_raw_receipt")
                    dest = data / name
                    if not dest.exists():
                        dest.write_bytes(
                            arc.extractfile(ROOT + "ucl-evidence/raw/" + name).read()
                        )
                    receipt = receipts[name]
                    return check_source(
                        receipt, data, kind, fid, cutoff, index_at
                    ), clock(receipt["query_time_utc"])

                target, _ = source("matchDetails", provider_id, None)
                out[mid] = slots_for(target, cutoff, provider_id, source, teams)
                require(
                    [(s["team"], s.get("prior"), s["rank"]) for s in out[mid]]
                    == [(s["team"], s.get("prior"), s["rank"]) for s in row["slots"]],
                    "old_calendar_disagrees",
                )
            return out


def aus_slots(repo, row):
    cutoff = clock(row["cutoff"])
    with (
        TemporaryDirectory() as tmp,
        evidence(repo, Path(tmp), australia.PACK, australia.SHA) as arc,
    ):
        directory = australia.PREFIX + "data/"

        def source(kind, fid, name):
            name = name or ("match_" + fid)
            arg = "matchId" if kind == "matchDetails" else "id"
            url = f"https://www.fotmob.com/api/data/{kind}?{arg}={fid}&ccode3=INT"
            meta = packed_value(arc, directory + name + ".meta.json")
            raw = arc.extractfile(directory + name + ".json").read()
            captured = clock(meta["queried_at_utc"])
            require(
                meta["url"] == url
                and meta["http_status"] == 200
                and captured <= cutoff
                and sha256(raw).hexdigest() == meta["sha256"],
                "aus_raw_binding",
            )
            return json.loads(raw), captured

        target, _ = source("matchDetails", australia.NATIVE, "fotmob_match_details")
        require(
            clock(target["general"]["matchTimeUTCDate"]) == clock(row["kickoff"])
            and target["general"]["parentLeagueId"] == 113,
            "aus_target",
        )
        return slots_for(
            target,
            cutoff,
            australia.NATIVE,
            source,
            native_pair(target),
            ("team_sydney_fc", "team_central_coast_mariners"),
        )


def checked_input(path, sha):
    raw = path.read_bytes()
    require(sha256(raw).hexdigest() == sha, "first_matrix_lock")
    return json.loads(raw)


def band(total):
    return "0-2" if total <= 2 else "3" if total == 3 else "4+"


def coverage_table(rows):
    groups = {}
    for name, subset in (
        ("all", rows),
        ("current_miss", [r for r in rows if not r["current_top3"]]),
        ("both_miss", [r for r in rows if not (r["current_top3"] or r["market_top3"])]),
    ):
        groups[name] = {}
        for label in ("all", "0-2", "3", "4+"):
            selection = [
                r for r in subset if label == "all" or r["goals_band"] == label
            ]
            ok = sum(r["both_complete"] for r in selection)
            groups[name][label] = dict(
                fixtures=len(selection), usable=ok, unavailable=len(selection) - ok
            )
    return groups


def run(repo, ucl, aus):
    urows = checked_input(ucl, grade.BLIND_SHA)
    (arow,) = checked_input(
        aus, "0c6576c161540bb591d1d73967f767257c97344a31c71f9d494b161f6eecbcc2"
    )
    slots = ucl_slots(repo, urows)
    slots[arow["id"]] = aus_slots(repo, arow)
    rows = urows + [arow]
    labels = grade.certified(repo, urows) + [grade.australia_label(repo, arow)]
    lookup = {v["id"]: v for v in labels}
    require(len(lookup) == len(rows) == len(slots), "incomplete_roster")
    ledger, unique, states, attempts = [], {}, Counter(), Counter()
    xg = Counter()
    for row in rows:
        key = row["id"]
        result = lookup[key]
        require(
            (row["home"], row["away"], clock(row["kickoff"]))
            == (result["home"], result["away"], clock(result["kickoff"])),
            "label_identity",
        )
        s = slots[key]
        states.update(item["status"] for item in s)
        for item in [v for v in s if v["status"] == "complete"]:
            fid, counts = item["prior"], item["set_plays_by_team"]
            if fid in unique:
                require(unique[fid] == counts, "duplicate_prior_conflict")
            else:
                unique[fid] = counts
                for side in counts.values():
                    attempts.update(side)
                for side in item["set_play_xg_by_team"].values():
                    xg.update(side)
        counts = {
            side: sum(v["status"] == "complete" for v in s if v["side"] == side)
            for side in ("home", "away")
        }
        score = result["score"]
        ledger.append(
            dict(
                id=key,
                competition=result["competition"],
                cutoff=row["cutoff"],
                kickoff=row["kickoff"],
                regulation_score=score,
                goals_band=band(sum(score)),
                home_slots=counts["home"],
                away_slots=counts["away"],
                both_complete=min(counts.values()) == 3,
                current_top3=bool(
                    grade.assessment(row["team"], score, row["line"])["top3"]
                ),
                market_top3=bool(
                    grade.assessment(row["market"], score, row["line"])["top3"]
                ),
                slots=s,
            )
        )
    return dict(
        classified=coverage_table(ledger),
        slot_states=dict(states),
        unique_complete_priors=len(unique),
        complete_priors_with_zero_set_plays=sum(
            not any(counts.values()) for counts in unique.values()
        ),
        attempts_by_native_type=dict(sorted(attempts.items())),
        mean_xg_per_set_play={
            k: round(xg[k] / n, 6) for k, n in sorted(attempts.items())
        },
        complete_sides=sum(
            v[side + "_slots"] == 3 for v in ledger for side in ("home", "away")
        ),
        events=[{k: v for k, v in row.items() if k != "slots"} for row in ledger],
    ), ledger


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    for option in ("data-repo", "ucl", "aus", "summary", "private-ledger"):
        p.add_argument("--" + option, required=True, type=Path)
    a = p.parse_args()
    public, private = run(a.data_repo, a.ucl, a.aus)
    for dest, obj in ((a.summary, public), (a.private_ledger, private)):
        with dest.open("x") as out:
            json.dump(obj, out, indent=2, sort_keys=True, allow_nan=False)
            out.write("\n")
