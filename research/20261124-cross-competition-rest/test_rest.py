import copy
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from archives import Packet, utc
from calendars import EvidenceGap, team_calendar
from grade import final, hits, metrics
from rest_model import ml, with_gaps


class Calendars(unittest.TestCase):
    def setUp(self):
        self.p = Packet.__new__(Packet)
        from collections import defaultdict

        self.p.refs = defaultdict(list)
        self.p.blobs = {}
        self.f = dict(kickoff_at="2026-11-07T17:00:00+00:00", competition="eng.1")
        self.cut = utc("2026-11-07T10:00:00Z")

        def game(id_, day, lid, name, phase, finished):
            return dict(
                id=id_,
                home={"id": 1, "name": "Club A"},
                away={"id": 2, "name": "Club B"},
                tournament=dict(leagueId=lid, name=name, stage=phase),
                status=dict(
                    utcTime="2026-11-" + day + "T17:00:00Z",
                    finished=finished,
                    reason=dict(short="FT") if finished else None,
                ),
            )

        self.league = game(1, "01", 47, "Premier League", "", True)
        self.cup = game(2, "04", 42, "Champions League", "4", True)
        self.target = game(3, "07", 47, "Premier League", "", False)
        self.doc = {
            "details": {"id": 1},
            "fixtures": {
                "allFixtures": {"fixtures": [self.league, self.cup, self.target]}
            },
        }

    def from_calendar(self, observed=None):
        return team_calendar(
            self.p,
            self.doc,
            observed or self.cut,
            "calendarhash",
            self.f,
            "1",
            "3",
            ["1", "2"],
            self.cut,
        )

    def test_same_club_competitive_round_corrects_only_true_gap(self):
        v = self.from_calendar()
        self.assertEqual(
            [v["league_days"], v["all_days"], v["league_hours"], v["all_hours"]],
            [6, 3, 144, 72],
        )

    def test_later_observation_or_unfinished_intervening_game(self):
        with self.assertRaises(EvidenceGap):
            self.from_calendar(utc("2026-11-07T11:00Z"))
        self.cup["status"]["finished"] = False
        with self.assertRaises(EvidenceGap):
            self.from_calendar()

    def test_known_cancellation_friendly_and_youth_do_not_shorten_rest(self):
        for change in (
            {"cancelled": True},
            {"tournament": {"name": "Club Friendlies", "leagueId": 489, "stage": ""}},
            {"away": {"id": 2, "name": "Club B U21"}},
        ):
            with self.subTest(change=change):
                c = copy.deepcopy(self.cup)
                if "cancelled" in change:
                    c["status"].update(change)
                else:
                    c.update(change)
                self.doc["fixtures"]["allFixtures"]["fixtures"][1] = c
                self.assertEqual(self.from_calendar()["all_days"], 6)

    def test_unverified_round_or_identity_conflict_abstains(self):
        self.cup["tournament"]["stage"] = ""
        with self.assertRaises(EvidenceGap):
            self.from_calendar()
        self.cup["tournament"]["stage"] = "4"
        self.target["away"]["id"] = 88
        with self.assertRaises(EvidenceGap):
            self.from_calendar()

    def test_daily_round_must_match_ordered_teams_and_cutoff(self):
        self.cup["tournament"]["stage"] = ""
        earlier = {
            "id": 2,
            "leagueId": 4000,
            "home": {"id": 1},
            "away": {"id": 2},
            "status": self.cup["status"],
            "tournamentStage": "4",
        }
        group = {
            "primaryId": 42,
            "parentLeagueId": 42,
            "id": 4000,
            "name": "Champions League",
            "matches": [earlier],
        }

        def store(when):
            self.p.blobs["day"] = json.dumps({"leagues": [group]}).encode()
            self.p.refs["daily", "2"] = [(when, "day")]

        store(self.cut)
        self.assertEqual(self.from_calendar()["all"]["phase_sha256"], "day")
        store(utc("2026-11-07T10:01Z"))
        with self.assertRaises(EvidenceGap):
            self.from_calendar()
        earlier["away"]["id"] = 99
        store(self.cut)
        with self.assertRaises(EvidenceGap):
            self.from_calendar()


class ModelAndGrading(unittest.TestCase):
    def test_gap_change_swaps_only_two_named_columns_in_opposite_roles(self):
        x = np.arange(2 * len(ml.FEATURES), dtype=float).reshape(2, -1)
        x[0, 0] = np.nan
        y = with_gaps(x, 7, 3)
        own = ml.FEATURES.index("own_same_cohort_gap")
        opp = ml.FEATURES.index("opponent_same_cohort_gap")
        np.testing.assert_equal(y[:, [own, opp]], [[7, 3], [3, 7]])
        np.testing.assert_equal(
            np.delete(y, [own, opp], axis=1), np.delete(x, [own, opp], axis=1)
        )

    def test_matrix_three_is_distinct_and_push_excluded(self):
        matrix = np.asarray([[0.4, 0.3], [0.25, 0.05]])
        study = hits(matrix, (0, 1), 1)
        self.assertFalse(study["top1"])
        self.assertTrue(study["top3"])
        self.assertIsNone(study["ou"])
        counts = metrics([dict(control=study, candidate=study)])
        self.assertEqual(counts["ou"]["n"], 0)
        self.assertEqual(counts["top3"]["n"], 1)

    def test_raw_label_rejects_rescheduled_or_extra_time_match(self):
        time = "2026-11-07T17:00:00Z"
        row = dict(
            match_id="espn:1",
            competition="eng.1",
            cohort="eng.1",
            kickoff_at=time,
            home="espn:2",
            away="espn:3",
        )
        status = dict(period=2, type={"completed": True, "name": "STATUS_FULL_TIME"})
        c = {
            "id": "1",
            "date": time,
            "status": status,
            "competitors": [
                dict(homeAway=s, team={"id": i}, score=v)
                for s, i, v in [("home", 2, "1"), ("away", 3, "0")]
            ],
        }
        raw = {"header": {"id": "1", "league": {"slug": "eng.1"}, "competitions": [c]}}
        url = (
            "https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/summary?event=1"
        )
        with tempfile.TemporaryDirectory() as tmp:

            def verify():
                return final(
                    json.dumps(raw).encode(),
                    url,
                    "2026-11-08T10:00Z",
                    row,
                    None,
                    Path(tmp) / "result.json",
                )

            self.assertEqual(verify(), (1, 0))
            c["date"] = "2026-11-07T19:00:00Z"
            with self.assertRaises(ValueError):
                verify()
            c["date"] = time
            status["period"] = 4
            with self.assertRaises(ValueError):
                verify()


if __name__ == "__main__":
    unittest.main()
