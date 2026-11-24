import copy
import unittest
from datetime import timedelta
from tempfile import TemporaryDirectory
from pathlib import Path

from research.analysis_freeze import aware
from prepare_clocked import reconstruct
from prepare_history import seal
from settle import assess, metrics
import numpy as np


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.url = "https://api.the-odds-api.com/v4/sports/soccer_epl/odds"
        quote, capture, cutoff = [f"2020-03-01T10:{m}:00Z" for m in ("10", "11", "12")]
        self.q = dict(bookmaker="Book", query_time=capture, quoted_at=quote, source_url=self.url,
                      source_event_id="native1", source_file_sha256="a" * 64, line=2.5,
                      over_odds=1.95, under_odds=1.85)
        self.f = dict(match_id="espn:1", cutoff_at=cutoff, kickoff_at="2020-03-01T11:00:00Z",
                      total_quote=self.q, source_ids={"the_odds": "native1"})
        self.names = {"home": {"host"}, "away": {"visitors"}}
        self.receipts = [("/v4/sports/soccer_epl/odds", aware(capture), aware(capture), "b" * 64)]
        self.odds = [dict(id="native1", sport_key="soccer_epl", commence_time=self.f["kickoff_at"],
                          home_team="Host", away_team="Visitors", bookmakers=[
            dict(key="book", title="Book", markets=[
                dict(key="h2h", last_update=quote, outcomes=[
                    dict(name="Host", price=2.), dict(name="Draw", price=3.2), dict(name="Visitors", price=3.8)]),
                dict(key="totals", last_update=quote, outcomes=[
                    dict(name="Over", point=2.5, price=1.95), dict(name="Under", point=2.5, price=1.85)])])])]

    def test_same_provider_book_period_and_original_total(self):
        original = copy.deepcopy(self.f)
        r = reconstruct(self.f, self.odds, self.receipts, self.names)
        self.assertEqual(self.f, original)
        self.assertEqual(r["h2h"], {"home": 2.0, "draw": 3.2, "away": 3.8})
        self.assertEqual(r["receipts"], ["b" * 64])
        self.assertEqual(r["total"]["line"], 2.5)

    def test_collection_finishing_after_cutoff_or_missing_receipt(self):
        path, queried, _, h = self.receipts[0]
        for rec in ([], [(path, queried, aware(self.f["cutoff_at"]) + timedelta(seconds=1), h)]):
            with self.subTest(rec=rec), self.assertRaisesRegex(ValueError, "RECEIPT"):
                reconstruct(self.f, self.odds, rec, self.names)

    def test_stale_or_post_capture_price_cannot_be_a_forecast(self):
        for clock in ("2020-03-01T01:00Z", "2020-03-01T10:11:30Z", None):
            raw = copy.deepcopy(self.odds)
            raw[0]["bookmakers"][0]["markets"][0]["last_update"] = clock
            with self.subTest(clock=clock), self.assertRaises(ValueError):
                reconstruct(self.f, raw, self.receipts, self.names)

    def test_rejects_conflicting_identity_or_market(self):
        for key, value in (("id", "other"), ("home_team", "Visitors"), ("sport_key", "soccer_italy"),
                           ("commence_time", "2020-03-02T10:00Z")):
            raw = copy.deepcopy(self.odds)
            raw[0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                reconstruct(self.f, raw, self.receipts, self.names)
        raw = copy.deepcopy(self.odds)
        raw[0]["bookmakers"][0]["markets"][1]["outcomes"][0]["price"] = 1.55
        with self.assertRaisesRegex(ValueError, "DISAGREES"):
            reconstruct(self.f, raw, self.receipts, self.names)

    def test_requires_both_markets_in_actual_bytes_and_all_distinct_sides(self):
        for remove in (0, 1):
            raw = copy.deepcopy(self.odds)
            raw[0]["bookmakers"][0]["markets"].pop(remove)
            with self.assertRaisesRegex(ValueError, "RAW_MARKET"):
                reconstruct(self.f, raw, self.receipts, self.names)
        self.odds[0]["bookmakers"][0]["markets"][0]["outcomes"][2]["name"] = "Host"
        with self.assertRaisesRegex(ValueError, "PRICE"):
            reconstruct(self.f, self.odds, self.receipts, self.names)

    def test_prediction_writer_rejects_labels_and_overwrite(self):
        with TemporaryDirectory() as t:
            out = Path(t) / "trial"
            with self.assertRaisesRegex(ValueError, "Target score"):
                seal([{"label": (2, 0)}], 0, out)
            with self.assertRaises(FileExistsError):
                seal([], 0, out)

    def test_only_full_wins_score_and_push_stays_out_of_denominator(self):
        m = np.array([[.34, .11], [.32, .23]])
        h = assess(m, [1, 1], 2.)
        self.assertEqual(h["settlement"], "PUSH")
        self.assertIsNone(h["full_win"])
        s = metrics([dict(baseline=h, candidate=h)])
        self.assertEqual(s["binary"]["top1"]["n"], 1)
        self.assertEqual(s["binary"]["full_win"]["n"], 0)
