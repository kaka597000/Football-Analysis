"""Check historical regulation-score provenance, including the archived K1 raw season cards."""

from collections import defaultdict
import gzip
from hashlib import sha256
import json
import re
import tarfile
from urllib.parse import parse_qs, urlsplit

from research.analysis_freeze import aware
from .goal_state_study import sha
from .r8_score_study import read


ARCHIVED_BASES = {
    "regulation_90", "verified_regulation_from_collection", "regulation_90_from_scoring_timeline",
    "regulation_90_scoring_events_excluding_extra_time_and_shootout",
    "football-data.co.uk HG/AG full-time score fields; regulation-time league result",
    "regulation_90 (retained ESPN goal-event clock ≤5400 seconds; extra time and shootout excluded)",
    "regulation_90 (sum of ESPN first- and second-half linescores)",
    "regulation_90 (ESPN scoreboard STATUS_FULL_TIME, period=2; no extra-time state)",
    "regulation_90 (ESPN scoring events through 5400 seconds; extra-time and shootout goals excluded)",
    "regulation_90 from timed scoring plays; non-shootout regulation plus extra-time goals cross-checked against both displayed team scores",
}


def korean_result(row, game):
    status = game["status"]
    source_teams = [game[side] for side in ("home", "away")]
    if ("fotmob:"+str(game["id"]) != row["id"] or source_teams[0]["id"] == source_teams[1]["id"]
            or [s["name"] for s in source_teams] != [row["home"], row["away"]]
            or aware(status["utcTime"]).date().isoformat() != row["date"]
            or row["stage"] not in ("regular", "split-a", "split-b") or not 1 <= int(game["round"]) <= 38):
        raise ValueError("K1 source fixture, scheduled league round or teams differ: "+row["id"])
    reason = status.get("reason") or {}
    if status.get("awarded") or status.get("cancelled") or reason.get("short") != "FT":
        return "awarded" if status.get("awarded") else "no_regulation_full_time"
    score = re.fullmatch(r"(\d+)\s*-\s*(\d+)", status.get("scoreStr") or "")
    if (not score or not status.get("finished") or reason.get("shortKey") != "fulltime_short"
            or [int(v) for v in score.groups()] != [row["home_goals"], row["away_goals"]]):
        raise ValueError("K1 source full-time score differs: "+row["id"])
    return None


def verify(games, r5, manifest):
    by_source = defaultdict(list)
    for row in games:
        if row["score_basis"] in ARCHIVED_BASES:
            continue
        if row["competition"] != "kor.1" or row["score_basis"] != "provider_fulltime_90m_unverified":
            raise ValueError("unknown historical score basis: "+row["score_basis"])
        by_source[row["source_sha256"]].append(row)
    if not by_source:
        return {}
    index_name = "evidence/source-index.json"
    if sha(r5/index_name) != manifest["files"][index_name]:
        raise ValueError("R5 source index differs")
    index = read(r5/index_name)
    checked = set()
    withheld = {}
    for key, rows in by_source.items():
        entry = index[key]
        name = "evidence/"+entry["archive"]
        if name not in checked and sha(r5/name) != manifest["files"][name]:
            raise ValueError("K1 source archive differs: "+name)
        checked.add(name)
        with tarfile.open(r5/name) as tar:
            body = gzip.decompress(tar.extractfile(entry["member"]).read())
        if sha256(body).hexdigest() != key:
            raise ValueError("K1 original season bytes differ")
        doc = json.loads(body)
        season = str(doc["details"]["selectedSeason"])
        u = urlsplit(entry["sources"][0])
        query = parse_qs(u.query)
        if (str(doc["details"]["id"]) != "9080" or u.scheme != "https" or u.hostname != "www.fotmob.com"
                or u.path != "/api/data/leagues" or query.get("id") != ["9080"] or query.get("season") != [season]
                or any(str(r["season"]) != season for r in rows)):
            raise ValueError("K1 season or league provenance differs")
        events = doc["fixtures"]["allMatches"]
        keyed = {"fotmob:"+str(g["id"]):g for g in events}
        if len(keyed) != len(events):
            raise ValueError("K1 season contains duplicate event IDs")
        for row in rows:
            reason = korean_result(row, keyed[row["id"]])
            if reason:
                withheld[row["id"]] = reason
    return withheld
