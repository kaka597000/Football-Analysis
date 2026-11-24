"""Read the independently archived Nov 6 regular-season first issue before its target label."""

import argparse
from datetime import timedelta
from hashlib import sha256
import json
from pathlib import Path
import tempfile

from opportunities import clock, matrix, native_pair, require, shot_profile
from replay import evidence, market, packed_value
from research.analysis_freeze import digest, verify

PACK = "workspace-updates/20261106-aleague-analysis/"
SHA = "7ccf7f258131eedcc1ea187ffee93c666df4a21db1c2e6e77eccda918052b9ba"
PREFIX = "WCPP/reports/aleague-20261106/"
MID = "espn:401892144"
NATIVE = "1000017575"


def blind(repo):
    with (
        tempfile.TemporaryDirectory() as tmp,
        evidence(repo, Path(tmp), PACK, SHA) as arc,
    ):

        def get(rel):
            return packed_value(arc, PREFIX + rel)

        first = {"match_id": MID, "stage": "prematch", "track": "current-test-pair"}
        fr = verify(
            packed_value(
                arc, "WCPP/forecasts/issued-analyses/" + digest(first) + "/000001.json"
            )
        )
        f = fr["fixture"]
        cut, ko = clock(f["cutoff_at"]), clock(f["kickoff_at"])
        require(
            all(fr[k] == v for k, v in first.items())
            and fr["revision"] == 1
            and fr["previous_sha256"] is None
            and fr["evaluation_slot"] == "FIRST_PREMATCH"
            and f["cohort"] == "aus.1"
            and f["stage"] == "regular"
            and cut <= clock(fr["recorded_at"]) < ko,
            "first_regular_issue",
        )

        def native(name, url):
            meta = get("data/" + name + ".meta.json")
            raw = arc.extractfile(PREFIX + "data/" + name + ".json").read()
            at = clock(meta["queried_at_utc"])
            require(
                meta["url"] == url
                and meta["http_status"] == 200
                and at <= cut
                and sha256(raw).hexdigest() == meta["sha256"],
                "source_clock_or_identity",
            )
            return json.loads(raw), at

        def detail(name, fid):
            return native(
                name,
                "https://www.fotmob.com/api/data/matchDetails?matchId="
                + fid
                + "&ccode3=INT",
            )

        header = native(
            "espn_event_summary",
            "https://site.api.espn.com/apis/site/v2/sports/soccer/aus.1/summary?event=401892144",
        )[0]["header"]
        (game,) = header["competitions"]
        sides = {v["homeAway"]: v["team"] for v in game["competitors"]}
        require(
            header["league"]["slug"] == "aus.1"
            and header["season"]["name"].endswith("Regular Season")
            and str(game["id"]) == MID.split(":")[1]
            and clock(game["date"]) == ko
            and game["status"]["type"]["state"] == "pre"
            and all(f[s] == "espn:" + str(sides[s]["id"]) for s in ("home", "away")),
            "espn_fixture",
        )
        target, _ = detail("fotmob_match_details", NATIVE)
        g = target["general"]
        names = tuple(g[k]["name"] for k in ("homeTeam", "awayTeam"))
        require(
            str(g["matchId"]) == NATIVE
            and g["parentLeagueId"] == 113
            and not g["started"]
            and clock(g["matchTimeUTCDate"]) == ko
            and names == ("Sydney FC", "Central Coast Mariners")
            and (sides["home"]["displayName"], sides["away"]["displayName"])
            == ("Sydney FC", "Central Coast Mariners FC"),
            "provider_crosswalk",
        )
        histories = []
        for team, listed in zip(
            native_pair(target),
            target["content"]["matchFacts"]["teamForm"],
            strict=True,
        ):
            prior3 = sorted(
                listed, key=lambda r: clock(r["date"]["utcTime"]), reverse=True
            )[:3]
            require(
                len({r["linkToMatch"] for r in prior3}) == 3,
                "incomplete_prior_schedule",
            )
            profile = []
            for slot in prior3:
                fid = slot["linkToMatch"].split("#")[-1]
                raw, received = detail("match_" + fid, fid)
                tip = slot["tooltipText"]
                require(
                    clock(tip["utcTime"]) == clock(slot["date"]["utcTime"])
                    and cut - timedelta(days=45) <= clock(tip["utcTime"]) < received
                    and raw["general"]["parentLeagueId"] == 113,
                    "senior_recent_fixture",
                )
                prior = dict(
                    id=fid,
                    home={"id": tip["homeTeamId"]},
                    away={"id": tip["awayTeamId"]},
                    status={"utcTime": tip["utcTime"], "reason": {"short": "FT"}},
                )
                probs = shot_profile(raw, prior, received)
                require(team in probs, "prior_team")
                (opponent,) = [t for t in probs if t != team]
                profile.append((probs[team], probs[opponent]))
            histories.append(profile)
        current = fr["payload"]["current_model"]
        require(
            current["role"] == "CURRENT_AUS_SIMPLE_BASELINE"
            and current["model_kind"] == "constant"
            and current["trained_xg_channel"] is False
            and digest(current["matrix"]) == current["matrix_hash"],
            "team_baseline",
        )
        meta = get("market/raw/the-odds-2026-11-06.metadata.json")
        raw = arc.extractfile(PREFIX + "market/raw/the-odds-2026-11-06.json").read()
        require(
            meta["http_status"] == 200
            and sha256(raw).hexdigest() == meta["raw_sha256"],
            "market_source",
        )
        # ESPN/FotMob native identities were cross-checked above; now require the same ordered odds event.
        mapped = dict(
            odds_api_match_status="EXACT_KICKOFF_TEAM_LABEL_VARIANT",
            odds_api_event_id=f["total_quote"]["source_event_id"],
            espn_state=game["status"]["type"]["name"],
            espn_home_team_id=sides["home"]["id"],
            espn_away_team_id=sides["away"]["id"],
            kickoff_utc=game["date"],
            odds_api_home_team=names[0],
            odds_api_away_team=names[1],
        )
        m, line = market(
            f,
            mapped,
            dict(
                queried_at_utc=meta["query_time_utc"],
                saved_file_sha256=meta["raw_sha256"],
            ),
            json.loads(raw),
            sport="soccer_australia_aleague",
        )
        return dict(
            id=MID,
            competition="aus.1",
            home=f["home"],
            away=f["away"],
            cutoff=f["cutoff_at"],
            kickoff=f["kickoff_at"],
            candidate=matrix(*histories).tolist(),
            same_xg_poisson=matrix(*histories, at_mean=True).tolist(),
            team=current["matrix"],
            market=m,
            line=line,
            main_asian_line_verified=f["total_quote"]["asian_main_line_verified"],
        )


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-repo", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    result = blind(a.data_repo)
    with a.out.open("x") as f:
        json.dump([result], f, separators=(",", ":"), allow_nan=False)
    print("1 regular-season match, 2 teams, 6 verified prior matches")
