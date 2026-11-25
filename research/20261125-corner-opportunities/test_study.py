from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from corners import PriorGames, adjusted, decisions, fit, hits, verify_pair
from research.analysis_freeze import digest
from run_study import quoted, seal_row, summary
from v2.model import matrix


def season(cutoff):
    games = []
    for i in range(65):
        games.append(
            dict(
                day=cutoff - timedelta(days=66 - i),
                home="H" if i % 2 == 0 else "A",
                away="A" if i % 2 == 0 else "H",
                raw=dict(FTHG="1", FTAG="0", FTR="H", HC="6", AC="3"),
            )
        )
    return games


def pair():
    f = dict(
        cohort="eng.1",
        stage="regular",
        score_basis="regulation_90",
        match_id="m",
        home="A",
        away="B",
        kickoff_at="2026-11-21T13:00:00Z",
        cutoff_at="2026-11-21T11:00:00Z",
        neutral=False,
        identity_verified=True,
    )
    pmf = [[0.5, 0.2], [0.1, 0.2]]
    p = dict(
        input=f,
        version="v",
        recipe="baseline",
        context_numeric_adjustment_applied=False,
        model_hash="artifact",
        training_data_cutoff="2026-10-01",
        matrix=pmf,
        matrix_hash=digest(pmf),
        model_observed_at="2026-10-03T11:00:00Z",
        model_built_at="2026-10-03T12:00:00Z",
        generated_at="2026-11-21T11:01:00Z",
    )
    first = dict(
        fixture=f,
        payload={"current_model": p},
        revision=1,
        evaluation_slot="FIRST_PREMATCH",
        stage="prematch",
        track="current-test-pair",
        previous_sha256=None,
        freeze_id="original",
        record_sha256="bound",
        recorded_at="2026-11-21T11:02:00Z",
        match_id="m",
    )
    author = dict(
        match_id="m",
        fixture=deepcopy(f),
        payload=dict(
            current_test_reference=dict(
                freeze_id="original", sha256="bound", recorded_at=first["recorded_at"]
            )
        ),
    )
    return first, author, dict(hash="artifact", version="v", recipe="baseline", train_end="2026-10-01")


class CornerStudy(unittest.TestCase):
    def setUp(self):
        self.cutoff = date(2024, 2, 1)

    def test_home_and_away_corner_normalization(self):
        state = PriorGames(season(self.cutoff), self.cutoff)
        np.testing.assert_allclose(state.means, [6, 3])
        self.assertEqual(state.signal("H", "A"), [0.0, 0.0])

    def test_target_and_preceding_calendar_day_cannot_enter_inputs(self):
        old = season(self.cutoff)
        sentinel = [dict(day=self.cutoff + timedelta(days=d), home="H", away="A") for d in (-1, 0, 1)]
        state = PriorGames(old + sentinel, self.cutoff)
        self.assertEqual(len(state.training), 65)
        self.assertEqual(state.signal("H", "A"), [0.0, 0.0])

    def test_missing_latest_corner_cannot_substitute_older_appearance(self):
        games = season(self.cutoff)
        games[-1]["raw"]["HC"] = ""
        self.assertIsNone(PriorGames(games, self.cutoff).signal("H", "A"))

    def test_short_stale_unknown_and_same_day_conflicts(self):
        games = season(self.cutoff)
        self.assertIsNone(PriorGames(games[:30], self.cutoff).signal("H", "A"))
        self.assertIsNone(PriorGames(games, self.cutoff + timedelta(days=100)).signal("H", "A"))
        self.assertIsNone(PriorGames(games, self.cutoff).signal("H", "Unknown"))
        with self.assertRaisesRegex(ValueError, "same-day"):
            PriorGames(games + [games[-1]], self.cutoff)

    def test_corner_direction_is_home_away_symmetric(self):
        games = season(self.cutoff)
        games[-1]["raw"]["HC"] = "14"
        state = PriorGames(games, self.cutoff)
        x = state.signal("H", "A")
        self.assertGreater(x[0], x[1])
        np.testing.assert_allclose(x, state.signal("A", "H")[::-1])

    def test_offset_fit_has_fixed_sign_and_bounds(self):
        self.assertEqual(fit([(1, 1, 0), (1, -1, 3)]), 0)
        self.assertEqual(fit([(1, 1, 100), (1, -1, 0)]), 1)
        self.assertTrue(0 < fit([(1, 1, 3), (1, -1, 0)]) < 1)

    def test_tilt_matches_poisson_rate_change_and_preserves_zeros(self):
        base = matrix(1.2, 0.9)
        candidate = adjusted(base, [0.3, -0.4], 0.2)
        np.testing.assert_allclose(candidate, matrix(1.2 * np.exp(0.06), 0.9 * np.exp(-0.08)), atol=1e-12)
        sparse = np.array([[0.2, 0.0], [0.5, 0.3]])
        self.assertEqual(adjusted(sparse, [0.4, 0.9], 0.2)[0, 1], 0)
        self.assertIs(adjusted(base, None, 0.2), base)
        self.assertIs(adjusted(base, [0.2, 0.4], 0), base)

    def test_rank_and_quarter_line_settlement(self):
        base = np.array([[0.25, 0.25], [0.25, 0.25]])
        p = decisions(base, None)
        self.assertEqual(p["scores"], ["0-0", "0-1", "1-0"])
        self.assertTrue(hits(p, [0, 1], None, base)["three"])
        self.assertFalse(hits(p, [0, 1], None, base)["first"])
        p["total"] = "under"
        self.assertEqual(hits(p, [1, 1], 2.25, base)["settlement"], "HALF_WIN")
        self.assertIsNone(hits(p, [1, 1], 2.0, base)["full_win"])

    def test_source_line_requires_both_prices(self):
        self.assertFalse(quoted({"B365>2.5": "2.0", "B365<2.5": ""}))
        self.assertFalse(quoted({"B365>2.5": "2.0", "B365<2.5": "inf"}))
        self.assertTrue(quoted({"B365>2.5": "1.9", "B365<2.5": "1.9"}))

    def test_only_same_first_pair_and_signed_anchor(self):
        first, author, anchor = pair()
        self.assertEqual(verify_pair(first, author, anchor)["model_hash"], "artifact")
        for field, value in [("revision", 2), ("track", "test"), ("previous_sha256", "earlier")]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                verify_pair(dict(first, **{field: value}), author, anchor)
        for field, value in [
            ("away", "different"),
            ("stage", "final"),
            ("kickoff_at", "2026-11-21T14:00:00Z"),
        ]:
            altered = deepcopy(author)
            altered["fixture"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                verify_pair(first, altered, anchor)
        with self.assertRaises(ValueError):
            verify_pair(first, author, dict(anchor, hash="wrong"))

    def test_model_snapshot_or_generation_after_deadline_rejected(self):
        for key, value in [
            ("model_observed_at", "2026-11-21T11:01:00Z"),
            ("generated_at", "2026-11-21T13:00:00Z"),
            ("matrix_hash", "different"),
        ]:
            first, author, anchor = pair()
            first["payload"]["current_model"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                verify_pair(first, author, anchor)

    def test_retries_and_probability_file_integrity(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            base = matrix(1, 1)
            a = seal_row(folder, {"id": "one"}, base, [0.3, -0.1], 0.25, 2.5)
            self.assertEqual(a, seal_row(folder, {"id": "one"}, base, [0.3, -0.1], 0.25, 2.5))
            file = folder / (a["matrix_file"] + ".npy")
            file.write_bytes(b"bad")
            with self.assertRaises(ValueError):
                seal_row(folder, {"id": "one"}, base, [0.3, -0.1], 0.25, 2.5)

    def test_summary_keeps_abstentions_and_separates_denominators(self):
        base = np.array([[0.1, 0.4], [0.2, 0.3]])
        x = decisions(base, 1.0)
        s0 = hits(x, [1, 0], 1.0, base)
        p = {"baseline": x, "candidate": x}
        report = summary(
            [{"status": "active"}, {"status": "unseen_team"}],
            [dict(picks=p, baseline=s0, candidate=s0, day="2024-01-01")],
            True,
        )
        self.assertEqual(
            (report["population"], report["paired"], report["three"]["n"], report["full_win"]["n"]),
            (2, 1, 1, 0),
        )
        self.assertEqual(report["baseline"]["error_paths"], {"second_or_third": 1})


if __name__ == "__main__":
    unittest.main()
