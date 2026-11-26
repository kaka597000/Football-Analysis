"""Regression coverage for registered J1 display-window review gates."""

from datetime import datetime, timezone
import gzip
import hashlib
import json
from copy import deepcopy
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

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
    def test_original_chronological_body_can_differ_from_fixture_id_order(self):
        body = "赛事 102\n第二个ID先开球\n赛事 101\n第一个ID后开球"
        identity = dict(rows={mid: {"authored": True} for mid in ("101", "102")},
                        body_sha256=hashlib.sha256(body.encode("utf-8")).hexdigest())
        records = {"101": "赛事 101\n第一个ID后开球", "102": "赛事 102\n第二个ID先开球"}
        with patch("research.display_delivery.delivery_request", side_effect=lambda text: {"text": text}):
            actual = review._batch_text_audit(body, identity, records)
            reordered = review._batch_text_audit(records["101"] + "\n" + records["102"], identity, records)
        self.assertTrue(actual["original_blocks_present_once_in_order"])
        self.assertFalse(reordered["original_blocks_present_once_in_order"])

    def test_strict_total_settles_only_for_the_same_confirmed_display_row(self):
        mids = ("espn:101", "espn:102")
        fixture = {mid: dict(kickoff_at="2026-12-05T06:00:00+00:00") for mid in mids}
        stamp = "2026-12-05T05:00:00+00:00"
        quote = dict(line=2.5, quoted_at=stamp)
        records = {mid: dict(fixture=fixture[mid], payload=dict(total_quote=quote, total_direction="大"))
                   for mid in mids}
        results = {mid: dict(home_goals=2, away_goals=1) for mid in mids}
        displays = [dict(match_id=mid, verified_completed=True, track="comprehensive", display_status="ISSUED",
                         top1_issued=i == 0, top3_issued=i == 0, top1_hit=True if i == 0 else None,
                         top3_hit=True if i == 0 else None, actual_kickoff_at=fixture[mid]["kickoff_at"])
                    for i, mid in enumerate(mids)]
        spec = dict(minimum_confirmed_scores=2, minimum_confirmed_wdl=2, minimum_strict_line_issues=2,
                    minimum_nonpush_settled=2)
        with patch.object(review, "quote_audit", side_effect=lambda _: {"eligible": True, "reasons": []}), \
                patch.object(review, "_quote_source", return_value=(True, None, stamp)):
            result, _, _ = review._window_report("test", spec, fixture, records, displays, None, results)
        counted = result["actual_bookmaker_quote"]["five_way_settlements"]
        self.assertEqual(counted["FULL_WIN"], 1)
        self.assertEqual(result["actual_bookmaker_quote"]["observed_price_five_way_settlements"]["FULL_WIN"], 2)

    def test_registered_complete_population_and_original_digest_required(self):
        plan = _plan()
        _, fixtures, slates, _ = review._registered(plan)
        self.assertEqual(set(fixtures), {"espn:101", "espn:102"})
        self.assertEqual([len(slates[k]) for k in review.WINDOW_NAMES], [1, 1])
        altered = dict(plan, recorded_at="2026-11-25T08:01:00+00:00")
        with self.assertRaisesRegex(ValueError, "checksum"):
            review._registered(altered)

    def test_new_series_reviews_its_own_two_named_windows(self):
        old = _plan()
        payload = deepcopy(old['payload'])
        identity = payload['identity']
        old_names = tuple(identity['slates'])
        new_names = ('opening_round', 'following_round')
        identity['version'] = 'J1-first-display-windows-20261126-1'
        identity['slates'] = {new: identity['slates'][previous]
                             for new, previous in zip(new_names, old_names, strict=True)}
        for spec, name in zip(identity['protocol']['windows'], new_names, strict=True):
            spec['name'] = name
        payload['track'] = 'j1-comprehensive-windows-20261126'
        plan = _freeze(payload, old['fixture'], payload['track'] + '-registration', old['recorded_at'])
        with tempfile.TemporaryDirectory() as tmp:
            result = review.review(plan, ledger_root=tmp, result_state=tmp, evidence_root=tmp,
                                   at='2026-11-25T09:30:00+00:00')
        self.assertEqual(tuple(result['windows']), new_names)
        self.assertEqual(result['combined']['pending_n'], 2)
        self.assertFalse(result['combined']['phase_pass'])

        identity['protocol']['windows'][1]['from'] = '2026-11-27T01:00:00+08:00'
        gap = _freeze(payload, old['fixture'], payload['track'] + '-registration', old['recorded_at'])
        with self.assertRaisesRegex(ValueError, 'consecutive'):
            review._registered(gap)

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

    def test_observed_prices_only_pair_in_the_frozen_audit(self):
        from research_program import all_scope_model as scores

        mid = "espn:101"
        quote = dict(line=2.5, over_odds=1.9, under_odds=1.95)
        h2h = dict(home=2.1, draw=3.2, away=3.5)
        matrix = scores._full_market(tuple(h2h[side] for side in scores.SIDES), 2.5, (1.9, 1.95))
        record = dict(fixture=dict(total_quote=quote),
                      payload=dict(total_line=2.5, primary="2-1", displayed_scores=["2-1", "1-1", "1-0"],
                                   wdl="主胜", total_direction="大",
                                   market_snapshot=dict(quote=quote, h2h=h2h, forecast=scores.pick(matrix, 2.5))))
        item = dict(match_id=mid, kickoff_at="2026-11-25T10:00:00+00:00", actual_result="2-1",
                    actual_result_source_sha256="settled", confirmed_first_score=True,
                    quote=dict(raw_source_verified=True, strict=False))
        observed = review._market_comparisons([item], {mid: record})
        self.assertEqual(observed["first_frozen_rows"]["paired"]["score_top1"]["eligible"], 1)
        self.assertTrue(all(v["eligible"] == 0 for v in observed["channel_confirmed_rows"]["paired"].values()))
        item["quote"]["strict"] = True
        verified = review._market_comparisons([item], {mid: record})
        self.assertTrue(all(v["eligible"] == 1 for v in verified["channel_confirmed_rows"]["paired"].values()))

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
        game = dict(id="101", date=fixture["kickoff_at"],
                    status=dict(type=dict(state="pre", completed=False)),
                    competitors=[dict(homeAway="home", team=dict(id="1")),
                                 dict(homeAway="away", team=dict(id="2"))])
        event = dict(header=dict(id="101", league=dict(slug="jpn.1"), competitions=[game]),
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
            wrong_game = dict(timed, header=dict(timed["header"], competitions=[dict(game, id="103")]))
            wrong_body = json.dumps(wrong_game).encode()
            wrong_sha = hashlib.sha256(wrong_body).hexdigest()
            cache[wrong_sha] = (wrong_body, "timed.tar.gz", "wrong-game.json")
            self.assertEqual(review._quote_source(dict(timed_quote, source_sha256=wrong_sha), fixture, root, cache)[1],
                             "QUOTE_RAW_FIXTURE_OR_SOURCE_DATE_MISMATCH")

    def test_conflicting_bookmaker_clocks_do_not_verify(self):
        stamp = "2026-11-25T09:05:00+00:00"
        clocks = dict(lastUpdated=stamp, last_update="2026-11-25T17:05:00+08:00")
        self.assertEqual(review._market_clock_match(dict(quoted_at=stamp), review._raw_market_clock(clocks)),
                         (True, None))
        clocks["last_update"] = "2026-11-25T17:04:00+08:00"
        self.assertEqual(review._market_clock_match(dict(quoted_at=stamp), review._raw_market_clock(clocks)),
                         (False, "RAW_BOOKMAKER_MARKET_CLOCK_CONFLICT"))


class KickoffEvidenceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.fixture = dict(match_id="espn:101", competition="jpn.1", cohort="jpn.1",
                            home="espn:11", away="espn:22", kickoff_at="2026-11-25T10:00:00Z")
        self.now = "2026-11-25T12:30:00Z"
        self.report = dict(match_id="espn:101", competition="jpn.1", home="espn:11", away="espn:22",
                           identity_verified=True, actual_kickoff_at="2026-11-25T19:02:13+09:00",
                           observed_at="2026-11-25T12:00:00Z", raw_file="summary.json",
                           source_url="https://site.api.espn.com/apis/site/v2/sports/soccer/jpn.1/summary?event=101")
        self.start = dict(type={"id": "80", "type": "kickoff", "text": "Kickoff"},
                          period={"number": 1}, clock={"value": 0.0}, wallclock="2026-11-25T10:02:13Z")
        self.game = dict(id="101", date=self.fixture["kickoff_at"], status={"type": {"state": "post"}},
                         competitors=[{"homeAway": "away", "team": {"id": "22"}},
                                      {"homeAway": "home", "team": {"id": "11"}}])
        self.raw = dict(header={"id": "101", "league": {"slug": "jpn.1"}, "competitions": [self.game]},
                        wallclockAvailable=True, keyEvents=[self.start,
                            dict(type={"type": "kickoff"}, period={"number": 2}, clock={"value": 2700},
                                 wallclock="2026-11-25T11:03:43Z")])

    def write_source(self, source=None, *, compress=False):
        body = json.dumps(self.raw if source is None else source).encode()
        if compress:
            body = gzip.compress(body)
        (self.root / self.report["raw_file"]).write_bytes(body)
        self.report["source_sha256"] = hashlib.sha256(body).hexdigest()

    def verify(self, report=None):
        mid = self.fixture["match_id"]
        return review._validate_kickoff_reports({mid: report or self.report}, {mid},
                                                {mid: self.fixture}, self.root, self.now)

    def test_actual_kickoff_is_reproduced_from_compressed_or_plain_event_source(self):
        for compress in (False, True):
            with self.subTest(compress=compress):
                self.write_source(compress=compress)
                verified = self.verify()["espn:101"]
                self.assertEqual(verified["actual_kickoff_at"], "2026-11-25T10:02:13+00:00")
                self.assertEqual(verified["source_sha256"], self.report["source_sha256"])
                self.assertEqual(self.report["actual_kickoff_at"], "2026-11-25T19:02:13+09:00")

    def test_actual_start_may_precede_scheduled_kickoff(self):
        self.start["wallclock"] = self.report["actual_kickoff_at"] = "2026-11-25T09:59:45Z"
        self.write_source()
        self.assertEqual(self.verify()["espn:101"]["actual_kickoff_at"], "2026-11-25T09:59:45+00:00")

    def test_scheduled_or_unscoped_start_fields_do_not_prove_actual_kickoff(self):
        self.raw.update(keyEvents=[], firstHalfStart=self.report["actual_kickoff_at"],
                        commentary=[{"text": "MatchStarted", "time": {"value": 0},
                                     "wallclock": self.report["actual_kickoff_at"]}],
                        httpDate=self.report["actual_kickoff_at"])
        self.game["date"] = self.report["actual_kickoff_at"]
        self.write_source()
        with self.assertRaisesRegex(ValueError, "one first-period kickoff"):
            self.verify()

    def test_only_typed_first_period_zero_clock_with_zoned_wallclock_is_accepted(self):
        changes = [
            {"type": {"type": "goal", "text": "MatchStarted"}},
            {"type": {"id": "94", "type": "kickoff"}}, {"period": {"number": 2}},
            {"period": {"number": True}}, {"clock": {"value": 1}},
            {"clock": {"value": False}}, {"clock": {"value": "0"}}, {"shootout": True},
            {"wallclock": None, "timestamp": self.start["wallclock"]},
            {"wallclock": "2026-11-25T10:02:13"},
        ]
        for change in changes:
            with self.subTest(change=change):
                self.write_source(dict(self.raw, keyEvents=[dict(self.start, **change)]))
                with self.assertRaises(ValueError):
                    self.verify()

    def test_event_league_and_oriented_team_identity_are_bound(self):
        changes = [
            (("header", "id"), "102"), (("header", "competitions", 0, "id"), "102"),
            (("header", "league", "slug"), "jpn.2"),
            (("header", "competitions", 0, "competitors", 0, "team", "id"), "99"),
            (("header", "competitions", 0, "competitors", 0, "homeAway"), "home"),
            (("header", "competitions", 0, "competitors"), [
                {"homeAway": "home", "team": {"id": "22"}},
                {"homeAway": "away", "team": {"id": "11"}}]),
            (("header", "competitions"), [self.game, self.game]),
        ]
        for keys, value in changes:
            with self.subTest(keys=keys):
                doc = deepcopy(self.raw)
                obj = doc
                for key in keys[:-1]:
                    obj = obj[key]
                obj[keys[-1]] = value
                self.write_source(doc)
                with self.assertRaises(ValueError):
                    self.verify()

    def test_only_registered_public_espn_summary_url_is_accepted(self):
        self.write_source()
        original = self.report["source_url"]
        invalid = (original.replace("https:", "http:"), original.replace(".espn.com", ".example.com"),
                   original.replace("jpn.1", "jpn.2"), original.replace("summary", "scoreboard"),
                   original.replace("=101", "=102"), original + "&event=102", original + "&event=")
        for url in invalid:
            with self.subTest(url=url):
                self.report["source_url"] = url
                with self.assertRaisesRegex(ValueError, "summary URL"):
                    self.verify()

    def test_claim_capture_and_later_play_chronology_are_checked(self):
        original = self.report.copy()
        for key, value in (("actual_kickoff_at", self.game["date"]),
                           ("actual_kickoff_at", "2026-11-25T10:02:13"),
                           ("observed_at", "2026-11-25T10:01:00Z"),
                           ("observed_at", "2026-11-25T13:00:00Z"),
                           ("observed_at", "2026-11-25T12:00:00")):
            with self.subTest(key=key, value=value):
                self.report = dict(original, **{key: value})
                self.write_source()
                with self.assertRaises(ValueError):
                    self.verify()
        self.report = original
        for later in ("2026-11-25T10:01:00Z", "2026-11-25T12:10:00Z"):
            with self.subTest(later=later):
                self.raw["keyEvents"][1]["wallclock"] = later
                self.write_source()
                with self.assertRaisesRegex(ValueError, "later play"):
                    self.verify()

    def test_duplicate_starts_and_prematch_summary_are_rejected(self):
        self.raw["keyEvents"].append(dict(self.start, wallclock="2026-11-25T10:02:14Z"))
        self.write_source()
        with self.assertRaisesRegex(ValueError, "one first-period kickoff"):
            self.verify()
        self.raw["keyEvents"].pop()
        self.game["status"]["type"]["state"] = "pre"
        self.write_source()
        with self.assertRaisesRegex(ValueError, "no live wall-clock"):
            self.verify()

    def test_capture_hash_window_and_report_identity_are_checked(self):
        mid = self.fixture["match_id"]
        self.write_source()
        self.assertEqual(review._validate_kickoff_reports({}, {mid}, {mid: self.fixture}, self.root, self.now), {})
        with self.assertRaisesRegex(ValueError, "outside registered window"):
            review._validate_kickoff_reports({mid: self.report}, set(), {mid: self.fixture}, self.root, self.now)
        (self.root / self.report["raw_file"]).write_bytes(b"{}")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.verify()
        self.report["raw_file"] = "../elsewhere.json"
        with self.assertRaisesRegex(ValueError, "out-of-root"):
            self.verify()
        self.report["home"] = "espn:99"
        with self.assertRaisesRegex(ValueError, "report identity"):
            self.verify()


if __name__ == "__main__":
    unittest.main()
