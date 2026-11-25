"""Describe non-penalty set-piece attempts from an already retained FotMob match."""

from collections import Counter
from datetime import timedelta
from html.parser import HTMLParser
import json
from math import isfinite

SET_PLAYS = frozenset(("FromCorner", "FreeKick", "SetPiece", "ThrowInSetPiece"))


def located_plays(shots, sides):
    """Called after #78 has checked every shot, both native totals and xG values."""
    counts = {side: Counter() for side in sides}
    xg = {side: Counter() for side in sides}
    for shot in shots:
        kind = shot["situation"]
        if shot["isOwnGoal"] or kind not in SET_PLAYS:
            continue
        for axis, limit in (("x", 105), ("y", 68)):
            v = shot.get(axis)
            if type(v) not in (float, int) or not isfinite(v) or not 0 <= v <= limit:
                raise ValueError("missing_or_invalid_set_piece_location")
        side = str(shot["teamId"])
        counts[side][kind] += 1
        xg[side][kind] += shot["expectedGoals"]
    return {
        "set_plays_by_team": {key: dict(value) for key, value in counts.items()},
        "set_play_xg_by_team": {key: dict(value) for key, value in xg.items()},
    }


def profile(raw, prior, received, cutoff, target_id, team):
    from opportunities import clock, native_pair, require, shot_profile

    general = raw["general"]
    kickoff = clock(prior["status"]["utcTime"])
    require(
        str(prior["id"]) != target_id and team in native_pair(raw), "target_or_team"
    )
    require(cutoff - timedelta(days=45) <= kickoff < received <= cutoff, "source_clock")
    league = prior["tournament"].get("leagueId")
    require(
        league is not None
        and league in (general.get("leagueId"), general.get("parentLeagueId")),
        "prior_competition_conflict",
    )
    shot_profile(raw, prior, received)
    summary = located_plays(raw["content"]["shotmap"]["shots"], native_pair(raw))
    return {"prior": str(prior["id"]), "league": league, **summary}


class NextData(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.active = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("id") == "__NEXT_DATA__":
            self.active = True
            self.parts.append("")

    def handle_data(self, data):
        if self.active:
            self.parts[-1] += data

    def handle_endtag(self, tag):
        if tag == "script":
            self.active = False


def page_detail(body):
    parser = NextData()
    parser.feed(body.decode("utf-8"))
    parser.close()
    if len(parser.parts) != 1 or parser.active:
        raise ValueError("missing_or_ambiguous_native_page")
    detail = json.loads(parser.parts[0])["props"]["pageProps"]
    if (
        not isinstance(detail, dict)
        or not {"general", "header", "content"} <= detail.keys()
    ):
        raise ValueError("missing_native_detail")
    return detail
