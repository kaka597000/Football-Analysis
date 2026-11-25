"""Regression coverage for registered J1 display-window review gates."""

from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

from research.analysis_freeze import digest
from research_program import display_window_review as review


def _freeze(payload, fixture, track, recorded_at, *, match_id=None):
    value = dict(schema="WCPP-ISSUED-FREEZE-1", match_id=match_id or fixture["match_id"],
                 fixture=fixture, payload=payload, track=track, stage="prematch",
                 recorded_at=recorded_at, revision=1, previous_sha256=None,
                 evaluation_slot="FIRST_PREMATCH")
    value["record_sha256"] = digest(value)
    return value


def _plan():
    specs = [
        dict(name="nov25", **{"from": "2026-11-25T18:00:00+08:00", "until": "2026-11-27T00:00:00+08:00"},
             expected=1, minimum_confirmed_scores=1, minimum_confirmed_wdl=1,
             minimum_nonpush_settled=1, minimum_strict_line_issues=1),
        dict(name="nov28_29", **{"from": "2026-11-27T00:00:00+08:00", "until": "2026-11-30T00:00:00+08:00"},
             expected=1, minimum_confirmed_scores=1, minimum_confirmed_wdl=1,
             minimum_nonpush_settled=1, minimum_strict_line_issues=1),
    ]
    fixtures, slates = {}, {"nov25": [], "nov28_29": []}
    for name, mid, kickoff in (("nov25", "espn:101", "2026-11-25T10:00:00+00:00"),
                               ("nov28_29", "espn:102", "2026-11-28T10:00:00+00:00")):
        f = dict(match_id=mid, home="espn:1", away="espn:2", cohort="jpn.1",
                 stage="Japanese J1 League", kickoff_at=kickoff)
        fixtures[mid] = f
        slates[name].append(mid)
    payload = dict(track="j1-comprehensive-windows-20261125", fixtures=fixtures,
                   identity=dict(version="J1-first-display-windows-20261125-1", competitions=["jpn.1"],
                                 slates=slates, protocol=dict(windows=specs),
                                 stages=[dict(stage="stage_1", targets=dict(first_exact_score=.2,
                                     three_exact_scores=.4, wdl=.65,
                                     actual_line_full_win_nonpush=.7, selected_three_primary_sweep=1.))]))
    shell = dict(schema="WCPP-ISSUED-FREEZE-1", match_id="j1-comprehensive-windows-20261125-plan:test",
                 fixture=dict(match_id="j1-comprehensive-windows-20261125-plan:test",
                              kickoff_at="2026-11-25T10:00:00+00:00", cutoff_at="2026-11-25T08:00:00+00:00"),
                 payload=payload, track=review.REGISTRATION_TRACK, stage="prematch",
                 recorded_at="2026-11-25T08:00:00+00:00", revision=1, previous_sha256=None,
                 evaluation_slot="FIRST_PREMATCH")
    shell["record_sha256"] = digest(shell)
    return shell


class DisplayWindowReviewTests(unittest.TestCase):
    def test_registered_complete_population_and_original_digest_required(self):
        plan = _plan()
        _, fixtures, slates, _ = review._registered(plan)
        self.assertEqual(set(fixtures), {"espn:101", "espn:102"})
        self.assertEqual([len(slates[k]) for k in review.WINDOW_NAMES], [1, 1])
        altered = dict(plan, recorded_at="2026-11-25T08:01:00+00:00")
        with self.assertRaisesRegex(ValueError, "checksum"):
            review._registered(altered)

    def test_unissued_publication_slot_is_explicit_and_never_selected(self):
        plan = _plan()
        fixture = plan["payload"]["fixtures"]["espn:101"]
        row = dict(match_id="espn:101", kickoff_at=fixture["kickoff_at"], authored=None,
                   pair=None, displayed_scores=[], wdl=None, total_line=None,
                   total_direction=None, ranking_probability=None,
                   unissued_reason="source issue failed before handoff")
        shell = _freeze(dict(fixtures={"espn:101": fixture},
                            identity=dict(parent=plan["record_sha256"], window="nov25",
                                           rows={"espn:101": row}, selected=[],
                                           body_sha256=hashlib.sha256(b"").hexdigest())),
                        dict(match_id="publication", kickoff_at=fixture["kickoff_at"],
                             cutoff_at="2026-11-25T09:00:00+00:00"),
                        review.PUBLICATION_TRACK + "nov25-registration", "2026-11-25T09:01:00+00:00",
                        match_id="publication")
        got = review._publication([shell], "nov25", plan["record_sha256"], ["espn:101"])
        self.assertEqual(got["payload"]["identity"]["rows"]["espn:101"]["unissued_reason"],
                         "source issue failed before handoff")
        bad = dict(row, unissued_reason="")
        shell["payload"]["identity"]["rows"]["espn:101"] = bad
        shell["record_sha256"] = digest({k:v for k,v in shell.items() if k != "record_sha256"})
        with self.assertRaisesRegex(ValueError, "unissued row"):
            review._publication([shell], "nov25", plan["record_sha256"], ["espn:101"])

    def test_authored_abstention_is_not_misclassified_as_unissued_or_ranked(self):
        plan = _plan()
        fixture = plan["payload"]["fixtures"]["espn:101"]
        row = dict(match_id="espn:101", kickoff_at=fixture["kickoff_at"],
                   authored=dict(id="author", sha256="a"*64), pair=dict(id="pair", sha256="b"*64),
                   displayed_scores=[], wdl="home", total_line=2.5, total_direction="under",
                   ranking_probability=None)
        shell = _freeze(dict(fixtures={"espn:101": fixture},
                            identity=dict(parent=plan["record_sha256"], window="nov25",
                                          rows={"espn:101": row}, selected=[],
                                          body_sha256=hashlib.sha256(b"").hexdigest())),
                        dict(match_id="publication", kickoff_at=fixture["kickoff_at"],
                             cutoff_at="2026-11-25T09:00:00+00:00"),
                        review.PUBLICATION_TRACK + "nov25-registration", "2026-11-25T09:01:00+00:00",
                        match_id="publication")
        got = review._publication([shell], "nov25", plan["record_sha256"], ["espn:101"])
        self.assertIsNone(got["payload"]["identity"]["rows"]["espn:101"]["ranking_probability"])
        self.assertEqual(got["payload"]["identity"]["selected"], [])

    def test_selected_sweep_requires_all_three_unique_primary_scores(self):
        primary_hit = {f"m{i}": dict(top1_hit=True, top3_hit=True) for i in range(3)}
        passed = review._selected_sweep(["m0", "m1", "m2"], primary_hit)
        self.assertTrue(passed["hit"])
        self.assertEqual((passed["hits"], passed["misses"], passed["confirmed"], passed["eligible"]), (3, 0, 3, 3))

        backup_only = dict(primary_hit, m0=dict(top1_hit=False, top3_hit=True))
        failed = review._selected_sweep(["m0", "m1", "m2"], backup_only)
        self.assertFalse(failed["hit"])
        self.assertEqual(failed["status"], "CONFIRMED_PRIMARY_MISS")
        self.assertEqual(failed["alternate_only_hits"], 1)
        self.assertTrue(failed["alternate_sweep"])

        pending = dict(primary_hit, m2=dict(top1_hit=None, top3_hit=None))
        self.assertIsNone(review._selected_sweep(["m0", "m1", "m2"], pending)["hit"])
        short = review._selected_sweep(["m0", "m1"], primary_hit)
        self.assertIsNone(short["hit"])
        self.assertEqual((short["hits"], short["misses"], short["eligible"], short["required"]), (2, 0, 2, 3))

    def test_official_stage_requires_same_window_stage_in_both_windows(self):
        both = {"nov25": {"_window_phase_candidates": {"stage_1": True}},
                "nov28_29": {"_window_phase_candidates": {"stage_1": True}}}
        self.assertTrue(review._official_phases(both)[0]["pass_"])
        one_only = {"nov25": {"_window_phase_candidates": {"stage_1": True}},
                    "nov28_29": {"_window_phase_candidates": {"stage_1": False}}}
        self.assertFalse(review._official_phases(one_only)[0]["pass_"])
        self.assertTrue(review._stage_thresholds_met(True, dict(a=True, b=True)))
        self.assertFalse(review._stage_thresholds_met(True, dict(a=True, b=False)))

    def test_all_three_top1_hits_can_complete_the_same_stage_in_both_windows(self):
        selected = ["m0", "m1", "m2"]
        rows = {mid: dict(top1_hit=True, top3_hit=True) for mid in selected}
        sweep = review._selected_sweep(selected, rows)
        self.assertIs(sweep["hit"], True)
        self.assertEqual((sweep["hits"], sweep["misses"], sweep["eligible"]), (3, 0, 3))

        checks = dict(first_exact_score=True, three_exact_scores=True, wdl=True,
                      actual_line_full_win=True, selected_three_primary_sweep=sweep["hit"] is True)
        self.assertTrue(review._stage_thresholds_met(True, checks))
        both_windows = {
            "nov25": {"_window_phase_candidates": {"stage_1": review._stage_thresholds_met(True, checks)}},
            "nov28_29": {"_window_phase_candidates": {"stage_1": review._stage_thresholds_met(True, checks)}},
        }
        official = review._official_phases(both_windows)
        self.assertEqual(official[0]["per_window_thresholds_met"], {"nov25": True, "nov28_29": True})
        self.assertTrue(official[0]["pass_"])

    def test_same_fixture_market_accepts_current_seven_row_missing_quote_schema(self):
        per_match = [
            dict(match_id=f"espn:{i}", kickoff_at="2026-11-25T10:00:00+00:00",
                 actual_result=None, actual_result_source_sha256=None,
                 confirmed_first_score=False,
                 quote=dict(raw_source_verified=False, strict=False))
            for i in range(7)
        ]
        result = review._market_comparisons(per_match, {})
        self.assertEqual(set(result), {"first_frozen_rows", "channel_confirmed_rows"})
        for comparison in result.values():
            self.assertEqual(comparison["population"], 7)

    def test_current_empty_window_keeps_full_denominator_and_cannot_pass(self):
        plan = _plan()
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            state.mkdir()
            report = review.review(plan, ledger_root=Path(tmp) / "empty-ledger",
                                   result_state=state, evidence_root=state,
                                   at="2026-11-25T09:30:00+00:00")
        self.assertEqual((report["combined"]["registered_n"], report["combined"]["completed_n"],
                          report["combined"]["pending_n"], report["combined"]["phase_pass"]), (2, 0, 2, False))
        self.assertFalse(report["combined"]["phases"][0]["pass_"])
        for window in report["windows"].values():
            self.assertEqual(window["completed_n"], 0)
            self.assertEqual(window["registered_n"], 1)
            self.assertFalse(window["phase_pass"])
            self.assertIn("UNSETTLED_REGISTERED_FIXTURES", window["blockers"])
            self.assertIn("INDEPENDENT_FIRST_RECEIPT_OR_READBACK_UNCERTIFIED", window["blockers"])

    def test_unquoted_line_time_never_counts_strict_and_five_way_keeps_observed_only(self):
        outcomes = [
            review._five_way(dict(line=2.25), (2, 1), "大"),
            review._five_way(dict(line=2.25), (1, 1), "小"),
            review._five_way(dict(line=2.0), (1, 1), "大"),
            review._five_way(dict(line=2.25), (1, 1), "大"),
            review._five_way(dict(line=2.25), (0, 1), "大"),
        ]
        self.assertEqual(outcomes, ["FULL_WIN", "HALF_WIN", "PUSH", "HALF_LOSS", "FULL_LOSS"])
        from collections import Counter
        counts = Counter()
        for outcome in outcomes:
            review._count_settlement(counts, outcome)
        self.assertEqual({k: counts[k] for k in review.SETTLEMENTS}, dict(zip(review.SETTLEMENTS, [1]*5)))
        self.assertIsNone(review._five_way(dict(line=2.5), None, "大"))

    def test_actual_quote_is_bound_to_original_raw_source_prices_and_event(self):
        fixture = dict(match_id="espn:101", cohort="jpn.1", home="espn:1", away="espn:2",
                       kickoff_at="2026-11-25T10:00:00+00:00", cutoff_at="2026-11-25T09:30:00+00:00")
        event = dict(header=dict(competitions=[dict(date=fixture["kickoff_at"], competitors=[
            dict(homeAway="home", team=dict(id="1")), dict(homeAway="away", team=dict(id="2"))])]),
            odds=[dict(provider=dict(id="100", name="DraftKings"), overUnder=2.5,
                       overOdds=110, underOdds=-110,
                       homeTeamOdds=dict(moneyLine=120), drawOdds=dict(moneyLine=220),
                       awayTeamOdds=dict(moneyLine=250))])
        body = json.dumps(event).encode()
        quote = dict(source_sha256=hashlib.sha256(body).hexdigest(),
                     source_url="https://site.api.espn.com/apis/site/v2/sports/soccer/jpn.1/summary?event=101",
                     query_time="2026-11-25T09:10:00+00:00", quoted_at="2026-11-25T09:05:00+00:00",
                     bookmaker="DraftKings", line=2.5,
                     over_odds=2.1, under_odds=1.9090909090909092)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with tarfile.open(root / "evidence.tar.gz", "w:gz") as archive:
                import io
                info = tarfile.TarInfo("summary.json")
                info.size = len(body)
                archive.addfile(info, io.BytesIO(body))
            cache = {}
            ok, reason, raw_clock = review._quote_source(quote, fixture, root, cache,
                                                        dict(home=2.2, draw=3.2, away=3.5))
            self.assertTrue(ok, reason)
            self.assertIsNone(raw_clock)
            self.assertEqual(review._market_clock_match(quote, raw_clock),
                             (False, "RAW_BOOKMAKER_MARKET_CLOCK_MISSING"))
            # An envelope clock cannot borrow the event's kickoff or an unrelated response clock.
            self.assertEqual(review._market_clock_match(quote, "2026-11-25T09:04:00+00:00"),
                             (False, "RAW_BOOKMAKER_MARKET_CLOCK_CONFLICT"))
            timed = dict(event)
            timed["odds"] = [dict(event["odds"][0], lastUpdated="2026-11-25T09:05:00+00:00")]
            timed_body = json.dumps(timed).encode()
            with tarfile.open(root / "timed.tar.gz", "w:gz") as archive:
                import io
                info = tarfile.TarInfo("summary.json")
                info.size = len(timed_body)
                archive.addfile(info, io.BytesIO(timed_body))
            timed_quote = dict(quote, source_sha256=hashlib.sha256(timed_body).hexdigest())
            ok, _, raw_clock = review._quote_source(timed_quote, fixture, root, {},
                                                    dict(home=2.2, draw=3.2, away=3.5))
            self.assertTrue(ok)
            self.assertEqual(review._market_clock_match(timed_quote, raw_clock), (True, None))
            broken = dict(quote, over_odds=2.2)
            self.assertEqual(review._quote_source(broken, fixture, root, cache)[1],
                             "QUOTE_RAW_TOTAL_LINE_OR_PRICES_DIFFER")


if __name__ == "__main__":
    unittest.main()
