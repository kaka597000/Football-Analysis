"""Kickoff gaps for the same club, using only completed official fixtures in a retained team calendar."""

import re
from datetime import timedelta

from archives import utc

LEAGUES = {
    "eng.1": (47, {"Premier League"}),
    "esp.1": (87, {"LaLiga", "La Liga"}),
    "ger.1": (54, {"Bundesliga"}),
    "ita.1": (55, {"Serie A"}),
    "fra.1": (53, {"Ligue 1"}),
    "ned.1": (57, {"Eredivisie"}),
    "nor.1": (59, {"Eliteserien"}),
    "swe.1": (67, {"Allsvenskan"}),
    "bra.1": (268, {"Série A", "Serie A", "Brasileirão Serie A"}),
    "usa.1": (130, {"Major League Soccer"}),
}
CUPS = {
    42: {"Champions League"},
    73: {"Europa League"},
    10216: {"Conference League"},
    133: {"EFL Cup", "Carabao Cup"},
    132: {"FA Cup"},
    138: {"Copa del Rey"},
    209: {"DFB Pokal"},
    185: {"Coppa Italia"},
    134: {"Coupe de France"},
    125: {"KNVB Cup", "KNVB Beker"},
    45: {"Copa Libertadores"},
    299: {"Copa Sudamericana"},
    9067: {"Copa do Brasil"},
}
FINAL = {"FT", "AET", "Pen"}


class EvidenceGap(ValueError):
    """The supplied archive cannot prove the input for this team or fixture."""


def native_league(code, league_id, name):
    if code not in LEAGUES:
        return False
    expected, names = LEAGUES[code]
    return (
        league_id == expected or code == "ned.1" and league_id == 937276
    ) and name in names


def target_detail(packet, fixture, fm_id, cut):
    eid = fixture["match_id"]
    kick = utc(fixture["kickoff_at"])
    if (
        cut >= kick
        or fixture.get("age") != "senior"
        or fixture.get("gender") != "men"
        or fixture.get("stage") != "regular"
        or fixture.get("score_basis") != "regulation_90"
    ):
        raise EvidenceGap("TARGET_SCOPE_OR_CUTOFF")
    if (
        fixture.get("identity_verified") is not True
        or any(not fixture[s].startswith("espn:") for s in ("home", "away"))
        or fixture["home"] == fixture["away"]
    ):
        raise EvidenceGap("UNBOUND_TARGET_TEAMS")
    if not eid.startswith("espn:"):
        raise EvidenceGap("UNBOUND_TARGET_ESPN_ID")

    def resolved(refs, parser):
        for observed, h in refs:
            value = parser(packet.json(h))
            if value:
                return value, h
        raise EvidenceGap("NO_MATCHING_PREMATCH_IDENTITY_BYTES")

    def verify_espn(doc):
        if "header" in doc:
            head = doc["header"]
            leagues = {head.get("league", {}).get("slug")}
        else:
            events = [r for r in doc.get("events", []) if str(r["id"]) == eid[5:]]
            if len(events) != 1:
                return None
            head = events[0]
            leagues = {r.get("slug") for r in doc.get("leagues", [])}
        if (
            fixture["competition"] not in leagues
            or str(head.get("id")) != eid[5:]
            or len(head.get("competitions", [])) != 1
        ):
            return None
        game = head["competitions"][0]
        sides = {
            r["homeAway"]: "espn:" + str(r["team"]["id"]) for r in game["competitors"]
        }
        state = game["status"]["type"]
        if (
            fixture["competition"] == "usa.1"
            and (
                head.get("season", {}).get("slug") or game.get("season", {}).get("slug")
            )
            != "regular-season"
        ):
            return None
        return (
            len(game["competitors"]) == 2
            and str(game["id"]) == eid[5:]
            and sides == {s: fixture[s] for s in ("home", "away")}
            and utc(game["date"]) == kick
            and state.get("state") == "pre"
            and state.get("completed") is False
        )

    def verify_fm(doc):
        g = doc.get("general") or {}
        if (
            str(g.get("matchId")) != fm_id
            or not g.get("matchTimeUTCDate")
            or utc(g["matchTimeUTCDate"]) != kick
            or g.get("started") is not False
            or g.get("finished") is not False
            or not (
                native_league(
                    fixture["competition"], g.get("leagueId"), g.get("leagueName")
                )
                or fixture["competition"] == "usa.1"
                and g.get("parentLeagueId") == 130
                and g.get("leagueId") in (10000001, 10000002)
                and g.get("leagueName") == "Major League Soccer"
            )
        ):
            return None
        if (
            fixture["competition"] == "ned.1"
            and g.get("parentLeagueId", g["leagueId"]) != 57
        ):
            return None
        sides = [str(g[s + "Team"]["id"]) for s in ("home", "away")]
        bridge = fixture.get("recent_league_form") or {}
        if bridge and any(
            str(bridge[s]["fotmob_team_id"]) != sides[i]
            or bridge[s]["team_id"] != fixture[s]
            for i, s in enumerate(("home", "away"))
        ):
            return None
        return sides if len(set(sides)) == 2 else None

    _, esp_hash = resolved(packet.available("espn", eid[5:], cut), verify_espn)
    sides, fm_hash = resolved(packet.available("game", fm_id, cut), verify_fm)
    return sides, [esp_hash, fm_hash]


def prior_stage(packet, game, team, cut, code):
    tour = game["tournament"]
    name = tour["name"]
    lid = tour["leagueId"]
    labels = [str(game.get(s, {}).get("name", "")) for s in ("home", "away")]
    if any(
        re.search(r"\b(?:U-?(?:17|18|19|20|21|23)|Youth|Reserves|Women)\b", n, re.I)
        for n in labels
    ):
        return "excluded", "non-first-team", None, None
    if native_league(code, lid, name):
        return "league", "regular", None, None
    if (
        lid == 489
        and name == "Club Friendlies"
        or re.search(
            r"\b(?:U-?(?:17|18|19|20|21|23)|Youth|Reserve[s]?|Women(?:'s)?)\b",
            name,
            re.I,
        )
    ):
        return "excluded", "non-first-team-or-friendly", None, None
    if name not in CUPS.get(lid, set()):
        return "unknown", "unknown-competition", None, None
    sides = [str(game[s]["id"]) for s in ("home", "away")]
    at = utc(game["status"]["utcTime"])
    for obs, h in packet.available("game", str(game["id"]), cut):
        d = packet.json(h)
        g = d["general"]
        st = d["header"]["status"]
        if (
            str(g["matchId"]) != str(game["id"])
            or utc(g["matchTimeUTCDate"]) != at
            or (g.get("leagueId") != lid and g.get("parentLeagueId") != lid)
            or g.get("leagueName") != name
            or g.get("gender") not in ("male", "men")
            or [str(g[s + "Team"]["id"]) for s in ("home", "away")] != sides
            or st.get("cancelled")
        ):
            continue
        stage = str(
            g.get("leagueRoundName")
            or g.get("leagueStageName")
            or tour.get("stage")
            or ""
        ).strip()
        if stage:
            return "cup", stage, h, obs.isoformat()
    for obs, h in packet.available("daily", str(game["id"]), cut):
        groups = packet.json(h)["leagues"]
        for group in groups:
            if (
                group.get("primaryId") != lid
                or group.get("parentLeagueId") != lid
                or group.get("name") != name
            ):
                continue
            matches = [v for v in group["matches"] if str(v["id"]) == str(game["id"])]
            if len(matches) != 1:
                continue
            v = matches[0]
            st = v["status"]
            if (
                v["leagueId"] != group["id"]
                or [str(v[s]["id"]) for s in ("home", "away")] != sides
                or utc(st["utcTime"]) != at
                or not st.get("finished")
                or st.get("cancelled")
            ):
                continue
            if str(v.get("tournamentStage") or "").strip():
                return "cup", str(v["tournamentStage"]), h, obs.isoformat()
    stage = str(tour.get("stage") or "").strip()
    return (
        ("cup", stage, None, None)
        if stage
        else ("unknown", "unverified-cup-round", None, None)
    )


def team_calendar(packet, doc, observed, h, f, team, fm_id, clubs, cut):
    kick = utc(f["kickoff_at"])
    code = f["competition"]
    if observed > cut or cut >= kick or str(doc.get("details", {}).get("id")) != team:
        raise EvidenceGap("WRONG_TEAM_OR_CAPTURE")
    catalog = doc.get("fixtures", {}).get("allFixtures", {}).get("fixtures")
    if not isinstance(catalog, list):
        raise EvidenceGap("NO_NATIVE_TEAM_CALENDAR")
    prior = []
    found = False
    seen = {}
    for game in catalog:
        status = game.get("status") or {}
        u = status.get("utcTime")
        mid = str(game["id"])
        if not u or not game.get("home") or not game.get("away"):
            raise EvidenceGap("CALENDAR_ROW_UNBOUND")
        at = utc(u)
        players = [str(game[s]["id"]) for s in ("home", "away")]
        tour = game.get("tournament") or {}
        identity = (
            at,
            tuple(players),
            tour.get("leagueId"),
            tour.get("name"),
            status.get("finished"),
            status.get("cancelled"),
            status.get("awarded"),
        )
        if mid in seen:
            if seen[mid] != identity:
                raise EvidenceGap("CONFLICTING_EVENT")
            continue
        seen[mid] = identity
        if mid == fm_id:
            if (
                players != clubs
                or at != kick
                or status.get("finished")
                or status.get("started")
                or status.get("cancelled")
                or not native_league(code, tour.get("leagueId"), tour.get("name"))
            ):
                raise EvidenceGap("CALENDAR_TARGET_CONFLICT")
            found = True
            continue
        if at > kick:
            continue
        if len(set(players)) != 2 or team not in players:
            raise EvidenceGap("WRONG_CALENDAR_PARTICIPANTS")
        if status.get("cancelled") or status.get("awarded"):
            continue
        if not tour.get("leagueId") or not tour.get("name"):
            raise EvidenceGap("UNBOUND_COMPETITION")
        if at == kick:
            raise EvidenceGap("SIMULTANEOUS_EVENT")
        prior.append((at, mid, game))
    if not found:
        raise EvidenceGap("TARGET_ABSENT_FROM_TEAM_CALENDAR")
    last_all = last_league = None
    for at, mid, game in sorted(prior, key=lambda x: x[0], reverse=True):
        kind, phase, phase_hash, phase_time = prior_stage(packet, game, team, cut, code)
        if kind == "excluded":
            continue
        if kind == "unknown":
            if last_all is None:
                raise EvidenceGap("UNVERIFIED_INTERVENING_COMPETITION_OR_ROUND")
            if game["tournament"]["leagueId"] in (
                LEAGUES[code][0],
                937276 if code == "ned.1" else -1,
            ):
                raise EvidenceGap("AMBIGUOUS_LEAGUE_PHASE")
            continue
        st = game["status"]
        if (
            at + timedelta(minutes=90) > observed
            or st.get("finished") is not True
            or (st.get("reason") or {}).get("short") not in FINAL
        ):
            if last_all is None or kind == "league":
                raise EvidenceGap("UNFINISHED_OR_UNRESOLVED_INTERVENING_GAME")
            continue
        proof = dict(
            event=mid,
            at=at.isoformat(),
            tournament=game["tournament"]["leagueId"],
            phase=phase,
            phase_sha256=phase_hash,
            phase_received_at=phase_time,
        )
        if last_all is None:
            last_all = proof
        if kind == "league":
            last_league = proof
            break
    if last_league is None:
        raise EvidenceGap("NO_PREVIOUS_DOMESTIC_LEAGUE_EVENT")

    def elapsed(event):
        return (kick - utc(event["at"])).total_seconds() / 3600

    def days(event):
        return min(21, (kick.date() - utc(event["at"]).date()).days)

    return dict(
        calendar_sha256=h,
        received_at=observed.isoformat(),
        team=team,
        league=last_league,
        all=last_all,
        league_hours=elapsed(last_league),
        all_hours=elapsed(last_all),
        league_days=days(last_league),
        all_days=days(last_all),
    )
