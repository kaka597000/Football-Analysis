"""Synthetic identity and chronology regression tests; no supplier requests or outcomes."""

import json
import unittest
from datetime import timedelta

from people_evidence import (
    admission,
    espn,
    fotmob,
    jleague,
    preview,
    regular_fotmob_fixture,
    sha,
    times,
    utc,
)
from slate_roles import history_keeper, schedule_check, starting_keeper, usual_keeper

KICK = "2026-01-02T18:00:00Z"
CUT = "2026-01-02T17:00:00Z"
PRE = "2026-01-02T16:55:00Z"


def encode(doc):
    return json.dumps(doc, ensure_ascii=False).encode()


def fixture(provider="espn"):
    return dict(
        match_id=provider + ":1",
        competition="eng.1",
        kickoff_at=KICK,
        cutoff_at=CUT,
        home=provider + ":10",
        away=provider + ":20",
    )


def xi(team):
    return [
        dict(
            id=team * 100 + i,
            positionId=11 if i == 0 else 70,
            usualPlayingPositionId=0 if i == 0 else 3,
        )
        for i in range(11)
    ]


def fm_doc(typ="predicted"):
    return dict(
        general=dict(
            matchId=1,
            homeTeam={"id": 10},
            awayTeam={"id": 20},
            leagueId=47,
            matchTimeUTCDate=KICK,
            started=False,
            finished=False,
        ),
        content={
            "lineup": dict(
                matchId=1,
                lineupType=typ,
                homeTeam=dict(id=10, starters=xi(10)),
                awayTeam=dict(id=20, starters=xi(20)),
            )
        },
    )


def fot_read(d):
    raw = encode(d)
    return fotmob(
        raw,
        dict(
            sha256=sha(raw),
            source_url="https://www.fotmob.com/api/matchDetails?matchId=1",
            received_at=PRE,
        ),
    )


def esp_doc(state="pre"):
    return {
        "header": {
            "id": "1",
            "league": {"slug": "eng.1"},
            "competitions": [
                {
                    "id": "1",
                    "date": KICK,
                    "status": {
                        "type": {
                            "state": state,
                            "completed": state == "post",
                            "name": "STATUS_SCHEDULED"
                            if state == "pre"
                            else "STATUS_FULL_TIME",
                        }
                    },
                    "competitors": [
                        {"homeAway": s, "team": {"id": str(t)}}
                        for s, t in (("home", 10), ("away", 20))
                    ],
                }
            ],
        },
        "rosters": [
            {
                "homeAway": s,
                "team": {"id": str(t)},
                "roster": [
                    {"athlete": {"id": p["id"]}, "starter": True} for p in xi(t)
                ],
            }
            for s, t in (("home", 10), ("away", 20))
        ],
    }


def esp_meta(raw):
    url = "https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/summary?event=1"
    return dict(
        source_url=url,
        status="OK",
        raw=dict(sha256=sha(raw), source_url=url, captured_at=PRE),
        captured_at=PRE,
        started_at=PRE,
    )


class ClockAndSources(unittest.TestCase):
    def test_receipt_at_cutoff(self):
        f = fixture()
        o = f | dict(received_at=CUT, pregame=True, kind="PROVIDER_BOTH_XI")
        self.assertEqual(admission(f, o, utc(CUT)), o["kind"])

    def test_earlier_publication_does_not_backdate_receipt(self):
        f = fixture()
        o = f | dict(
            received_at="2026-01-02T17:01:00Z",
            published_at=PRE,
            pregame=True,
            kind="OFFICIAL_BOTH_XI",
        )
        self.assertEqual(admission(f, o, utc(CUT)), "AFTER_CUTOFF")
        self.assertEqual(
            admission(f, o | {"published_at": KICK}, utc(CUT)), "SOURCE_CLOCK_CONFLICT"
        )

    def test_before_kickoff_and_timezone(self):
        f = fixture()
        for at in (KICK, "2026-01-02T19:00:00Z"):
            self.assertEqual(
                admission(
                    f, f | dict(received_at=at, pregame=True, kind="test"), utc(CUT)
                ),
                "LIVE_OR_POSTMATCH",
            )
        for date in (KICK, "2026-01-02T19:00:00Z", "2026-01-02T17:00:00"):
            with self.assertRaises(ValueError):
                times(f | {"cutoff_at": date})

    def test_swapped_clubs_wrong_competition_or_new_kickoff(self):
        f = fixture()
        o = f | dict(received_at=PRE, pregame=True, kind="OFFICIAL_BOTH_XI")
        for alt in (
            dict(match_id="espn:2"),
            dict(competition="esp.1"),
            dict(away=f["home"], home=f["away"]),
            dict(kickoff_at=CUT),
        ):
            self.assertEqual(admission(f, o | alt, utc(CUT)), "IDENTITY_MISMATCH")

    def test_postgame_roster_rejected_even_if_complete(self):
        d = esp_doc("post")
        b = encode(d)
        v = preview("espn", fixture(), esp_meta(b), b)
        self.assertEqual(
            {s["status"] for s in v["comparison"].values()}, {"LIVE_OR_POSTMATCH"}
        )
        self.assertFalse(v["prediction_issued"])

    def test_pregame_provider_is_provider(self):
        b = encode(esp_doc())
        o = espn(b, esp_meta(b))
        self.assertEqual(o["kind"], "PROVIDER_BOTH_XI")
        self.assertTrue(o["pregame"])

    def test_duplicate_or_wrong_three_way_match(self):
        d = esp_doc()
        d["rosters"][1]["roster"][0] = d["rosters"][0]["roster"][0]
        b = encode(d)
        self.assertFalse(espn(b, esp_meta(b))["both_xi"])
        d = esp_doc()
        d["rosters"][0]["team"]["id"] = "20"
        b = encode(d)
        self.assertFalse(espn(b, esp_meta(b))["both_xi"])

    def test_receipt_and_source_hash(self):
        b = encode(esp_doc())
        r = esp_meta(b)
        for different in (
            r | {"status": "FAILED"},
            r | {"raw": r["raw"] | {"sha256": "f" * 64}},
            r | {"source_url": r["source_url"].replace("event=1", "event=2")},
        ):
            with self.assertRaises(ValueError):
                espn(b, different)

    def test_predicted_previous_and_provider_confirmed_separate(self):
        for typ, state in (
            ("predicted", "PROJECTED_XI"),
            ("lastStarting11", "PREVIOUS_XI"),
            ("confirmed", "PROVIDER_BOTH_XI"),
            ("standard", "PROVIDER_WITHOUT_CONFIRMED_BOTH_XI"),
        ):
            self.assertEqual(fot_read(fm_doc(typ))["kind"], state)
        d = fm_doc()
        d["content"]["lineup"]["homeTeam"]["lineupType"] = "lastStarting11"
        self.assertEqual(fot_read(d)["kind"], "PROVIDER_WITHOUT_CONFIRMED_BOTH_XI")

    def test_provider_missing_confirmation_and_absence_states(self):
        d = fm_doc()
        d["content"]["lineup"]["homeTeam"]["unavailable"] = []
        self.assertEqual(
            fot_read(d)["availability"]["home"]["status"], "PROVIDER_EMPTY_UNKNOWN"
        )
        self.assertEqual(
            fot_read(d)["availability"]["away"]["status"], "SCHEMA_MISSING_UNKNOWN"
        )
        del d["general"]["finished"]
        self.assertFalse(fot_read(d)["pregame"])

    def test_bad_provider_lineup_identity(self):
        for change in ("other_match", "other_club", "duplicate"):
            d = fm_doc()
            ln = d["content"]["lineup"]
            if change == "other_match":
                ln["matchId"] = 2
            if change == "other_club":
                ln["awayTeam"]["id"] = 10
            if change == "duplicate":
                ln["homeTeam"]["starters"][1] = ln["homeTeam"]["starters"][0]
                self.assertFalse(fot_read(d)["both_xi"])
            else:
                with self.assertRaises(ValueError):
                    fot_read(d)

    def test_native_division_and_regular_phase(self):
        native = {
            "leagueId": 937276,
            "parentLeagueId": 57,
            "leagueName": "Eredivisie",
            "leagueRoundName": "12",
        }
        f = {"competition": "ned.1", "stage": "regular"}
        self.assertTrue(regular_fotmob_fixture(f, native))
        self.assertFalse(
            regular_fotmob_fixture(f, native | {"leagueRoundName": "playoffs"})
        )
        self.assertFalse(regular_fotmob_fixture(f, native | {"parentLeagueId": 11}))
        self.assertFalse(regular_fotmob_fixture(f | {"stage": "quarterfinals"}, native))

    def test_same_provider_league_required(self):
        d = fm_doc()
        b = encode(d)
        with self.assertRaises(ValueError):
            preview(
                "fotmob",
                fixture("fotmob") | {"fotmob_league_id": 99},
                dict(
                    sha256=sha(b),
                    source_url="https://www.fotmob.com/api/matchDetails?matchId=1",
                    received_at=PRE,
                ),
                b,
            )


def jp_record(state="pre-game", section="準々決勝第1戦", duplicate=False):
    f = dict(
        competition="jpn.league_cup",
        stage="quarterfinals",
        leg=1,
        match_id="jleague:111101",
        home="jleague:a",
        away="jleague:b",
        kickoff_at=KICK,
        cutoff_at=CUT,
    )
    url = "https://www.jleague.jp/match/leaguecup/2026/111101/"
    teams = {}
    rows = [[], []]
    for n, side in enumerate(("home", "away")):
        club = "ab"[n]
        players = []
        for i in range(11):
            pos = "GK" if i == 0 else "DF"
            role = f"{pos} {i + 1}"
            name = club + str(i)
            rows[n].append(f"{role} {name}")
            players.append(
                dict(
                    position=role,
                    name=name,
                    legacyPlayerPhotoLookup=dict(
                        teamNameKey=club, playerId=(0 if duplicate else n) * 100 + i + 1
                    ),
                )
            )
        teams[side + "Team"] = dict(code=club, players=players)
    obj = teams | dict(type=state, date="$D" + KICK, section=section)
    serial = json.dumps(
        "1:" + json.dumps(obj, ensure_ascii=False) + "\n", ensure_ascii=False
    )
    article = " ".join(v for pair in zip(*rows) for v in pair)
    body = f'<link rel="canonical" href="{url}"><script>self.__next_f.push([1,{serial}])</script><div>スターティングメンバー発表 {article} フォーメーション</div>'.encode()
    return (
        f,
        dict(
            url=url,
            match_id=f["match_id"],
            http_status=200,
            sha256=sha(body),
            requested_at_utc_complete=PRE,
            requested_at_utc_start=PRE,
        ),
        body,
    )


class OfficialAndHistory(unittest.TestCase):
    def test_official_pair_roles_and_three_clocks(self):
        f, r, b = jp_record()
        o = jleague(b, r, f)
        self.assertEqual(o["keeper_ids"], ["1", "101"])
        self.assertEqual(
            preview("jleague", f, r, b)["comparison"]["T-60"]["status"],
            "OFFICIAL_BOTH_XI",
        )

    def test_wrong_leg_team_page_or_state(self):
        for mode in (
            "different_leg",
            "swapped",
            "wrongurl",
            "post",
            "overlap",
            "missing_visible",
        ):
            f, r, b = jp_record(
                state="post-game" if mode == "post" else "pre-game",
                duplicate=mode == "overlap",
            )
            if mode == "different_leg":
                f["leg"] = 2
            if mode == "swapped":
                f["home"], f["away"] = f["away"], f["home"]
            if mode == "wrongurl":
                r["url"] = r["url"].replace("111101", "111102")
            if mode == "missing_visible":
                b = b.replace(b"DF 11 a10", b"DF 11 wrong")
                r["sha256"] = sha(b)
            with self.assertRaises(ValueError):
                jleague(b, r, f)

    def test_dual_gk_position_and_stability(self):
        self.assertEqual(starting_keeper(dict(id=10, starters=xi(10)), "10"), "1000")
        squad = xi(10)
        squad[0]["usualPlayingPositionId"] = 2
        self.assertIsNone(starting_keeper(dict(id=10, starters=squad), "10"))
        self.assertEqual(usual_keeper(["a"] * 4 + ["b"] * 2), "a")
        for history in (
            ["a"] * 3 + ["b"] * 3,
            ["a"] * 5,
            ["a"] * 6 + [None],
            ["a"] * 5 + [None],
        ):
            self.assertIsNone(usual_keeper(history))

    def prior(self):
        d = fm_doc("standard")
        d["general"].update(started=True, finished=True)
        d["header"] = {"status": {"reason": {"short": "FT"}, "halfs": {}}}
        ref = dict(
            source_sha256=sha(encode(d)),
            source_url="https://www.fotmob.com/api/matchDetails?matchId=1",
            id="fotmob:1",
            observed_at="2026-01-02T20:10:00Z",
            kickoff_at=KICK,
            fotmob_home_team_id=10,
            fotmob_away_team_id=20,
            side="home",
        )
        return d, ref

    def hist(self, d, r):
        raw = encode(d)
        return history_keeper(
            raw,
            r | {"source_sha256": sha(raw)},
            "10",
            47,
            utc("2026-01-06T18:00:00Z"),
            utc("2026-01-06T17:00:00Z"),
        )

    def test_previous_completed_roster_can_inform_later_fixture(self):
        d, r = self.prior()
        self.assertEqual(self.hist(d, r), ("1000", "BOUND_PRIOR_GK"))
        for when in ("2026-01-02T18:10:00Z", "2026-01-06T17:01:00Z"):
            self.assertIsNone(self.hist(d, r | {"observed_at": when})[0])
        self.assertIsNone(self.hist(d, r | {"side": "away"})[0])

    def test_prior_match_no_live_extra_time_or_projected(self):
        for mode in ("notfinished", "extra", "predicted", "later", "same_fixture"):
            d, r = self.prior()
            if mode == "notfinished":
                d["general"]["finished"] = False
            elif mode == "extra":
                d["header"]["status"]["halfs"]["extraHalf1"] = "2026-01-02T20:00:00Z"
            elif mode == "predicted":
                d["content"]["lineup"]["lineupType"] = "predicted"
            else:
                date = (
                    "2026-01-07T18:00:00Z"
                    if mode == "later"
                    else "2026-01-06T18:00:00Z"
                )
                d["general"]["matchTimeUTCDate"] = r["kickoff_at"] = date
            self.assertIsNone(self.hist(d, r)[0])

    def test_independent_calendar_checks_last_six(self):
        t = utc(KICK)
        prior = []
        fixtures = []
        for k in range(7):
            at = (t - timedelta(days=k + 1)).isoformat()
            prior.append(dict(kickoff_at=at, id=f"fotmob:{k}"))
            fixtures.append(
                dict(
                    id=k,
                    tournament=dict(leagueId=47),
                    status=dict(utcTime=at, finished=True, reason=dict(short="FT")),
                    home=dict(id=10),
                    away=dict(id=20),
                )
            )
        blob = encode(
            dict(
                details=dict(id=10), fixtures=dict(allFixtures=dict(fixtures=fixtures))
            )
        )
        url = "https://www.fotmob.com/api/data/teams?id=10"
        h = sha(blob)
        packet = dict(
            observed_at=PRE,
            kickoff_at=KICK,
            home=dict(
                team_id="espn:10",
                source_url=url,
                source_sha256=h,
                raw_file="raw/team.json",
            ),
        )
        args = (
            packet,
            {"form/west/raw/team.json": h},
            {h: blob},
            "west",
            "home",
            "10",
            "espn:10",
            47,
        )
        self.assertEqual(
            schedule_check(*args, prior[:6], t, utc(CUT)),
            "SIX_MATCH_CALENDAR_CONFIRMED",
        )
        self.assertEqual(
            schedule_check(*args, prior[1:], t, utc(CUT)),
            "SIX_NOT_CONFIRMED_BY_CALENDAR",
        )
        packet["observed_at"] = KICK
        self.assertEqual(
            schedule_check(*args, prior[:6], t, utc(CUT)), "UNBOUND_TEAM_CALENDAR"
        )


if __name__ == "__main__":
    unittest.main()
