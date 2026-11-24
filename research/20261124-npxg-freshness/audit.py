"""Read-only audit of exact prior-six native non-penalty xG for locally archived fixtures."""

import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import os
import statistics
import subprocess
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
SLUG = {
    "eng.1": "EPL",
    "esp.1": "La_liga",
    "ger.1": "Bundesliga",
    "ita.1": "Serie_A",
    "fra.1": "Ligue_1",
}
DIV = {"eng.1": "E0", "esp.1": "SP1", "ger.1": "D1", "ita.1": "I1", "fra.1": "F1"}
DATA = "a6d45024cf6cf622f05e4b378ab53cfe2d6ae329"
BACKUP = "466fd626a47481e70b176a9a69c58224d7d1bf63"
ANCHOR = "2026-11-02T03:09:28Z"
OLD = "WCPP/data/staging/systematic-20261015/understat"
FD = "workspace-updates/20261118-transformer/transfer/raw/"
PUBLIC_SCHEDULE = "source-audit/calendar/receipt.json"


def utc(v):
    d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    if d.utcoffset() is None:
        raise ValueError("Time zone required for receipt")
    return d.astimezone(timezone.utc)


def native_time(v):
    return datetime.fromisoformat(v).replace(tzinfo=timezone.utc)


def sha(data):
    return hashlib.sha256(data).hexdigest()


@lru_cache(maxsize=16)
def read_git(repo, rev, path):
    return subprocess.check_output(["git", "-C", str(repo), "show", rev + ":" + path])


def jread(path):
    return json.loads(Path(path).read_bytes())


def dump(path, o):
    path.write_text(
        json.dumps(o, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        + "\n"
    )


def csvout(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=list(dict.fromkeys(k for r in rows for k in r)),
            lineterminator="\n",
        )
        w.writeheader()
        w.writerows(rows)


def fraction(s):
    return (
        isinstance(s, (float, int))
        and not isinstance(s, bool)
        and math.isfinite(s)
        and s >= 0
    )


def key(s):
    return "".join(
        v for v in unicodedata.normalize("NFKD", s.casefold()) if v.isalnum()
    )


def valid_pair(h, a):
    ns = [h.get("npxG"), a.get("npxG"), a.get("npxGA"), h.get("npxGA")]
    if not all(fraction(v) for v in ns):
        return False
    return all(abs(x - y) <= 0.0002 for x, y in zip(ns[:2], ns[2:])) and all(
        fraction(v.get("xG")) and v["npxG"] <= v["xG"] + 0.0002 for v in (h, a)
    )


def unpack_native(raw, competition, observed, available, blob):
    p = json.loads(raw)
    teams = p["teams"]
    games = p["dates"]
    hist = {
        (str(t["id"]), s["h_a"], s["date"]): s
        for t in teams.values()
        for s in t["history"]
    }
    ids = {str(t["id"]): t["title"] for t in teams.values()}
    pairs = {}
    completed = []
    seen = set()
    if len(hist) != sum(len(t["history"]) for t in teams.values()):
        raise ValueError("Ambiguous team-history row")
    for g in games:
        mid = str(g["id"])
        h = str(g["h"]["id"])
        a = str(g["a"]["id"])
        ts = g["datetime"]
        if (
            h == a
            or (h, a) in pairs
            or mid in seen
            or ids[h] != g["h"]["title"]
            or ids[a] != g["a"]["title"]
        ):
            raise ValueError("Native identity conflict")
        seen.add(mid)
        pairs[h, a] = dict(
            id=mid,
            home=h,
            away=a,
            nominal=ts,
            day=ts[:10],
            completed=g.get("isResult") is True,
        )
        if g.get("isResult") is False:
            continue
        if (
            g.get("isResult") is not True
            or native_time(ts) + timedelta(minutes=90) > observed
        ):
            raise ValueError("Completed match ahead of source clock")
        home = hist[h, "h", ts]
        away = hist[a, "a", ts]
        pair_ok = valid_pair(home, away)
        if [home["scored"], away["scored"]] != [away["missed"], home["missed"]] or [
            home["scored"],
            away["scored"],
        ] != [int(g["goals"]["h"]), int(g["goals"]["a"])]:
            raise ValueError("Native home/away or goals conflict")
        if (
            not all(fraction(v) for v in (home["xG"], away["xG"]))
            or abs(home["xG"] - away["xGA"]) > 0.0002
            or abs(away["xG"] - home["xGA"]) > 0.0002
        ):
            raise ValueError("Native xG identity conflict")
        completed.append(
            dict(
                id=mid,
                home_id=h,
                away_id=a,
                date=ts[:10],
                scheduled=ts,
                goals=[home["scored"], away["scored"]],
                xg=[home["xG"], away["xG"]],
                npx=[home["npxG"], away["npxG"]] if pair_ok else [None, None],
                raw_sha=blob,
                observed_at=observed.isoformat(),
                known_by=available.isoformat(),
            )
        )
    if len(pairs) != len(ids) * (len(ids) - 1):
        raise ValueError("Incomplete home and away league grid")
    return dict(
        competition=competition,
        team_names=ids,
        pairs=pairs,
        completed=sorted(completed, key=lambda g: (g["date"], g["id"])),
        observed=observed,
        known_by=available,
        sha=blob,
    )


def original_seasons(args):
    manifest = jread(args.prior / "inputs/input-manifest.json")
    protocol = jread(ROOT / "protocol.json")
    idx = read_git(args.data, BACKUP, "workspace-backup-20261102/files.json")
    if (
        sha(idx) != manifest["index_sha256"]
        or sha((args.prior / "inputs/input-manifest.json").read_bytes())
        != protocol["hashes"]["old_input_manifest"]
    ):
        raise ValueError("Backup or study input mismatch")
    original = {
        r["path"]: r["sha256"]
        for r in json.loads(idx)
        if r["path"].startswith(OLD + "/")
    }
    audit = (args.restored / OLD / "source-audit.json").read_bytes()
    if (
        sha(audit) != original[OLD + "/source-audit.json"]
        or sha(audit) != manifest["capture_audit_sha256"]
    ):
        raise ValueError("Unbound capture")
    captures = {
        (v["league"], int(v["season_start_year"])): v
        for v in json.loads(audit)["requests"]
    }
    seasons = {}
    for f in manifest["files"]:
        code = f["competition"]
        year = f["season"]
        wire = (args.restored / f["path"]).read_bytes()
        raw = gzip.decompress(wire)
        q = captures[SLUG[code], year]
        w = sha(wire)
        b = sha(raw)
        if not (
            w == original[f["path"]] == q["wire_sha256"] == f["wire_sha256"]
            and b == q["decoded_sha256"] == f["decoded_sha256"]
            and q["http_status"] == 200
            and utc(q["fetched_at_beijing"]) == utc(f["capture_at"])
        ):
            raise ValueError("Original source proof mismatch")
        seasons[code, year] = unpack_native(
            raw, code, utc(f["capture_at"]), utc(ANCHOR), b
        )
    return seasons


def additional_native(args):
    # Three later originals were retained decoded. Each original and its contemporaneous acquisition manifest are bound by the same pre-November-evaluation Git backup.
    anchor = "WCPP/reports/jingcai-20261019-2135/facts-major"
    prefix = anchor + "/"
    original = {
        r["path"]: r["sha256"]
        for r in json.loads(
            read_git(args.data, BACKUP, "workspace-backup-20261102/files.json")
        )
        if r["path"].startswith(prefix)
    }
    manifest_key = prefix + "understat_manifest.json"
    raw = (args.restored / manifest_key).read_bytes()
    if sha(raw) != original[manifest_key]:
        raise ValueError("Later native capture manifest differs")
    by_label = {"EPL": "eng.1", "Serie A": "ita.1", "La liga": "esp.1"}
    out = []
    for r in json.loads(raw):
        code = by_label[r["league"]]
        path = prefix + r["path"]
        blob = (args.restored / path).read_bytes()
        if (
            r["season"] != "2026/27"
            or r["http_status"] != 200
            or not (sha(blob) == r["decoded_sha256"] == original[path])
        ):
            raise ValueError("Later native bytes differ")
        out.append(
            unpack_native(blob, code, utc(r["captured_at_utc"]), utc(ANCHOR), sha(blob))
        )
    return out


def observations_by_fixture(snapshots):
    out = defaultdict(list)
    for v in snapshots:
        for row in v["completed"]:
            out[row["id"]].append(row)
    return out


def eligible_snapshot(snapshots, cutoff, strict):
    eligible = [
        s
        for s in snapshots
        if s["observed"] <= cutoff and (not strict or s["known_by"] <= cutoff)
    ]
    return max(eligible, key=lambda s: s["observed"]) if eligible else None


def reference_calendar(args):
    metadata = json.loads(read_git(args.data, DATA, FD + "source-metadata.json"))
    source = {m["file"]: m for m in metadata}
    live = jread(args.calendar)
    data = {}
    evidence = []
    for code, div in DIV.items():
        filename = "2627_" + div + ".csv"
        entry = source[filename]
        raw = read_git(args.data, DATA, FD + filename)
        if sha(raw) != entry["sha256"]:
            raise ValueError("Football-Data calendar bytes differ")
        live_receipts = [
            v
            for v in live["receipts"]
            if v.get("competition") == div and v.get("status") == 200
        ]
        new = [v for v in live["schedule"] if v["competition"] == div]
        if len(live_receipts) != 1 or not new:
            raise ValueError("No current complete date panel")
        rec = live_receipts[0]
        if (
            rec.get("schedule_sha256")
            and sha(
                json.dumps(
                    new, sort_keys=True, ensure_ascii=False, separators=(",", ":")
                ).encode()
            )
            != rec["schedule_sha256"]
        ):
            raise ValueError("Projected independent calendar differs from receipt")
        games = {}
        for r in csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))):
            if r["Div"] != div or r["FTR"] not in ("H", "D", "A"):
                raise ValueError("Non-league or unresolved entry in FT calendar")
            k = (r["HomeTeam"], r["AwayTeam"])
            ko = (
                datetime.strptime(r["Date"] + " " + r["Time"], "%d/%m/%Y %H:%M")
                .replace(tzinfo=ZoneInfo("Europe/London"))
                .astimezone(timezone.utc)
            )
            if k in games:
                raise ValueError("Repeat league pair in season")
            games[k] = {
                "day": ko.date().isoformat(),
                "kickoff": ko,
                "finished": True,
                "source": entry["sha256"],
                "observed_calendar": entry["captured_at"],
            }
        old = list(games)
        seen = set()
        for r in new:
            k = r["home"], r["away"]
            d = datetime.strptime(r["date_raw"], "%d/%m/%Y").date().isoformat()
            if k in seen:
                raise ValueError("Repeated pairing on independent calendar")
            seen.add(k)
            if k in games:
                if d != games[k]["day"]:
                    raise ValueError("Calendar date changed between captures")
            else:
                games[k] = {
                    "day": d,
                    "kickoff": None,
                    "finished": False,
                    "source": live_receipts[0]["sha256"],
                    "observed_calendar": live_receipts[0]["received_at"],
                }
        if not set(old) <= seen:
            raise ValueError("Original dated fixture missing from later edition")
        data[code] = games
        evidence.append(
            dict(
                competition=code,
                div=div,
                old_ft=len(old),
                new_identity_only=len(games) - len(old),
                calendar_rows=len(games),
                ft_capture=entry["captured_at"],
                ft_sha=entry["sha256"],
                current_capture=live_receipts[0]["received_at"],
                current_sha=live_receipts[0]["sha256"],
            )
        )
    return data, evidence


def club_map(source, calendar):
    # Ascertain abbreviations using distinct, already identified opponents on multiple exact match days. Scores are never consulted.
    clubs = source["team_names"]
    aliases = list({t for pair in calendar for t in pair})
    names = defaultdict(set)
    for tid, name in clubs.items():
        names[key(name)].add(tid)
    mapping = {n: next(iter(names[key(n)])) for n in aliases if len(names[key(n)]) == 1}
    basis = {n: "unique source name" for n in mapping}
    native = source["completed"]
    for _ in range(len(clubs)):
        changes = {}
        for name in sorted(set(aliases) - set(mapping)):
            votes = defaultdict(set)
            for (home, away), g in calendar.items():
                if name not in (home, away):
                    continue
                home_side = home == name
                other = mapping.get(away if home_side else home)
                if not other:
                    continue
                for v in native:
                    if (
                        v["date"] == g["day"]
                        and v["away_id" if home_side else "home_id"] == other
                    ):
                        votes[v["home_id" if home_side else "away_id"]].add(
                            (other, v["id"])
                        )
            if len(votes) == 1:
                tid, matches = next(iter(votes.items()))
                if len({o for o, _ in matches}) >= 2:
                    changes[name] = (tid, ",".join(sorted(m for _, m in matches)))
        if not changes:
            break
        for n, (i, evidence) in changes.items():
            mapping[n] = i
            basis[n] = "ordered opponents and native game IDs " + evidence
    if len(set(mapping.values())) != len(mapping):
        raise ValueError("Ambiguous provider club crosswalk")
    return mapping, basis


def linked_histories(calendar, mapping, source):
    rows = []
    for (h, a), c in calendar.items():
        hn, an = mapping.get(h), mapping.get(a)
        pair = source["pairs"].get((hn, an))
        rows.append(
            dict(
                **c,
                home_name=h,
                away_name=a,
                home=hn,
                away=an,
                id=pair["id"] if pair else None,
            )
        )
    return rows


def previous_six(rows, team, cutoff, target_id):
    # Date-only new rows are explicitly unresolved. Never choose a seventh row to replace one.
    day = cutoff.date().isoformat()
    scoped = []
    for g in rows:
        if g["id"] == target_id or team not in (g["home"], g["away"]):
            continue
        if g["day"] > day:
            continue
        if g["day"] == day and (
            not g["kickoff"] or g["kickoff"] + timedelta(minutes=90) > cutoff
        ):
            if not g["kickoff"]:
                return [], "AMBIGUOUS_SAME_DAY"
            continue
        if g["kickoff"] and g["kickoff"] + timedelta(minutes=90) > cutoff:
            continue
        scoped.append(g)
    scoped.sort(key=lambda g: (g["day"], g["id"] or ""), reverse=True)
    six = scoped[:6]
    if len({g["day"] for g in six}) != len(six):
        return six, "AMBIGUOUS_ORDER"
    if any(not g["finished"] or not g["id"] for g in six):
        return six, "UNRESOLVED_FIXTURE_ID_OR_STATUS"
    return six, "OK" if len(six) == 6 else "SEASON_HAS_FEWER_THAN_SIX"


def matching(observations, g, tid, cut, strict):
    avail = []
    for ob in observations.get(g["id"], []):
        if (ob["home_id"], ob["away_id"], ob["date"]) != (
            g["home"],
            g["away"],
            g["day"],
        ) or any(v is None for v in ob["npx"]):
            continue
        upper = (
            max(utc(ob["observed_at"]), utc(ob["known_by"]))
            if strict
            else utc(ob["observed_at"])
        )
        if upper <= cut:
            avail.append((upper, ob))
    if not avail:
        return None
    latest = max((t, utc(o["observed_at"])) for t, o in avail)
    opts = [o for t, o in avail if (t, utc(o["observed_at"])) == latest]
    if any(o["npx"] != opts[0]["npx"] for o in opts):
        return None
    return opts[0]


def side_coverage(previous, tid, cut, mid, observations, strict):
    wanted, calendar_status = previous_six(previous, tid, cut, mid)
    slots = []
    for pos, g in enumerate(wanted, 1):
        ob = (
            matching(observations, g, tid, cut, strict)
            if g["finished"] and g["id"]
            else None
        )
        slots.append(
            dict(
                rank=pos,
                id=g["id"],
                day=g["day"],
                team=tid,
                opponent=(g["away"] if g["home"] == tid else g["home"]),
                covered=ob is not None,
                raw_sha256=ob["raw_sha"] if ob else None,
                known_by=ob["known_by"] if ob else None,
                observed_at=ob["observed_at"] if ob else None,
            )
        )
    hits = sum(x["covered"] for x in slots)
    latest = max((x["day"] for x in slots if x["covered"]), default=None)
    return dict(
        team=tid,
        slots=slots,
        observed_slots=hits,
        calendar_status=calendar_status,
        complete=(hits == 6 and calendar_status == "OK"),
        latest_covered=latest,
        actual_latest=wanted[0]["day"] if wanted else None,
    )


def scan(args):
    seasons = original_seasons(args)
    calendar, cals = reference_calendar(args)
    p = jread(ROOT / "protocol.json")
    rr = (args.prior / "grade/roster.json").read_bytes()
    bb = (args.prior / "inputs/bridges.csv").read_bytes()
    if sha(rr) != p["hashes"]["old_roster"] or sha(bb) != p["hashes"]["old_bridges"]:
        raise ValueError("Original cohort changed")
    if (
        sha(
            read_git(
                args.data,
                DATA,
                "workspace-updates/20261123-prematch-literature/first-issues.tar.gz",
            )
        )
        != p["hashes"]["r7"]
    ):
        raise ValueError("Protected inventory digest differs")
    extra = additional_native(args)
    roster = json.loads(rr)
    bridges = {
        r["understat_match_id"]: r for r in csv.DictReader(io.StringIO(bb.decode()))
    }
    cross = []
    windows = []
    deferrals = []

    for code in SLUG:
        source = seasons[code, 2026]
        m, proof = club_map(source, calendar[code])
        rs = linked_histories(calendar[code], m, source)
        actual = {g["id"]: g for g in rs if g["id"]}
        gidx = {g["id"]: g for g in source["pairs"].values()}
        versions = [source] + [v for v in extra if v["competition"] == code]
        obs = observations_by_fixture(versions)
        for v in versions:
            if v["team_names"] != source["team_names"] or any(
                v["pairs"][k]["id"] != g["id"] for k, g in source["pairs"].items()
            ):
                raise ValueError("Source edition team or fixture identity changed")
        cross.extend(
            dict(
                competition=code,
                calendar_name=n,
                understat_id=i,
                understat_name=source["team_names"][i],
                proof=proof[n],
            )
            for n, i in sorted(m.items())
        )
        candidates = []
        for r in roster:
            if r["cohort"] != code:
                continue
            if r["status"] != "SCORED":
                deferrals.append(
                    dict(
                        window="november",
                        competition=code,
                        id=r["id"],
                        home=r["home"],
                        away=r["away"],
                        status=r["status"],
                    )
                )
                continue
            b = bridges[r["id"]]
            g = gidx[r["id"]]
            cal = actual.get(r["id"])
            kick = utc(r["actual_kickoff"])
            if (
                (b["understat_home_id"], b["understat_away_id"])
                != (g["home"], g["away"])
                or b["espn_event_id"] != r["espn_id"]
                or not cal
                or cal["day"] != kick.date().isoformat()
                or (cal["kickoff"] and cal["kickoff"] != kick)
            ):
                raise ValueError(
                    "No common original target identity, date or kickoff: "
                    + str(r["id"])
                )
            candidates.append(("november", g, kick, r["espn_id"], True))
        start = source["observed"]
        end = start + timedelta(days=7)
        oct_nom = {
            g["id"]
            for g in source["pairs"].values()
            if not g["completed"]
            and start <= native_time(g["nominal"]) - timedelta(minutes=60) < end
        }
        oct_actual = {
            g["id"]
            for g in rs
            if g["id"]
            and g["finished"]
            and start <= g["kickoff"] - timedelta(minutes=60) < end
        }
        for mid in sorted(oct_actual | oct_nom):
            g = gidx[mid]
            cal = actual.get(mid)
            if not cal or not cal["finished"] or mid not in oct_actual:
                deferrals.append(
                    dict(
                        window="october",
                        competition=code,
                        id=mid,
                        home=source["team_names"][g["home"]],
                        away=source["team_names"][g["away"]],
                        status="MISSING_OR_ACTUAL_OUTSIDE_SEVEN_DAYS",
                    )
                )
                continue
            if g["completed"]:
                raise ValueError("Target included in source history")
            candidates.append(("october", g, cal["kickoff"], None, False))
        for window, g, kick, espn, strict in candidates:
            cut = kick - timedelta(minutes=60)
            sd = [
                side_coverage(rs, g[role], cut, g["id"], obs, strict)
                for role in ("home", "away")
            ]
            current = eligible_snapshot(versions, cut, strict)
            if current is None:
                raise ValueError("No eligible source edition")
            latest = []
            old_latest = []
            for role in ("home", "away"):
                tid = g[role]
                for ver, dest in ((source, old_latest), (current, latest)):
                    prev = [
                        v["date"]
                        for v in ver["completed"]
                        if tid in (v["home_id"], v["away_id"])
                        and all(n is not None for n in v["npx"])
                    ]
                    dest.append(max(prev, default=None))
            windows.append(
                dict(
                    window=window,
                    competition=code,
                    id=g["id"],
                    espn=espn,
                    home_id=g["home"],
                    away_id=g["away"],
                    home=source["team_names"][g["home"]],
                    away=source["team_names"][g["away"]],
                    kickoff=kick.isoformat(),
                    cutoff=cut.isoformat(),
                    native_capture=current["observed"].isoformat(),
                    native_proven_bound=current["known_by"].isoformat(),
                    native_sha=current["sha"],
                    older_native_latest=old_latest,
                    latest_native=latest,
                    strict_fixture_inputs_bound=current["known_by"] <= cut,
                    sides=sd,
                    eligible=all(x["complete"] for x in sd),
                    fixture_proof=actual[g["id"]]["source"],
                )
            )
    return seasons, windows, deferrals, cross, cals


def summarize(items):
    if not items:
        return None
    sides = [(r, s) for r in items for s in r["sides"]]
    pos = [slot for _, s in sides for slot in s["slots"]]
    hist = Counter(s["observed_slots"] for _, s in sides)

    def age(row, day):
        return (utc(row["kickoff"]).date() - datetime.fromisoformat(day).date()).days

    prev = [age(r, d) for r in items for d in r["older_native_latest"] if d]
    new = [age(r, d) for r in items for d in r["latest_native"] if d]
    latest = [age(r, s["actual_latest"]) for r, s in sides if s["actual_latest"]]
    return dict(
        fixtures=len(items),
        teams=len(sides),
        six_slots_capacity=len(sides) * 6,
        actual_prior_slots=len(pos),
        scored_slots=sum(x["covered"] for x in pos),
        complete_sides=sum(s["complete"] for _, s in sides),
        complete_fixtures=sum(r["eligible"] for r in items),
        histogram={i: hist[i] for i in range(7)},
        oct15_only_native_days=statistics.median(prev) if prev else None,
        latest_native_days=statistics.median(new) if new else None,
        latest_actual_game_days=statistics.median(latest) if latest else None,
        distinct_prior_games=len(
            {(r["competition"], v["id"]) for r, s in sides for v in s["slots"]}
        ),
        distinct_covered_games=len(
            {
                (r["competition"], v["id"])
                for r, s in sides
                for v in s["slots"]
                if v["covered"]
            }
        ),
        source_bound_before_cutoff=sum(r["strict_fixture_inputs_bound"] for r in items),
    )


def run(args):
    _, windows, deferrals, clubs, cal = scan(args)
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    dump(out / "windows.json", windows)
    csvout(out / "identity.csv", clubs)
    csvout(out / "deferred-fixtures.csv", deferrals)
    dump(out / "calendars.json", cal)
    csvout(
        out / "slot-coverage.csv",
        [
            dict(
                window=r["window"],
                competition=r["competition"],
                understat_target=r["id"],
                kickoff=r["kickoff"],
                cutoff=r["cutoff"],
                target_side=role,
                **slot,
            )
            for r in windows
            for role, s in zip(("home", "away"), r["sides"])
            for slot in s["slots"]
        ],
    )
    summary = {
        group: summarize([r for r in windows if r["window"] == group])
        for group in ("october", "november")
    }
    summary["by_league"] = {
        c: {
            group: summarize(
                [r for r in windows if r["window"] == group and r["competition"] == c]
            )
            for group in ("october", "november")
        }
        for c in SLUG
    }
    summary["excluded"] = dict(
        Counter((r["window"] + "/" + r["status"]) for r in deferrals)
    )
    dump(out / "coverage.json", summary)
    print(
        json.dumps({g: summary[g] for g in ("october", "november")}, ensure_ascii=False)
    )


def args_for(parser):
    parser.add_argument(
        "--data",
        type=Path,
        default=Path(os.environ.get("FOOTBALL_DATA_ARCHIVE", "inputs/data-repository")),
    )
    parser.add_argument(
        "--restored",
        type=Path,
        default=Path(os.environ.get("FOOTBALL_RESTORED_ARCHIVE", "inputs/restored")),
    )
    parser.add_argument(
        "--prior",
        type=Path,
        default=Path(os.environ.get("FOOTBALL_PRIOR_STUDY", "inputs/prior-study")),
    )
    parser.add_argument("--calendar", type=Path, default=ROOT / PUBLIC_SCHEDULE)
    parser.add_argument("--out", type=Path, default=ROOT / "output")
    return parser


if __name__ == "__main__":
    run(args_for(argparse.ArgumentParser(description=__doc__)).parse_args())
