import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

import numpy as np

from data_sources.team_market_quotes import load_targets, normalize_event
from research.analysis_freeze import digest, verify
from research_program import team_market_candidate as study


def observation(family, p, line=0.5):
    btts = family == "btts"
    return {
        "market": "btts" if btts else "team_total",
        "team": None if btts else family,
        "line": None if btts else line,
        "decimal_odds": dict(zip(("yes", "no") if btts else ("over", "under"), (1 / p, 1 / (1 - p)))),
    }


class TeamMarketCandidate(unittest.TestCase):
    def setUp(self):
        self.base = np.outer([0.475, 0.405, 0.12], [0.505, 0.395, 0.1])

    def test_single_binary_closed_form_partial_and_swap(self):
        for family in ("btts", "home", "away"):
            q = observation(family, 0.82)
            m, (feature,) = study.adjust(self.base, [q])
            p0 = feature["original_probability"]
            odds = ((0.82 / 0.18) * (p0 / (1 - p0))) ** 0.5
            self.assertAlmostEqual(feature["adjusted_probability"], odds / (1 + odds))
            self.assertAlmostEqual(m.sum(), 1)
        home = observation("home", 0.9)
        away = observation("away", 0.2)
        m, _ = study.adjust(self.base, [home, away, observation("btts", 0.32)])
        mirror, _ = study.adjust(
            self.base.T, [dict(home, team="away"), dict(away, team="home"), observation("btts", 0.32)]
        )
        np.testing.assert_allclose(m, mirror.T, atol=1e-10)
        self.assertGreater(m[1:, :].sum(), self.base[1:, :].sum())
        self.assertLess(m[:, 1:].sum(), self.base[:, 1:].sum())

    def test_fractional_coverage_contradictory_marginals_and_support(self):
        unchanged, features = study.adjust(self.base, [])
        np.testing.assert_array_equal(unchanged, self.base)
        self.assertEqual(features, [])
        self.assertIsNone(study.market_key(observation("home", 0.7, 1.25)))
        m, (high,) = study.adjust(self.base, [observation("home", 0.75, 1.5)])
        self.assertAlmostEqual(high["adjusted_probability"], m[2:, :].sum())
        m = self.base.copy()
        m[0, 0] = 0
        m /= m.sum()
        m, features = study.adjust(
            m,
            [
                observation("home", 0.01),
                observation("home", 0.01, 1.5),
                observation("away", 0.01),
                observation("btts", 0.99),
            ],
        )
        self.assertTrue(np.isfinite(m).all() and abs(m.sum() - 1) < 1e-12 and m[0, 0] == 0)
        self.assertEqual([f["weight"] for f in features], [0.5, 0.5, 1.0, 1.0])

    def test_raw_to_first_issue_report_and_tampered_input(self):
        now = datetime.now(timezone.utc)
        stamp, ko = (now - timedelta(seconds=4)).isoformat(), (now + timedelta(days=2)).isoformat()
        mid = "espn:synthetic-test"
        identity = dict(
            match_id=mid,
            cohort="bra.1",
            competition="bra.1",
            cutoff_at=stamp,
            kickoff_at=ko,
            home_name="Home",
            away_name="Away",
            identity_verified=True,
            score_basis="regulation_90",
        )
        event = dict(
            id="a" * 32,
            sport_key="soccer_brazil_campeonato",
            home_team="Home",
            away_team="Away",
            commence_time=ko,
        )

        def write(path, value):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(study.encode(value))

        def signed(**content):
            return dict(content, record_sha256=digest(content))

        with tempfile.TemporaryDirectory() as temp:
            data = Path(temp)
            batch = data / study.DEFAULT_BATCH
            market = data / "market/runs/initial/odds.json"
            write(market, [event])
            sha = hashlib.sha256(market.read_bytes()).hexdigest()
            quote = dict(
                match_id=mid,
                provider="the-odds-api",
                provider_match_id=event["id"],
                source_sha256=sha,
                bookmaker="example",
            )
            payload = dict(
                fixtures=[identity],
                markets={mid: quote},
                binding={"market_archive": "market/runs/initial"},
                sources={"odds": {"sha256": sha}},
            )
            plan = signed(payload=payload)
            parent = data / study.PARENT
            write(parent / "plan.json", plan)
            base_payload = dict(
                plan_sha256=plan["record_sha256"],
                market=quote,
                arms={"market_baseline": study.picks(self.base, 1.5)},
            )
            base = signed(
                evaluation_slot="FIRST_PREMATCH",
                match_id=mid,
                recorded_at=stamp,
                fixture=identity,
                payload=base_payload,
            )
            write(parent / "freezes/first.json", base)
            write(batch / "protocol.json", dict(version=study.VERSION, parent_plan=plan["record_sha256"]))
            write(
                batch.parent / "registration.json",
                dict(
                    forward_plan_parent=plan["record_sha256"],
                    forward_ids=[mid],
                    forward_arms={"team_markets": list(study.ARMS)},
                ),
            )
            (data / "normalized-results.jsonl").touch()
            run = data / "market/runs/extra"
            (target,) = load_targets(data, parent / "plan.json")
            write(run / "targets.json", [target])
            props = []
            for name, p in [("Home", 0.94), ("Away", 0.25)]:
                for side, price in [("Over", 1 / p), ("Under", 1 / (1 - p))]:
                    props.append(dict(name=side, description=name, price=price, point=0.5))
            # Missing price for a second home threshold never forms a complete source pair.
            props.append(dict(name="Over", description="Home", price=1.95, point=1.5))
            markets = [
                dict(key="team_totals", last_update=stamp, outcomes=props),
                dict(
                    key="btts",
                    last_update=stamp,
                    outcomes=[{"name": "Yes", "price": 3.2}, {"name": "No", "price": 1.3}],
                ),
            ]
            raw_event = dict(event, bookmakers=[dict(key="example", markets=markets)])
            write(run / "raw/event.json", raw_event)
            meta = dict(
                match_id=mid,
                status="OK",
                requested_at=stamp,
                captured_at=stamp,
                source_file="raw/event.json",
                source_sha256=hashlib.sha256((run / "raw/event.json").read_bytes()).hexdigest(),
                source_url="https://example.org/football/synthetic-test",
            )
            write(run / "requests.json", [meta])
            rows, rejects = normalize_event(target, raw_event, meta)
            self.assertEqual(len(rows), 3)
            self.assertTrue(rejects)
            write(run / "quotes.json", rows)
            # Test-only synthetic evidence stays inside this throwaway directory.
            report = study.issue(data, batch, run)
            self.assertEqual(report["status_counts"], {"ISSUED": 1})
            self.assertEqual(report, study.issue(data, batch, run))
            record = verify(study.read(next((batch / "records").glob("*/*.json"))))
            a, b = (record["payload"]["arms"][k] for k in study.ARMS)
            self.assertNotEqual(a["primary"], b["primary"])
            self.assertNotEqual(a["matrix_sha256"], b["matrix_sha256"])
            self.assertEqual(len(record["payload"]["feature_probabilities"]), 3)
            self.assertEqual(study.replay(data, batch)["replayed_grids"], 2)
            self.assertEqual(study.review(data, batch)["settled_first_issues"], 0)
            # Switching the labeled team in the ledger without editing the real raw
            # provider record must fail, even if the quoted price still looks valid.
            side = next(r for r in rows if r["team"] == "home")
            side["team"] = "away"
            write(run / "quotes.json", rows)
            with self.assertRaisesRegex(ValueError, "original raw event"):
                study.replay(data, batch)


if __name__ == "__main__":
    unittest.main()
