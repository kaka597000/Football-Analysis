from datetime import datetime, timedelta, timezone
import unittest

from research_program.team_market_pilot import mainlines


class EnglandPilotMarkets(unittest.TestCase):
    def test_same_book_panels_and_quote_time_are_required(self):
        cutoff = datetime.now(timezone.utc)
        stamp = cutoff.isoformat()
        h2h = dict(
            key="h2h",
            last_update=stamp,
            outcomes=[
                {"name": "H", "price": 1.8},
                {"name": "Draw", "price": 3.8},
                {"name": "A", "price": 4.3},
            ],
        )
        total = dict(
            key="totals",
            last_update=stamp,
            outcomes=[
                {"name": "Over", "point": 2.75, "price": 1.89},
                {"name": "Under", "point": 2.75, "price": 1.99},
            ],
        )
        wrong_line = dict(total, outcomes=[total["outcomes"][0], dict(total["outcomes"][1], point=3.0)])
        stale = dict(total, last_update=(cutoff - timedelta(hours=2)).isoformat())
        event = {
            "id": "event",
            "bookmakers": [
                {"key": "one", "markets": [h2h]},
                {"key": "another", "markets": [total]},
                {"key": "duplicate", "markets": [h2h, total, total]},
                {"key": "stale", "markets": [h2h, stale]},
                {"key": "split", "markets": [h2h, wrong_line]},
                {"key": "complete", "markets": [h2h, total]},
            ],
        }
        meta = dict(captured_at=stamp, source_url="https://example.org/synthetic", source_sha256="digest")
        f = dict(match_id="fixture", provider_home="H", provider_away="A")
        result = mainlines(event, meta, f, cutoff)
        self.assertEqual(set(result), {"complete"})
        self.assertEqual(result["complete"]["h2h"], {"home": 1.8, "draw": 3.8, "away": 4.3})
        self.assertEqual(result["complete"]["total"], {"line": 2.75, "over": 1.89, "under": 1.99})


if __name__ == "__main__":
    unittest.main()
