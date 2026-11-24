"""Read the 22-November first issues and audit role history without opening target scores."""

import argparse
import json
import tarfile
from collections import Counter
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from research.analysis_freeze import verify
from research.availability import extract

from audit_archives import save, table
from people_evidence import (
    SIDES,
    admission,
    fotmob,
    player_ids,
    regular_fotmob_fixture,
    sha,
    times,
    utc,
)

ARCHIVE = "df67e4209ff042fba2473b770084e84d1401e085f25c8d629616aeaa5b3c72c2"
RECEIPTS = "46a13b07f27c4b43f3cac756a090dbff89c605d6bb7a9a4050708706b0b53910"


def archive_objects(archive, needed):
    out = {}
    with tarfile.open(archive, "r|gz") as t:
        for m in t:
            if not m.isfile() or not m.name.startswith("objects/"):
                continue
            key = m.name[8:]
            if key not in needed:
                continue
            raw = t.extractfile(m).read()
            if sha(raw) != key or key in out:
                raise ValueError("Missing integrity or ambiguous source object")
            out[key] = raw
    return out


def match_ids(doc):
    g = doc["general"]
    return str(g["matchId"]), [str(g[s + "Team"]["id"]) for s in SIDES]


def starting_keeper(team, team_id):
    if str(team.get("id")) != team_id:
        return None
    players = team.get("starters") or []
    if not player_ids(players):
        return None
    # Require both FotMob's goalkeeper slot and normal playing position, and a unique stable ID.
    keepers = [
        str(p["id"])
        for p in players
        if p.get("positionId") == 11 and p.get("usualPlayingPositionId") == 0
    ]
    return keepers[0] if len(keepers) == 1 else None


def history_keeper(blob, ref, team_id, league, target_kickoff, cutoff):
    if not blob:
        return None, "NO_BOUND_HISTORY_BYTES"
    if sha(blob) != ref.get("source_sha256"):
        raise ValueError("Historical reference hash differs")
    if utc(ref["observed_at"]) > cutoff:
        return None, "HISTORY_CAPTURE_AFTER_CUTOFF"
    data = json.loads(blob)
    g = data["general"]
    mid, teams = match_ids(data)
    started = utc(g["matchTimeUTCDate"])
    u = urlsplit(ref["source_url"])
    history_id = ref["id"].removeprefix("fotmob:")
    if (
        u.netloc != "www.fotmob.com"
        or u.scheme != "https"
        or u.path not in ("/api/matchDetails", "/api/data/matchDetails")
        or parse_qs(u.query).get("matchId") != [mid]
        or history_id != mid
        or teams != [str(ref["fotmob_home_team_id"]), str(ref["fotmob_away_team_id"])]
        or team_id not in teams
        or len(set(teams)) != 2
        or started != utc(ref["kickoff_at"])
        or started >= target_kickoff
        or started + timedelta(minutes=90) > utc(ref["observed_at"])
    ):
        return None, "HISTORY_IDENTITY_OR_TIME"
    status = data["header"]["status"]
    halfs = status.get("halfs", {})
    ln = data.get("content", {}).get("lineup") or {}
    if (
        g.get("leagueId") != league
        or g.get("finished") is not True
        or status.get("cancelled")
        or (status.get("reason") or {}).get("short") != "FT"
        or any(v for k, v in halfs.items() if "extrahalf" in k.casefold())
        or ln.get("lineupType") != "standard"
    ):
        return None, "NON_LEAGUE_FT90_OR_NO_ACTUAL_ROSTER"
    if ln.get("matchId") is not None and str(ln["matchId"]) != mid:
        return None, "HISTORY_LINEUP_IDENTITY_MISMATCH"
    i = teams.index(team_id)
    side = SIDES[i]
    if ref.get("side") and ref["side"] != side:
        return None, "HISTORY_WRONG_SIDE"
    starter = starting_keeper(ln.get(side + "Team") or {}, team_id)
    return (starter, "BOUND_PRIOR_GK") if starter else (None, "NO_UNIQUE_STARTING_GK")


def usual_keeper(ids):
    if len(ids) != 6 or any(not i for i in ids):
        return None
    count = Counter(ids)
    keeper, n = count.most_common(1)[0]
    return keeper if n >= 4 else None


def schedule_check(
    packet, manifest, source, area, side, team, espn_team, league, prior, target, cutoff
):
    """Corroborate the six selected fixtures with the same team's separately archived calendar."""
    if area not in ("west", "other"):
        return "NO_BOUND_TEAM_CALENDAR"
    row = packet[side]
    ref = row.get("team_source") or {
        "url": row.get("source_url"),
        "sha256": row.get("source_sha256"),
        "raw_file": row.get("raw_file"),
    }
    if not all(ref.get(k) for k in ("url", "sha256", "raw_file")):
        return "NO_BOUND_TEAM_CALENDAR"
    url = urlsplit(ref["url"])
    key = "form/" + area + "/" + ref["raw_file"]
    if (
        row["team_id"] != espn_team
        or utc(packet["observed_at"]) > cutoff
        or utc(packet["kickoff_at"]) != target
        or url.scheme != "https"
        or url.netloc != "www.fotmob.com"
        or url.path not in ("/api/data/teams", "/api/teams")
        or parse_qs(url.query).get("id") != [team]
        or manifest.get(key) != ref["sha256"]
        or ref["sha256"] not in source
    ):
        return "UNBOUND_TEAM_CALENDAR"
    doc = json.loads(source[ref["sha256"]])
    if str(doc["details"]["id"]) != team:
        return "TEAM_CALENDAR_ID_MISMATCH"
    sequence = []
    for game in doc["fixtures"]["allFixtures"]["fixtures"]:
        status = game["status"]
        if game.get("tournament", {}).get("leagueId") != league or not status.get(
            "utcTime"
        ):
            continue
        at = utc(status["utcTime"])
        if at >= target or status.get("cancelled"):
            continue
        if str(game["home"]["id"]) != team and str(game["away"]["id"]) != team:
            return "TEAM_CALENDAR_ID_MISMATCH"
        sequence.append(
            (
                at,
                str(game["id"]),
                status.get("finished") is True
                and (status.get("reason") or {}).get("short") == "FT",
            )
        )
    six = sorted(sequence, reverse=True)[:6]
    if len(six) != 6:
        return "INCOMPLETE_TEAM_CALENDAR"
    expected = sorted(
        [(utc(r["kickoff_at"]), r["id"].removeprefix("fotmob:")) for r in prior],
        reverse=True,
    )
    return (
        "SIX_MATCH_CALENDAR_CONFIRMED"
        if all(finished for _, _, finished in six)
        and [(at, i) for at, i, _ in six] == expected
        else "SIX_NOT_CONFIRMED_BY_CALENDAR"
    )


def audit(archive, receipts, out):
    import hashlib

    with archive.open("rb") as f:
        h = hashlib.file_digest(f, "sha256").hexdigest()
    refs = json.loads(receipts.read_bytes())["matches"]
    if h != ARCHIVE or sha(receipts.read_bytes()) != RECEIPTS:
        raise ValueError("Different batch")
    with tarfile.open(archive, "r:gz") as t:
        manifest = json.load(t.extractfile("manifest.json"))["files"]
    records = {eid: manifest[f"freeze-copies/{eid}-comprehensive.json"] for eid in refs}
    predecessors = archive_objects(archive, set(records.values()))
    frozen = {eid: verify(json.loads(predecessors[h])) for eid, h in records.items()}
    hashes = set()
    for r in frozen.values():
        hashes.add(r["payload"]["availability"]["raw_source"]["response_sha256"])
        for side in SIDES:
            hashes.update(
                v["source_sha256"]
                for v in r["fixture"]["recent_league_form"][side]["matches"]
            )
    packet_paths = {
        eid: next(
            (
                k
                for k in ("form/west/" + eid + ".json", "form/other/" + eid + ".json")
                if k in manifest
            ),
            None,
        )
        for eid in records
    }
    hashes.update(manifest[k] for k in packet_paths.values() if k)
    hashes.update(
        v for k, v in manifest.items() if "/fotmob-team-" in k or "/fotmob_team_" in k
    )
    reference = json.loads(
        (Path(__file__).parent / "evidence/nov22-club-source-review.json").read_bytes()
    )
    reviewed_files = reference["files"]
    if manifest[reference["index"]] != reference["index_sha256"] or any(
        manifest[r["path"]] != r["sha256"] for r in reviewed_files
    ):
        raise ValueError("First-party content review belongs to another edition")
    hashes.update(r["sha256"] for r in reviewed_files)
    hashes.add(reference["index_sha256"])
    source = archive_objects(archive, hashes)
    if not all(r["sha256"] in source for r in reviewed_files) or utc(
        json.loads(source[reference["index_sha256"]])["observed_at"]
    ) != utc(reference["received_at"]):
        raise ValueError("Original reviewed first-party source or receipt unavailable")
    rows = []
    history_rows = []
    for eid, r in sorted(frozen.items()):
        f = r["fixture"]
        co = f["competition"]
        leg = f["recent_league_form"]
        av = r["payload"]["availability"]
        sr = av["raw_source"]
        mid = f["match_id"]
        target_kick = utc(f["kickoff_at"])
        cut = utc(f["cutoff_at"])
        expected = refs[eid]["comprehensive"]
        if (
            r["track"] != "comprehensive"
            or r["stage"] != "prematch"
            or r["revision"] != 1
            or r["previous_sha256"] is not None
            or r["evaluation_slot"] != "FIRST_PREMATCH"
            or r["freeze_id"] != expected["freeze_id"]
            or r["record_sha256"] != expected["sha256"]
            or utc(r["recorded_at"]) != utc(expected["recorded_at"])
            or not cut <= utc(r["recorded_at"]) < target_kick
        ):
            raise ValueError("Different or late first freeze")
        raw = source[sr["response_sha256"]]
        doc = json.loads(raw)
        rawmid, clubs = match_ids(doc)
        if (
            manifest.get(sr["file"]) != sha(raw)
            or mid != "espn:" + eid
            or leg["match_id"] != mid
            or f["identity_verified"] is not True
            or str(f["fotmob_fixture_id"]) != rawmid
            or clubs != [str(leg[s]["fotmob_team_id"]) for s in SIDES]
            or [f[s] for s in SIDES] != [leg[s]["team_id"] for s in SIDES]
            or target_kick != utc(doc["general"]["matchTimeUTCDate"])
            or utc(av["observed_at"]) != utc(sr["query_time"])
        ):
            raise ValueError("Invalid target/source bridge")
        if utc(sr["query_time"]) > cut:
            raise ValueError("Target source after first cutoff")
        fm = fotmob(
            raw,
            {
                "sha256": sr["response_sha256"],
                "received_at": sr["query_time"],
                "source_url": sr["source_url"],
            },
        )
        stage_ok = regular_fotmob_fixture(f, doc["general"])
        target = {
            "match_id": fm["match_id"],
            "competition": co,
            "kickoff_at": f["kickoff_at"],
            "cutoff_at": f["cutoff_at"],
            "home": "fotmob:" + clubs[0],
            "away": "fotmob:" + clubs[1],
        }
        clocks = {
            label: admission(target, fm | {"competition": co}, clock)
            for label, clock in times(f).items()
        }
        if not all(x == fm["kind"] for x in clocks.values()):
            raise ValueError("Frozen source did not qualify at every reported clock")
        ln = doc.get("content", {}).get("lineup") or {}
        gaps = []
        listed = []
        roles = []
        acts = []
        lineups = []
        items = extract(doc)["sides"]
        packet_path = packet_paths[eid]
        packet = json.loads(source[manifest[packet_path]]) if packet_path else {}
        if packet and str(packet.get("id")).removeprefix("espn:") != eid:
            raise ValueError("Different source calendar fixture")
        calendars = []
        calendar_role = []
        calendar_changed = []
        for i, s in enumerate(SIDES):
            tm = clubs[i]
            ref = leg[s]["matches"]
            ks = []
            if (
                leg[s]["team_id"] != f[s]
                or leg["cohort"] != f["cohort"]
                or any(v["cohort"] != leg["cohort"] for v in ref)
            ):
                raise ValueError("Prior sequence belongs to another team or scope")
            if (
                len(ref) != 6
                or len({v["id"] for v in ref}) != 6
                or ("fotmob:" + rawmid) in {v["id"] for v in ref}
            ):
                raise ValueError("Frozen six distinct prior fixtures required")
            ds = [utc(v["kickoff_at"]) for v in ref]
            if len(set(ds)) != 6 or any(v >= target_kick for v in ds):
                raise ValueError("History is not strictly prior")
            for v in ref:
                keeper, reason = history_keeper(
                    source.get(v["source_sha256"]),
                    v,
                    tm,
                    doc["general"]["leagueId"],
                    target_kick,
                    cut,
                )
                ks.append(keeper)
                history_rows.append(
                    dict(
                        target=mid,
                        competition=co,
                        in_scope=stage_ok,
                        side=s,
                        prior_fixture=v["id"],
                        history_source_sha256=v["source_sha256"],
                        history_observed_at=v["observed_at"],
                        prior_kickoff=v["kickoff_at"],
                        regular_time_verified_and_keeper=bool(keeper),
                        reason=reason,
                    )
                )
            calendar = schedule_check(
                packet,
                manifest,
                source,
                packet_path.split("/")[1] if packet_path else None,
                s,
                tm,
                f[s],
                doc["general"]["leagueId"],
                ref,
                target_kick,
                cut,
            )
            calendars.append(calendar)
            stable = usual_keeper(ks)
            calendar_role.append(
                bool(stable and calendar == "SIX_MATCH_CALENDAR_CONFIRMED")
            )
            role_starters = (
                starting_keeper(ln.get(s + "Team") or {}, tm)
                if fm["kind"] == "PROJECTED_XI"
                else None
            )
            ids = {
                str(x["person_id"])
                for x in items[s]["items"]
                if x["person_id"] is not None and x["role"] != "coach"
            }
            roles.append(bool(stable))
            acts.append(sum(x is not None for x in ks))
            listed.append(len(items[s]["items"]))
            gaps.append(bool(stable and stable in ids))
            lineups.append(bool(stable and role_starters and stable != role_starters))
            calendar_changed.append(
                bool(calendar_role[-1] and (gaps[-1] or lineups[-1]))
            )
        rows.append(
            dict(
                calendar_home=calendars[0],
                calendar_away=calendars[1],
                calendar_confirmed_usual_sides=sum(calendar_role),
                calendar_confirmed_flagged_sides=sum(calendar_changed),
                event=mid,
                home=f["home"],
                away=f["away"],
                competition=co,
                stage=f["stage"],
                in_scope=stage_ok,
                first_freeze=r["record_sha256"],
                cutoff=f["cutoff_at"],
                kickoff=f["kickoff_at"],
                observed_at=fm["received_at"],
                source_sha256=fm["source_sha256"],
                raw_lineup_type=ln.get("lineupType"),
                status=fm["kind"],
                raw_starters_both=fm["both_xi"],
                **clocks,
                absence_items=sum(listed),
                listed_sides=sum(n > 0 for n in listed),
                history_goalkeeper_slots=sum(acts),
                both_histories_six=acts == [6, 6],
                usual_keeper_sides=sum(roles),
                both_usual_keepers=all(roles),
                provider_listed_usual_keeper_sides=sum(gaps),
                projected_different_keeper_sides=sum(lineups),
                official_target_source=False,
            )
        )

    def summary(rs):
        return dict(
            fixtures=len(rs),
            providers=dict(Counter(r["status"] for r in rs)),
            registered_team_slots=12 * len(rs),
            goalkeeper_slots=sum(r["history_goalkeeper_slots"] for r in rs),
            calendar_checks=dict(
                Counter(r["calendar_" + s] for r in rs for s in SIDES)
            ),
            calendar_confirmed_usual_sides=sum(
                r["calendar_confirmed_usual_sides"] for r in rs
            ),
            calendar_confirmed_flagged_sides=sum(
                r["calendar_confirmed_flagged_sides"] for r in rs
            ),
            both_history_verified=sum(r["both_histories_six"] for r in rs),
            usual_keeper_sides=sum(r["usual_keeper_sides"] for r in rs),
            both_usual_keepers=sum(r["both_usual_keepers"] for r in rs),
            provider_listed_usual_keeper_sides=sum(
                r["provider_listed_usual_keeper_sides"] for r in rs
            ),
            fixtures_with_listed_role=sum(
                r["provider_listed_usual_keeper_sides"] > 0 for r in rs
            ),
            fixtures_with_projected_change=sum(
                r["projected_different_keeper_sides"] > 0 for r in rs
            ),
            fixtures_with_both_provider_indications=sum(
                r["provider_listed_usual_keeper_sides"] > 0
                and r["projected_different_keeper_sides"] > 0
                for r in rs
            ),
            projected_different_keeper_sides=sum(
                r["projected_different_keeper_sides"] for r in rs
            ),
            provider_listed_items=sum(r["absence_items"] for r in rs),
            fixtures_with_reported_items=sum(r["absence_items"] > 0 for r in rs),
            first_party_xi_from_supplier=sum(r["official_target_source"] for r in rs),
        )

    out.mkdir(parents=True, exist_ok=True)
    table(out / "slate-role-fixtures.csv", rows)
    table(out / "slate-prior-gk.csv", history_rows)
    answer = {
        "first_party_files_reviewed": dict(
            Counter(r["classification"] for r in reviewed_files)
        ),
        "all_registered": summary(rows),
        "within_regular_scope": summary([r for r in rows if r["in_scope"]]),
        "scope_exclusions": dict(
            Counter(
                r["competition"] + "/" + r["stage"] for r in rows if not r["in_scope"]
            )
        ),
        "by_competition": {
            c: summary([r for r in rows if r["competition"] == c])
            for c in sorted({r["competition"] for r in rows})
        },
        "past_slot_status_in_scope": dict(
            Counter(r["reason"] for r in history_rows if r["in_scope"])
        ),
        "raw_edition_hash": h,
    }
    save(out / "slate-coverage.json", answer)
    print(answer["within_regular_scope"])


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    for key in ("archive", "receipts", "out"):
        p.add_argument("--" + key, type=Path, required=True)
    a = p.parse_args()
    audit(a.archive, a.receipts, a.out)
