"""Offline, read-only personnel evidence for one fixture at one cutoff."""

import hashlib
import html
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

from jleague_cup.quarterfinals import official_match_objects
from research.availability import extract

SIDES = ("home", "away")
CLOCKS = ("original", "T-60", "T-15")
FOTMOB_LEAGUES = {
    "bra.1": 268,
    "swe.1": 67,
    "nor.1": 59,
    "ned.1": 57,
    "eng.1": 47,
    "esp.1": 87,
    "ger.1": 54,
    "ita.1": 55,
    "fra.1": 53,
}


def regular_fotmob_fixture(fixture, native):
    code = fixture["competition"]
    if fixture.get("stage") != "regular" or code not in FOTMOB_LEAGUES:
        return False
    if code == "ned.1":
        return (
            native.get("leagueName") == "Eredivisie"
            and native.get("leagueId") in (57, 937276)
            and native.get("parentLeagueId", native.get("leagueId")) == 57
            and str(native.get("leagueRoundName", "")).isdigit()
        )
    return native.get("leagueId") == FOTMOB_LEAGUES[code]


def utc(value):
    d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if d.utcoffset() is None:
        raise ValueError("A timezone is required")
    return d.astimezone(timezone.utc)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def times(fixture):
    kick = utc(fixture["kickoff_at"])
    original = utc(fixture["cutoff_at"])
    if original >= kick:
        raise ValueError("A first prematch cutoff is required")
    return {
        "original": original,
        "T-60": kick - timedelta(minutes=60),
        "T-15": kick - timedelta(minutes=15),
    }


def matched(f, o):
    return (
        f["match_id"],
        f["competition"],
        utc(f["kickoff_at"]),
        f["home"],
        f["away"],
    ) == (
        o["match_id"],
        o["competition"],
        utc(o["kickoff_at"]),
        o["home"],
        o["away"],
    ) and f["home"] != f["away"]


def admission(f, o, cut):
    if not matched(f, o):
        return "IDENTITY_MISMATCH"
    observed = utc(o["received_at"])
    kickoff = utc(f["kickoff_at"])
    if cut >= kickoff:
        raise ValueError("Cutoff must precede kickoff")
    if o.get("published_at") and utc(o["published_at"]) > observed:
        return "SOURCE_CLOCK_CONFLICT"
    if observed >= kickoff or not o["pregame"]:
        return "LIVE_OR_POSTMATCH"
    if observed > cut:
        return "AFTER_CUTOFF"
    return o["kind"]


def espn(raw, r):
    src = r["source_url"]
    q = urlsplit(src)
    source_match = re.fullmatch(
        r"/apis/site/v2/sports/soccer/([a-z0-9._]+)/summary", q.path
    )
    event = parse_qs(q.query).get("event")
    if (
        q.scheme != "https"
        or q.netloc != "site.api.espn.com"
        or not source_match
        or not event
        or len(event) != 1
    ):
        raise ValueError("Unexpected ESPN fixture URL")
    ref = r["raw"]
    if (
        r.get("status") != "OK"
        or ref["source_url"] != src
        or sha(raw) != ref["sha256"]
        or utc(r["captured_at"]) != utc(ref["captured_at"])
        or utc(r["started_at"]) > utc(r["captured_at"])
    ):
        raise ValueError("ESPN acquisition receipt differs from source bytes")
    d = json.loads(raw)
    h = d["header"]
    (c,) = h["competitions"]
    code = source_match[1]
    eid = event[0]
    if h["league"]["slug"] != code or str(h["id"]) != eid or str(c["id"]) != eid:
        raise ValueError("ESPN source event or competition differs")
    teams = {v["homeAway"]: str(v["team"]["id"]) for v in c["competitors"]}
    if (
        len(c["competitors"]) != 2
        or set(teams) != set(SIDES)
        or teams["home"] == teams["away"]
    ):
        raise ValueError("Ambiguous ESPN sides")
    t = c["status"]["type"]
    pregame = (
        t.get("state") == "pre"
        and t.get("completed") is False
        and t.get("name") == "STATUS_SCHEDULED"
    )
    rosters = d.get("rosters") or []
    distinct = {}
    bad = False
    for v in rosters:
        side = v.get("homeAway")
        if (
            side not in teams
            or side in distinct
            or str((v.get("team") or {}).get("id")) != teams[side]
        ):
            bad = True
            continue
        ids = [
            str((p.get("athlete") or {}).get("id") or "")
            for p in v.get("roster", [])
            if p.get("starter") is True
        ]
        distinct[side] = ids
    both = (
        not bad
        and set(distinct) == set(SIDES)
        and all(
            len(distinct[s]) == len(set(distinct[s])) == 11 and "" not in distinct[s]
            for s in SIDES
        )
    )
    if both and set(distinct["home"]) & set(distinct["away"]):
        both = False
    return dict(
        match_id="espn:" + eid,
        competition=code,
        kickoff_at=c["date"],
        home="espn:" + teams["home"],
        away="espn:" + teams["away"],
        received_at=r["captured_at"],
        published_at=None,
        source_sha256=ref["sha256"],
        pregame=pregame,
        kind="PROVIDER_BOTH_XI" if both else "PROVIDER_WITHOUT_BOTH_XI",
        both_xi=both,
    )


def player_ids(rows):
    ids = [str(v.get("id") or "") for v in rows]
    if len(rows) != 11 or "" in ids or len(set(ids)) != 11:
        return None
    return set(ids)


def fotmob(raw, receipt):
    """Provider reports remain provider reports even with a starter flag or an empty injury table."""
    if sha(raw) != receipt["sha256"]:
        raise ValueError("FotMob source hash differs")
    if not receipt.get("received_at"):
        raise ValueError("No actual acquisition clock")
    d = json.loads(raw)
    g = d["general"]
    ln = (d.get("content") or {}).get("lineup") or {}
    t = urlsplit(receipt["source_url"])
    params = parse_qs(t.query)
    if (
        t.scheme != "https"
        or t.netloc != "www.fotmob.com"
        or t.path not in ("/api/matchDetails", "/api/data/matchDetails")
        or params.get("matchId") != [str(g["matchId"])]
    ):
        raise ValueError("FotMob source identity mismatch")
    if ln.get("matchId") is not None and str(ln["matchId"]) != str(g["matchId"]):
        raise ValueError("FotMob lineup belongs to a different fixture")
    clubs = [str(g[s + "Team"]["id"]) for s in SIDES]
    if len(set(clubs)) != 2:
        raise ValueError("FotMob sides coincide")
    for side, club in zip(SIDES, clubs):
        team = ln.get(side + "Team") or {}
        if team and str(team.get("id")) != club:
            raise ValueError("FotMob lineup side differs from event")
    kinds = [(ln.get(s + "Team") or {}).get("lineupType") for s in SIDES]
    typ = ln.get("lineupType")
    if typ:
        kinds = [v or typ for v in kinds]
    ids = [player_ids((ln.get(s + "Team") or {}).get("starters") or []) for s in SIDES]
    both = bool(all(ids) and not (ids[0] & ids[1]))
    projected = all(v in ("predicted", "projected") for v in kinds)
    kind = (
        "PREVIOUS_XI"
        if typ == "lastStarting11"
        else "PROJECTED_XI"
        if projected and both
        else "PROVIDER_BOTH_XI"
        if both and all(v == "confirmed" for v in kinds)
        else "PROVIDER_WITHOUT_CONFIRMED_BOTH_XI"
    )
    a = extract(d)["sides"]
    return dict(
        match_id="fotmob:" + str(g["matchId"]),
        home="fotmob:" + str(g["homeTeam"]["id"]),
        away="fotmob:" + str(g["awayTeam"]["id"]),
        source_league_id=str(g["leagueId"]),
        kickoff_at=g["matchTimeUTCDate"],
        received_at=receipt["received_at"],
        source_sha256=sha(raw),
        published_at=receipt.get("published_at"),
        pregame=g.get("started") is False and g.get("finished") is False,
        kind=kind,
        both_xi=both,
        availability={
            s: {"status": a[s]["status"], "listed": len(a[s]["items"])} for s in SIDES
        },
    )


def jleague(raw, receipt, fixture):
    """Replay an official 2026 cup QF page, binding the visible XI to its stable player IDs."""
    url = receipt["url"]
    q = urlsplit(url)
    code = fixture["match_id"].removeprefix("jleague:")
    if (
        not all(fixture[k].startswith("jleague:") for k in ("match_id", "home", "away"))
        or fixture["competition"] != "jpn.league_cup"
        or fixture["stage"] != "quarterfinals"
        or fixture.get("leg") not in (1, 2)
    ):
        raise ValueError("Cup quarterfinal stage/leg required")
    if (
        q.scheme != "https"
        or q.netloc != "www.jleague.jp"
        or q.path != "/match/leaguecup/2026/" + code + "/"
        or q.query
        or q.fragment
        or receipt["match_id"] != fixture["match_id"]
    ):
        raise ValueError("Wrong first-party event URL")
    if (
        receipt["http_status"] != 200
        or sha(raw) != receipt["sha256"]
        or utc(receipt["requested_at_utc_complete"])
        < utc(receipt["requested_at_utc_start"])
    ):
        raise ValueError("Invalid original first-party receipt")
    page = raw.decode()
    canonical = re.search(r'<link[^>]*rel="canonical"[^>]*href="([^"]+)"', page)
    if not canonical or html.unescape(canonical[1]) != url:
        raise ValueError("Canonical match URL differs")
    expected = [fixture[s].removeprefix("jleague:") for s in SIDES]
    matches = official_match_objects(page)
    identities = {
        (
            m["date"],
            m["homeTeam"].get("code"),
            m["awayTeam"].get("code"),
            m.get("type"),
            m.get("section"),
        )
        for m in matches
        if isinstance(m["homeTeam"], dict) and isinstance(m["awayTeam"], dict)
    }
    if len(identities) != 1:
        raise ValueError("Conflicting official identity")
    date, h, a, state, section = identities.pop()
    if (
        not date.startswith("$D")
        or utc(date[2:]) != utc(fixture["kickoff_at"])
        or [h, a] != expected
        or h == a
        or re.sub(r"\s+", "", section or "") != f"準々決勝第{fixture['leg']}戦"
    ):
        raise ValueError("Official date, stage or ordered clubs differ")
    if state != "pre-game":
        raise ValueError("Page was not in its prematch state")
    # The visible two-column starting widget alternates home and away; exclude scripts containing localization dictionaries or other pages.
    visible = html.unescape(
        re.sub(
            r"<[^>]*>",
            " ",
            re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", page, flags=re.S | re.I),
        )
    )
    visible = " ".join(visible.split())
    head = visible.find("スターティングメンバー発表")
    start = visible.find("GK", head) if head >= 0 else -1
    end = visible.find("フォーメーション", start) if start >= 0 else -1
    if min(start, end) < 0:
        raise ValueError("Missing visible confirmed lineup")
    players = re.findall(
        r"(GK|DF|MF|FW)\s+(\d+)\s+(.*?)(?=\s+(?:GK|DF|MF|FW)\s+\d+|$)",
        visible[start:end].strip(),
    )
    if len(players) != 22:
        raise ValueError("Official visible widget lacks 22 starters")
    visible_sides = [
        [(pos, num, re.sub(r"\s+", "", name)) for pos, num, name in players[n::2]]
        for n in (0, 1)
    ]
    # The native roster serializations contain side-specific club codes, names and stable player IDs.
    chunks = re.findall(r'self\.__next_f\.push\(\[1,("(?:\\.|[^"\\])*")\]\)', page)
    seen = [set(), set()]
    for line in "".join(json.loads(s) for s in chunks).splitlines():
        try:
            stack = [json.loads(line.partition(":")[2])]
        except ValueError:
            continue
        while stack:
            value = stack.pop()
            if isinstance(value, list):
                stack.extend(v for v in value if isinstance(v, (dict, list)))
                continue
            if not isinstance(value, dict):
                continue
            stack.extend(v for v in value.values() if isinstance(v, (dict, list)))
            for n, side in enumerate(SIDES):
                team = value.get(side + "Team")
                if not isinstance(team, dict):
                    continue
                rows = team.get("players") or []
                if len(rows) != 11 or not all(
                    isinstance(p.get("legacyPlayerPhotoLookup"), dict) for p in rows
                ):
                    continue
                seq = []
                role_ids = []
                jerseys = set()
                for p in rows:
                    m = re.fullmatch(r"(GK|DF|MF|FW)\s+(\d+)", p.get("position", ""))
                    proof = p["legacyPlayerPhotoLookup"]
                    if not m or proof.get("teamNameKey") != expected[n]:
                        break
                    seq.append((m[1], m[2], re.sub(r"\s+", "", p["name"])))
                    role_ids.append((m[1], str(proof.get("playerId") or "")))
                    jerseys.add(m[2])
                if seq == visible_sides[n]:
                    if (
                        len(jerseys) != 11
                        or len({pid for _, pid in role_ids if pid}) != 11
                        or sum(pos == "GK" for pos, _ in role_ids) != 1
                    ):
                        raise ValueError(
                            "Ambiguous official lineup identities or positions"
                        )
                    seen[n].add(tuple(role_ids))
    if any(len(s) != 1 for s in seen):
        raise ValueError("Native stable players and visible XI differ")
    hs, as_ = next(iter(seen[0])), next(iter(seen[1]))
    if {i for _, i in hs} & {i for _, i in as_}:
        raise ValueError("Player ID on both teams")
    return {
        k: fixture[k] for k in ("competition", "home", "away", "match_id", "kickoff_at")
    } | dict(
        received_at=receipt["requested_at_utc_complete"],
        published_at=receipt.get("published_at"),
        source_sha256=sha(raw),
        pregame=True,
        kind="OFFICIAL_BOTH_XI",
        both_xi=True,
        keeper_ids=[next(i for pos, i in team if pos == "GK") for team in (hs, as_)],
    )


def preview(provider, fixture, receipt, raw):
    """Check a saved acquisition against each cutoff; never create an issue record."""
    if provider == "espn":
        o = espn(raw, receipt)
    elif provider == "jleague":
        o = jleague(raw, receipt, fixture)
    elif provider == "fotmob":
        o = fotmob(raw, receipt)
        if not regular_fotmob_fixture(fixture, json.loads(raw)["general"]):
            raise ValueError("Unverified FotMob competition or phase")
        o["competition"] = fixture["competition"]
    else:
        raise ValueError("Unknown evidence adapter")
    now = datetime.now(timezone.utc)
    if utc(o["received_at"]) > now:
        raise ValueError("Receipt time is in the future")
    return dict(
        match_id=fixture["match_id"],
        source_kind=provider,
        source_sha256=sha(raw),
        received_at=o["received_at"],
        published_at=o.get("published_at"),
        comparison={
            k: {
                "clock": c.isoformat(),
                "reached": c <= now,
                "status": admission(fixture, o, min(c, now)),
            }
            for k, c in times(fixture).items()
        },
        official_keeper_ids_available=len(o.get("keeper_ids", [])) == 2,
        provider_availability_counts=o.get("availability"),
        prediction_issued=False,
    )


if __name__ == "__main__":
    import argparse
    from pathlib import Path

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("provider", choices=("jleague", "espn", "fotmob"))
    for key in ("fixture", "receipt", "raw"):
        p.add_argument("--" + key, required=True, type=Path)
    p.add_argument(
        "--receipt-sha256",
        required=True,
        help="Digest of the acquisition receipt from its original manifest",
    )
    a = p.parse_args()
    meta = a.receipt.read_bytes()
    if sha(meta) != a.receipt_sha256:
        raise SystemExit("Capture receipt does not match supplied archival reference")
    fixture = json.loads(a.fixture.read_bytes())
    receipt = json.loads(meta)
    if a.provider == "jleague" and "sources" in receipt:
        (receipt,) = (
            r for r in receipt["sources"] if r["match_id"] == fixture["match_id"]
        )
    print(
        json.dumps(
            preview(a.provider, fixture, receipt, a.raw.read_bytes()),
            indent=2,
            ensure_ascii=False,
        )
    )
