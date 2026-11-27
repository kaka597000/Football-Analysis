"""Read-only live review for a registered pair of J1 comprehensive display windows.

The registered universe is fixed by the original display_windows ledger record.
Repository-issued model rows are reported as audit evidence only. Strict display
credit requires a verified first freeze, locked complete batch, independent
original-chat readback and first-delivery receipt, approved issue scope, and an
actual kickoff check. Result labels pass through kpi_validation._actual.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys
import tarfile
from urllib.parse import parse_qs, urlsplit

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from optimization.evaluate import settle_ou
from research.analysis_freeze import DEFAULT_ROOT, aware, verify
from research.display_delivery import DISPLAY_TRACK
from research.display_review import review_batch, summarize as summarize_display
from research_program.display_market import comparisons as _market_comparisons
from research_program.display_window_contract import consecutive_windows, series_for_version
from research_program.display_windows import prepared_batch
from research_program.kpi_validation import _actual, quote_audit
from research_program.j1_native_scores import review as review_native_scores
from research_program.three_arm_review import _certify, _results

REGISTRATION_TRACK = "j1-comprehensive-windows-20261125-registration"
PUBLICATION_TRACK = "j1-comprehensive-windows-20261125-"
WINDOW_NAMES = ("nov25", "nov28_29")
SETTLEMENTS = ("FULL_WIN", "HALF_WIN", "PUSH", "HALF_LOSS", "FULL_LOSS")


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_jsonl(path):
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _registered(plan_record):
    plan_record = verify(plan_record)
    payload = plan_record["payload"]
    identity = payload["identity"]
    series = series_for_version(identity.get("version"))
    if (plan_record.get("track") != series + "-registration" or payload.get("track") != series
            or identity.get("competitions") != ["jpn.1"]):
        raise ValueError("unexpected or unverified J1 window registration")
    fixtures = payload.get("fixtures", {})
    slates = identity.get("slates", {})
    specs = consecutive_windows(identity.get("protocol", {}).get("windows", []))
    if set(slates) != set(specs) or (plan_record['track'] == REGISTRATION_TRACK and tuple(specs) != WINDOW_NAMES):
        raise ValueError("both registered windows are required")
    if not fixtures or len(fixtures) != sum(len(ids) for ids in slates.values()):
        raise ValueError("registered fixture population is incomplete or duplicated")
    all_ids = set()
    for name, spec in specs.items():
        ids = slates[name]
        if len(ids) != spec["expected"] or len(set(ids)) != len(ids) or all_ids.intersection(ids):
            raise ValueError("registered window size or unique slot set differs")
        all_ids.update(ids)
        start, end = aware(spec["from"]), aware(spec["until"])
        for mid in ids:
            f = fixtures.get(mid)
            if (not f or f.get("match_id") != mid or f.get("cohort") != "jpn.1"
                    or f.get("stage") != "Japanese J1 League" or not start <= aware(f["kickoff_at"]) < end):
                raise ValueError("registered fixture does not belong to its original window: " + mid)
    if all_ids != set(fixtures):
        raise ValueError("window slates do not cover the complete original ledger")
    if (plan_record.get("stage") != "prematch" or plan_record.get("evaluation_slot") != "FIRST_PREMATCH"
            or aware(plan_record["recorded_at"]) >= min(aware(f["kickoff_at"]) for f in fixtures.values())):
        raise ValueError("J1 series must be registered before its first kickoff")
    return plan_record, fixtures, slates, specs


def _ledger_records(root):
    """Read immutable ledger records without modifying the archive."""
    records = []
    for path in Path(root).rglob("*.json"):
        try:
            value = read(path)
            if isinstance(value, dict) and value.get("schema") == "WCPP-ISSUED-FREEZE-1":
                records.append(verify(value))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return records


def _publication(records, window, plan_sha, mids, *, prefix=PUBLICATION_TRACK):
    matches = [r for r in records if r.get("track") in
               (prefix + window, prefix + window + "-registration")]
    if len(matches) > 1:
        raise ValueError("conflicting publication lock records for " + window)
    if not matches:
        return None
    record = matches[0]
    payload = record.get("payload", {})
    identity = payload.get("identity", {})
    rows = identity.get("rows", {})
    selected = identity.get("selected", [])
    if (identity.get("parent") != plan_sha or identity.get("window") != window
            or set(rows) != set(mids) or len(selected) > 3 or len(set(selected)) != len(selected)
            or not set(selected) <= set(mids) or record.get("stage") != "prematch"):
        raise ValueError("publication lock does not match the original complete window")
    eligible = []
    for mid, row in rows.items():
        if row.get("match_id") != mid or aware(row["kickoff_at"]) != aware(record["payload"]["fixtures"][mid]["kickoff_at"]):
            raise ValueError("published row identity differs from registered slot")
        if row.get("authored") is None:
            if (not isinstance(row.get("unissued_reason"), str) or not row["unissued_reason"].strip()
                    or row.get("pair") is not None or row.get("displayed_scores") != []
                    or row.get("wdl") is not None or row.get("total_line") is not None
                    or row.get("total_direction") is not None or row.get("ranking_probability") is not None):
                raise ValueError("unissued row must retain an explicit reason and empty authored fields")
        else:
            if row.get("unissued_reason"):
                raise ValueError("issued and unissued fields conflict")
            abstain = row.get("displayed_scores") == [] and row.get("ranking_probability") is None
            if not abstain:
                if not isinstance(row.get("ranking_probability"), (int, float)) or not math.isfinite(row["ranking_probability"]):
                    raise ValueError("issued published row requires its locked ranking probability")
                eligible.append(row)
    ranked = sorted(eligible, key=lambda r:(-r["ranking_probability"], aware(r["kickoff_at"]), r["match_id"]))[:3]
    if selected != [r["match_id"] for r in ranked]:
        raise ValueError("selected seats differ from ranking all eligible issued rows")
    if aware(record["recorded_at"]) >= min(aware(rows[m]["kickoff_at"]) for m in mids):
        raise ValueError("publication selection was not locked before the window kickoff")
    return record


def _decimal_from_american(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value == 0:
        return None
    return 1.0 + value / 100.0 if value > 0 else 1.0 + 100.0 / abs(value)


def _book_alias(value):
    return "".join(ch for ch in str(value or "").casefold() if ch.isalnum())


def _raw_market_clock(odds):
    """Read a consistent clock attached to the selected bookmaker market."""
    clocks = set()
    for key in ("lastUpdated", "last_update", "lastUpdate", "quotedAt", "quoteTime"):
        value = odds.get(key)
        if isinstance(value, str) and value:
            try:
                clocks.add(aware(value))
            except ValueError:
                return "INVALID_TIMEZONE:" + value
    if len(clocks) > 1:
        return "CONFLICTING_SOURCE_CLOCKS"
    return next(iter(clocks)).astimezone(timezone.utc).isoformat() if clocks else None


def _market_clock_match(quote, raw_clock):
    if raw_clock is None:
        return False, "RAW_BOOKMAKER_MARKET_CLOCK_MISSING"
    if raw_clock.startswith("INVALID_TIMEZONE:"):
        return False, "RAW_BOOKMAKER_MARKET_CLOCK_INVALID"
    if raw_clock == "CONFLICTING_SOURCE_CLOCKS":
        return False, "RAW_BOOKMAKER_MARKET_CLOCK_CONFLICT"
    try:
        if aware(raw_clock) != aware(quote["quoted_at"]):
            return False, "RAW_BOOKMAKER_MARKET_CLOCK_CONFLICT"
    except (KeyError, TypeError, ValueError):
        return False, "ENVELOPE_QUOTE_TIME_NOT_BOUND_TO_MARKET_CLOCK"
    return True, None


def _quote_source(quote, fixture, archive_root, archive_cache, expected_h2h=None):
    """Verify the acquired odds payload against preserved response bytes and fixture IDs."""
    if not isinstance(quote, dict) or not quote.get("source_sha256") or not quote.get("source_url"):
        return False, "QUOTE_RAW_SOURCE_REFERENCE_MISSING", None
    url = urlsplit(quote["source_url"])
    if url.scheme == "https" and url.netloc == "api.infersports.dev":
        if not archive_root:
            return False, "QUOTE_RAW_RESPONSE_NOT_IN_RETAINED_ARCHIVE", None
        from research_program.club_market_capture import verify_j1_quote
        try:
            return verify_j1_quote(fixture, quote, archive_root, expected_h2h)
        except (AttributeError, KeyError, OSError, TypeError, ValueError):
            return False, "QUOTE_RAW_EVENT_STRUCTURE_UNVERIFIED", None
    if (url.scheme != "https" or url.hostname not in {"site.api.espn.com", "site.web.api.espn.com"}
            or not url.path.endswith("/soccer/" + fixture["cohort"] + "/summary")
            or parse_qs(url.query).get("event") != [fixture["match_id"].removeprefix("espn:")]):
        return False, "QUOTE_SOURCE_EVENT_ENDPOINT_MISMATCH", None
    try:
        acquired = aware(quote["query_time"])
        if not aware(fixture["cutoff_at"]) >= acquired:
            return False, "QUOTE_ACQUIRED_AFTER_SELECTION_CUTOFF", None
    except (KeyError, TypeError, ValueError):
        return False, "QUOTE_ACQUISITION_TIME_MISSING", None
    source_hash = quote["source_sha256"]
    if source_hash not in archive_cache:
        found = None
        for path in Path(archive_root).glob("*.tar.gz") if archive_root else []:
            try:
                with tarfile.open(path, "r:gz") as archive:
                    for member in archive.getmembers():
                        if not member.isfile():
                            continue
                        stream = archive.extractfile(member)
                        if stream is None:
                            continue
                        body = stream.read()
                        digest = hashlib.sha256(body).hexdigest()
                        archive_cache.setdefault(digest, (body, str(path), member.name))
                        if digest == source_hash:
                            found = archive_cache[digest]
                            break
                if found:
                    break
            except (OSError, tarfile.TarError):
                continue
        if found is None:
            return False, "QUOTE_RAW_RESPONSE_NOT_IN_RETAINED_ARCHIVE", None
    body, _, _ = archive_cache[source_hash]
    try:
        raw = json.loads(body)
        header = raw["header"]
        comp, = header["competitions"]
        status = comp["status"]["type"]
        event_id = fixture["match_id"].removeprefix("espn:")
        teams = {v["homeAway"]: "espn:" + str(v["team"]["id"]) for v in comp["competitors"]}
        if (str(header["id"]) != event_id or str(comp["id"]) != event_id
                or header["league"]["slug"] != fixture["cohort"]
                or status.get("state") != "pre" or status.get("completed") is not False
                or len(comp["competitors"]) != 2 or set(teams) != {"home", "away"}
                or teams["home"] != fixture["home"] or teams["away"] != fixture["away"]
                or aware(comp["date"]) != aware(fixture["kickoff_at"])):
            return False, "QUOTE_RAW_FIXTURE_OR_SOURCE_DATE_MISMATCH", None
        market = raw.get("odds", raw.get("pickcenter", []))
        wanted_book = _book_alias(quote.get("bookmaker"))
        selected = [o for o in market if _book_alias(o.get("provider", {}).get("name")) == wanted_book]
        if len(selected) != 1:
            return False, "QUOTE_RAW_BOOKMAKER_MARKET_NOT_UNIQUE", None
        odds = selected[0]
        over, under = _decimal_from_american(odds.get("overOdds")), _decimal_from_american(odds.get("underOdds"))
        if (odds.get("overUnder") != quote.get("line") or over is None or under is None
                or not math.isclose(over, quote.get("over_odds", float("nan")), rel_tol=0, abs_tol=1e-8)
                or not math.isclose(under, quote.get("under_odds", float("nan")), rel_tol=0, abs_tol=1e-8)):
            return False, "QUOTE_RAW_TOTAL_LINE_OR_PRICES_DIFFER", None
        if expected_h2h:
            raw_h2h = dict(home=_decimal_from_american(odds.get("homeTeamOdds", {}).get("moneyLine")),
                           draw=_decimal_from_american(odds.get("drawOdds", {}).get("moneyLine")),
                           away=_decimal_from_american(odds.get("awayTeamOdds", {}).get("moneyLine")))
            if any(raw_h2h[k] is None or not math.isclose(raw_h2h[k], expected_h2h[k], rel_tol=0, abs_tol=1e-8)
                   for k in ("home", "draw", "away")):
                return False, "QUOTE_RAW_1X2_PRICES_DIFFER", None
        raw_clock = _raw_market_clock(odds)
    except (KeyError, TypeError, ValueError, IndexError):
        return False, "QUOTE_RAW_EVENT_STRUCTURE_UNVERIFIED", None
    return True, None, raw_clock


def _quote_for(record):
    if record is None:
        return None
    payload, fixture = record.get("payload", {}), record.get("fixture", {})
    # Only accept the project's explicit quote envelope; do not infer prices from prose.
    for value in (payload.get("total_quote"), fixture.get("total_quote"),
                  payload.get("market", {}).get("total_quote") if isinstance(payload.get("market"), dict) else None):
        if isinstance(value, dict):
            return value
    return None


def _five_way(quote, goals, side):
    side = {"大": "over", "小": "under"}.get(side, side)
    if quote is None or goals is None or side not in ("over", "under"):
        return None
    return settle_ou(sum(goals), quote["line"], side)


def _selected_sweep(selected, rows):
    """Measure only the unique first score at each locked selected seat."""
    if len(selected) != 3:
        hits = sum(rows[mid].get("top1_hit") is True for mid in selected)
        misses = sum(rows[mid].get("top1_hit") is False for mid in selected)
        confirmed = hits + misses
        return dict(status="SHORT_BATCH", hit=None, hits=hits, misses=misses, confirmed=confirmed,
                    eligible=len(selected), required=3,
                    alternate_only_hits=sum(rows[mid].get("top3_hit") is True and rows[mid].get("top1_hit") is False
                                            for mid in selected), alternate_sweep=None)
    hits = sum(rows[mid].get("top1_hit") is True for mid in selected)
    misses = sum(rows[mid].get("top1_hit") is False for mid in selected)
    confirmed = hits + misses
    alt_only = sum(rows[mid].get("top3_hit") is True and rows[mid].get("top1_hit") is False for mid in selected)
    if misses:
        status, hit = "CONFIRMED_PRIMARY_MISS", False
    elif confirmed == 3:
        status, hit = "ALL_THREE_PRIMARY_HIT", True
    else:
        status, hit = "PENDING_UNCONFIRMED_SEATS", None
    alternate = (all(rows[mid].get("top3_hit") is True for mid in selected)
                 if all(rows[mid].get("top3_hit") is not None for mid in selected) else None)
    return dict(status=status, hit=hit, hits=hits, misses=misses, confirmed=confirmed,
                eligible=3, required=3, alternate_only_hits=alt_only, alternate_sweep=alternate)


def _count_settlement(counts, settlement):
    if settlement in SETTLEMENTS:
        counts[settlement] += 1


def _stage_thresholds_met(minima_ok, checks):
    return bool(minima_ok and all(checks.values()))


def _official_phases(window_reports):
    """An official stage passes only when that same stage passes both windows."""
    if not window_reports:
        return []
    names = set.intersection(*(set(r.get("_window_phase_candidates", {})) for r in window_reports.values()))
    names = sorted(names, key=lambda n: int(n.split("_")[-1]) if n.startswith("stage_") else 99)
    phases = []
    for name in names:
        per_window = {window: report["_window_phase_candidates"][name]
                      for window, report in window_reports.items()}
        passed = len(per_window) == 2 and all(per_window.values())
        phases.append(dict(stage=name, pass_=passed, per_window_thresholds_met=per_window,
                           blockers=[] if passed else ["SAME_STAGE_MUST_PASS_IN_BOTH_REGISTERED_WINDOWS"]))
    return phases


def _kickoff_instant(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("explicit timezone required for kickoff evidence")
    return value.astimezone(timezone.utc)


def summary_kickoff(body, source_url, fixture, observed):
    """Extract the typed first-period kickoff from an ESPN summary response."""
    match_id = fixture["match_id"]
    event_id = match_id.removeprefix("espn:")
    league = fixture["competition"]
    url = urlsplit(source_url)
    if (not match_id.startswith("espn:") or not event_id.isascii() or not event_id.isdecimal()
            or fixture.get("cohort", league) != league or url.scheme != "https"
            or url.hostname not in {"site.api.espn.com", "site.web.api.espn.com"}
            or url.username is not None or url.password is not None or url.port not in (None, 443)
            or url.path != f"/apis/site/v2/sports/soccer/{league}/summary"
            or parse_qs(url.query, keep_blank_values=True).get("event") != [event_id]):
        raise ValueError("expected the registered event's ESPN summary URL")

    if body.startswith(b"\x1f\x8b"):
        body = gzip.decompress(body)
    raw = json.loads(body)
    header = raw["header"]
    game, = header["competitions"]
    if (str(header["id"]) != event_id or str(game["id"]) != event_id
            or header["league"]["slug"] != league):
        raise ValueError("ESPN event or league differs from the registered fixture")
    teams = [(entry["homeAway"], "espn:" + str(entry["team"]["id"]))
             for entry in game["competitors"]]
    expected_teams = [("away", fixture["away"]), ("home", fixture["home"])]
    if fixture["home"] == fixture["away"] or sorted(teams) != expected_teams:
        raise ValueError("ESPN home and away identities differ from the registered fixture")
    if (game.get("status", {}).get("type", {}).get("state") == "pre"
            or raw.get("wallclockAvailable") is False):
        raise ValueError("ESPN summary has no live wall-clock evidence")

    starts = []
    events = raw.get("keyEvents", [])
    for event in events:
        kind = event.get("type", {})
        period = event.get("period", {}).get("number")
        if kind.get("type") == "kickoff" and type(period) is int and period == 1:
            clock = event.get("clock", {}).get("value")
            if (str(kind.get("id", "80")) != "80" or type(clock) not in (float, int)
                    or clock != 0 or event.get("shootout") is True):
                raise ValueError("first-period kickoff has conflicting type or match clock")
            starts.append(_kickoff_instant(event.get("wallclock")))
    if len(starts) != 1:
        raise ValueError("expected one first-period kickoff with its own wallclock")
    start = starts[0]
    if start > observed:
        raise ValueError("actual kickoff follows capture")
    for event in events:
        elapsed = event.get("clock", {}).get("value")
        if type(elapsed) in (float, int) and elapsed > 0 and event.get("wallclock") is not None:
            if not start < _kickoff_instant(event["wallclock"]) <= observed:
                raise ValueError("later play contradicts the kickoff or capture chronology")
    return start


def _validate_kickoff_reports(raw, window_ids, fixtures, root, now):
    """Return only kickoff claims reproduced from retained ESPN event evidence."""
    root = Path(root).resolve()
    as_of = _kickoff_instant(now)
    checked = {}
    for mid, item in raw.items():
        if mid not in window_ids:
            raise ValueError(f"actual kickoff outside registered window: {mid}")
        try:
            fixture = fixtures[mid]
            if (item["match_id"] != mid or item["identity_verified"] is not True
                    or any(item[key] != fixture[key]
                           for key in ("match_id", "competition", "home", "away"))):
                raise ValueError("report identity differs from registered fixture")
            observed = _kickoff_instant(item["observed_at"])
            if observed > as_of:
                raise ValueError("capture follows review time")
            path = (root / item["raw_file"]).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise ValueError("missing or out-of-root retained source")
            body = path.read_bytes()
            if hashlib.sha256(body).hexdigest() != item["source_sha256"]:
                raise ValueError("retained source SHA-256 differs")
            start = summary_kickoff(body, item["source_url"], fixture, observed)
            if start != _kickoff_instant(item["actual_kickoff_at"]):
                raise ValueError("reported start differs from the actual kickoff wallclock")
        except (AttributeError, EOFError, KeyError, OSError, TypeError, ValueError) as exc:
            raise ValueError(f"{mid}: {exc}") from exc
        checked[mid] = dict(item, actual_kickoff_at=start.isoformat())
    return checked


def _batch_text_audit(body, identity, records):
    from research.display_delivery import delivery_request

    observed_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    exact = observed_hash == identity.get("body_sha256")
    # The body digest commits the original display order. The ledger encodes the
    # fixture mapping by key for hashing, which can differ from display order.
    ordered = exact and all(mid in records and body.count(delivery_request(records[mid])["text"]) == 1
                            for mid, row in identity["rows"].items() if row.get("authored"))
    return dict(sha256_matches_registered_batch=exact, original_blocks_present_once_in_order=ordered,
                registered_body_sha256=identity.get("body_sha256"), observed_body_sha256=observed_hash)


def _window_report(name, spec, fixtures, records, display_rows, publication, result_by_id,
                   *, quote_archive_root=None, archive_cache=None, batch_root=None,
                   prepared_batch_verified=False):
    by_id = {r["match_id"]: r for r in display_rows}
    registered_ids = list(fixtures)
    if set(by_id) != set(registered_ids):
        raise ValueError("review rows must retain every registered slot")
    strict_settlements = Counter()
    observed_settlements = Counter()
    observed_only_settlements = Counter()
    strict_quotes = 0
    observed_quote_n = 0
    quote_records = {}
    wdl_issued = wdl_settled = wdl_hits = 0
    certified_rows = 0
    unissued_rows = {}
    body_audit = {"available": False, "sha256_matches_registered_batch": False,
                  "original_blocks_present_once_in_order": False,
                  "prepared_batch_verified": False}
    if publication:
        for mid, row in publication["payload"]["identity"]["rows"].items():
            if row.get("authored") is None:
                unissued_rows[mid] = row["unissued_reason"]
        if batch_root:
            folder = Path(batch_root) / name
            body_path = folder / "message.txt"
            if body_path.is_file():
                body = body_path.read_text(encoding="utf-8")
                body_audit = dict(available=True, prepared_batch_verified=prepared_batch_verified,
                                  path=str(body_path),
                                  **_batch_text_audit(body, publication["payload"]["identity"], records))
    for mid, fixture in fixtures.items():
        record = records.get(mid)
        dr = by_id[mid]
        kickoff_proof = dr.get('actual_kickoff_at') or dr.get('actual_kickoff_interval')
        quote = _quote_for(record)
        result = result_by_id.get(mid)
        goals = (result["home_goals"], result["away_goals"]) if result else None
        direction = record.get("payload", {}).get("total_direction") if record else None
        qa = {"eligible": False, "reasons": ["NO_REAL_TOTAL_QUOTE"], "quote": quote}
        if quote is not None:
            observed_quote_n += 1
            quote_fixture = dict(record["fixture"], total_quote=quote)
            qa = quote_audit(quote_fixture)
            market = record.get("payload", {}).get("market_snapshot", {})
            expected_h2h = market.get("h2h") if market.get("quote") == quote else None
            source_ok, source_issue, raw_market_clock = _quote_source(
                quote, record["fixture"], quote_archive_root,
                archive_cache if archive_cache is not None else {}, expected_h2h)
            if not source_ok:
                qa["eligible"] = False
                qa["reasons"] = [*qa["reasons"], source_issue]
            else:
                clock_ok, clock_issue = _market_clock_match(quote, raw_market_clock)
                if not clock_ok:
                    qa["eligible"] = False
                    qa["reasons"] = [*qa["reasons"], clock_issue]
            strict_quotes += int(qa["eligible"])
        settlement = _five_way(quote, goals, direction) if quote is not None and source_ok else None
        _count_settlement(observed_settlements, settlement)
        if settlement in SETTLEMENTS:
            if not qa["eligible"]:
                _count_settlement(observed_only_settlements, settlement)
            if qa["eligible"] and dr.get("top1_issued") and kickoff_proof:
                _count_settlement(strict_settlements, settlement)
        wdl = record.get("payload", {}).get("wdl") if record else None
        if wdl and dr.get("top1_issued") and kickoff_proof:
            wdl_issued += 1
            if goals is not None:
                wdl_settled += 1
                actual = "home" if goals[0] > goals[1] else "away" if goals[0] < goals[1] else "draw"
                predicted = {"主胜": "home", "平": "draw", "平局": "draw", "客胜": "away"}.get(wdl, wdl)
                wdl_hits += int(predicted == actual)
        certified_rows += int(record is not None and dr.get("top1_issued") and not dr.get("display_issue"))
        quote_records[mid] = dict(observed=quote is not None, raw_source_verified=source_ok if quote is not None else False,
                                  strict=qa["eligible"],
                                  strict_reasons=qa["reasons"],
                                  raw_market_quoted_at=raw_market_clock if quote is not None else None,
                                  bookmaker=quote.get("bookmaker") if quote else None,
                                  quoted_at=quote.get("quoted_at") if quote else None,
                                  query_time=quote.get("query_time") if quote else None,
                                  line=quote.get("line") if quote else None,
                                  over_odds=quote.get("over_odds") if quote else None,
                                  under_odds=quote.get("under_odds") if quote else None,
                                  five_way_settlement=settlement)
    summary = summarize_display(display_rows)
    selected = publication["payload"]["identity"]["selected"] if publication else []
    selected_results = [by_id[mid] for mid in selected]
    selected_rows = {mid: by_id[mid] for mid in selected}
    sweep = _selected_sweep(selected, selected_rows)
    primary_hits, primary_misses, primary_confirmed = sweep["hits"], sweep["misses"], sweep["confirmed"]
    alternate_only_hits = sweep["alternate_only_hits"]
    primary_sweep, sweep_status, alternate_sweep = sweep["hit"], sweep["status"], sweep["alternate_sweep"]
    settled = sum(r["verified_completed"] for r in display_rows)
    issued = sum(r["top1_issued"] for r in display_rows)
    total_n = len(fixtures)
    quote_min = spec["minimum_strict_line_issues"]
    score_min, wdl_min = spec["minimum_confirmed_scores"], spec["minimum_confirmed_wdl"]
    nonpush_min = spec["minimum_nonpush_settled"]
    market_coverage_ok = strict_quotes >= quote_min
    independent_readback_ok = issued == certified_rows and issued > 0 and not any(r.get("display_issue") for r in display_rows if r.get("top1_issued"))
    phase_rows = []
    result_accuracy = summary["top1"]["accuracy"]
    three_accuracy = summary["top3"]["accuracy"]
    wdl_accuracy = wdl_hits / wdl_settled if wdl_settled else None
    total_nonpush_n = sum(strict_settlements.values()) - strict_settlements["PUSH"]
    total_wins = strict_settlements["FULL_WIN"]
    ou_accuracy = total_wins / total_nonpush_n if total_nonpush_n else None
    body_ok = bool(body_audit["available"] and body_audit["sha256_matches_registered_batch"]
                   and body_audit["original_blocks_present_once_in_order"]
                   and body_audit["prepared_batch_verified"])
    minima_ok = (settled == total_n and issued >= score_min and wdl_issued >= wdl_min
                 and wdl_settled == wdl_issued and market_coverage_ok
                 and total_nonpush_n >= nonpush_min and independent_readback_ok and publication is not None
                 and body_ok and primary_sweep is True and not unissued_rows)
    report = dict(window=name, registered_n=total_n, completed_n=settled, pending_n=total_n-settled,
                  confirmed_first_score_n=issued, confirmed_wdl_n=wdl_issued,
                  wdl_settled_n=wdl_settled, wdl_hits=wdl_hits, wdl_accuracy=wdl_accuracy,
                  top1=summary["top1"], top3=summary["top3"],
                  actual_bookmaker_quote=dict(quote_records_n=observed_quote_n,
                      raw_verified_price_snapshots_n=sum(v["raw_source_verified"] for v in quote_records.values()),
                      strict_quote_issues_n=strict_quotes, strict_quote_coverage=strict_quotes/total_n if total_n else None,
                      minimum_strict_quote_issues=quote_min, market_coverage_minimum_met=market_coverage_ok,
                      five_way_settlements={k:strict_settlements[k] for k in SETTLEMENTS},
                      observed_price_five_way_settlements={k:observed_settlements[k] for k in SETTLEMENTS},
                      observed_only_five_way_settlements={k:observed_only_settlements[k] for k in SETTLEMENTS},
                      nonpush_settled_n=total_nonpush_n, nonpush_settled_minimum=nonpush_min,
                      full_win_rate=ou_accuracy, per_match=quote_records),
                  selected_seats=dict(selected_match_ids=selected, selected_n=len(selected),
                                      seats_coverage=len(selected)/3,
                                      unique_primary_hits_n=primary_hits, unique_primary_misses_n=primary_misses,
                                      unique_primary_confirmed_n=primary_confirmed,
                                      unique_primary_hit_rate=primary_hits/primary_confirmed if primary_confirmed else None,
                                      all_three_unique_primary_hit_sweep=primary_sweep,
                                      primary_sweep_status=sweep_status,
                                      backup_only_hits_n=alternate_only_hits,
                                      all_three_any_of_top3_sweep=alternate_sweep,
                                      per_match=[dict(match_id=r["match_id"], verified_completed=r["verified_completed"],
                                                      top1_hit=r["top1_hit"], top3_hit=r["top3_hit"])
                                                 for r in selected_results]),
                  per_match=[dict(match_id=mid, kickoff_at=fixtures[mid]["kickoff_at"],
                                  display_status=by_id[mid]["display_status"],
                                  display_issue=by_id[mid].get("display_issue"),
                                  confirmed_first_score=by_id[mid].get("top1_issued", False),
                                  first_score_hit=by_id[mid].get("top1_hit"),
                                  three_scores_hit=by_id[mid].get("top3_hit"),
                                  wdl_prediction=(records[mid].get("payload", {}).get("wdl") if mid in records else None),
                                  actual_result=(f'{result_by_id[mid]["home_goals"]}-{result_by_id[mid]["away_goals"]}'
                                                 if mid in result_by_id else None),
                                  actual_result_observed_at=(result_by_id[mid].get("observed_at") if mid in result_by_id else None),
                                  actual_result_source_sha256=(result_by_id[mid].get("evidence", {}).get("sha256")
                                                               if mid in result_by_id else None),
                                  unissued_reason=unissued_rows.get(mid), quote=quote_records[mid])
                             for mid in fixtures],
                  unissued_slots=unissued_rows,
                  repository_preissued_audit_only=dict(count=0, note="reported separately; never receives comprehensive display credit"),
                  repository_batch_body_audit=body_audit,
                  registered_minima=dict(confirmed_scores=score_min, confirmed_wdl=wdl_min,
                                         strict_line_issues=quote_min, nonpush_settled=nonpush_min),
                  blockers=[])
    if settled != total_n: report["blockers"].append("UNSETTLED_REGISTERED_FIXTURES")
    if issued < score_min or wdl_issued < wdl_min: report["blockers"].append("CONFIRMED_DISPLAY_MINIMUM_NOT_MET")
    if not independent_readback_ok: report["blockers"].append("INDEPENDENT_FIRST_RECEIPT_OR_READBACK_UNCERTIFIED")
    if not market_coverage_ok: report["blockers"].append("STRICT_BOOKMAKER_MARKET_COVERAGE_BELOW_REGISTERED_MINIMUM")
    if total_nonpush_n < nonpush_min: report["blockers"].append("NONPUSH_MARKET_SETTLEMENT_MINIMUM_NOT_MET")
    if publication is None: report["blockers"].append("COMPLETE_PREKICKOFF_SELECTED_BATCH_NOT_LOCKED")
    if publication and not (body_audit["available"] and body_audit["sha256_matches_registered_batch"]
                            and body_audit["original_blocks_present_once_in_order"]):
        report["blockers"].append("REPOSITORY_WHOLE_BATCH_BODY_NOT_VERIFIED")
    if len(selected) != 3 or primary_sweep is not True: report["blockers"].append("SELECTED_THREE_PRIMARY_SWEEP_INCOMPLETE")
    if unissued_rows: report["blockers"].append("REGISTERED_UNISSUED_SLOTS")
    return report, minima_ok, phase_rows


def review(plan_record, *, ledger_root=DEFAULT_ROOT, result_state, evidence_root,
           batch_root=None, kickoff_evidence_root=None, readbacks=None,
           actual_kickoffs=None, approved_scopes=None, jleague_kickoffs=None, at=None):
    """Review both locked windows; missing evidence stays visible and earns no credit."""
    now = aware(at) if isinstance(at, str) else (at or datetime.now(timezone.utc))
    plan_record, universe, slates, specs = _registered(plan_record)
    kickoff_root = kickoff_evidence_root or evidence_root
    if jleague_kickoffs is not None:
        from research_program import jleague_kickoff_proof
        official_bindings = jleague_kickoff_proof.bindings(jleague_kickoffs, plan_record, universe, kickoff_root, now)
    ledger_records = _ledger_records(ledger_root)
    if batch_root:
        ledger_records += _ledger_records(batch_root)
    unique_records = {}
    for record in ledger_records:
        key = (record.get("track"), record.get("match_id"), record.get("record_sha256"))
        unique_records[key] = record
    ledger_records = list(unique_records.values())
    issues = {}
    repository_audit = {}
    for r in ledger_records:
        mid = r.get("fixture", {}).get("match_id")
        if mid not in universe:
            continue
        if r.get("track") == DISPLAY_TRACK and r.get("stage") == "prematch" and r.get("revision") == 1:
            if mid in issues and issues[mid]["record_sha256"] != r["record_sha256"]:
                raise ValueError("conflicting original comprehensive freeze for " + mid)
            issues[mid] = r
        elif (r.get("track", "").startswith("research-r8-conditional-score-20261125")
              and r.get("stage") == "prematch" and r.get("evaluation_slot") == "FIRST_PREMATCH"):
            repository_audit.setdefault(mid, []).append(r.get("track"))
    result_file = Path(result_state) / "normalized-results.jsonl"
    if result_file.exists():
        results, result_issues = _results(Path(result_state), universe, now)
    else:
        results, result_issues = {}, {mid: ["normalized result archive missing"] for mid in universe}
    result_by_id = {}
    latest_result_issues = {}
    with __import__("tempfile").TemporaryDirectory(prefix="j1-display-actual-") as actual_tmp:
        actual_root = Path(actual_tmp)
        latest = {}
        if result_file.exists():
            for line in result_file.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                mid = row.get("match_id")
                if mid not in universe:
                    continue
                try:
                    certified = _certify(row, universe[mid], Path(result_state), now)
                    latest.setdefault(mid, []).append(certified)
                except (OSError, EOFError, KeyError, TypeError, ValueError) as exc:
                    latest_result_issues.setdefault(mid, []).append(str(exc))
        for mid, certified_rows in latest.items():
            if len({(r["home"], r["away"]) for r in certified_rows}) != 1:
                raise ValueError("conflicting certified regulation-90 result evidence: " + mid)
            result = max(certified_rows, key=lambda r: (aware(r["source"]["captured_at"],), r["source"]["sha256"]))
            meta = result["source"]
            raw_path = (Path(result_state) / meta["path"]).resolve()
            body = raw_path.read_bytes()
            if raw_path.suffix == ".gz":
                body = gzip.decompress(body)
            body_sha = hashlib.sha256(body).hexdigest()
            if body_sha != meta["sha256"]:
                raise ValueError("latest 90-minute result raw source checksum differs: " + mid)
            evidence_file = actual_root / (mid.replace(":", "-") + ".json")
            evidence_file.write_bytes(body)
            actual = dict(universe[mid], event_status="FT", score_basis="regulation_90",
                          home_goals=result["home"], away_goals=result["away"],
                          observed_at=meta["captured_at"], evidence=dict(file=evidence_file.name,
                              query_time=meta["captured_at"], sha256=body_sha,
                              url=meta["source_url"], identity_verified=True))
            if _actual(actual, universe[mid], actual_root, now) is not None:
                result_by_id[mid] = dict(actual)
    reports, phases = {}, {}
    for name in specs:
        mids = slates[name]
        rich_fixtures = {}
        for mid in mids:
            f = dict(universe[mid])
            f.update(competition="jpn.1", score_basis="regulation_90", provider_competition="jpn.1",
                     provider_stage="regular", identity_verified=True, stage_verified=True)
            record = issues.get(mid)
            if record:
                f = record["fixture"]
            else:
                f["cutoff_at"] = plan_record["recorded_at"]
            rich_fixtures[mid] = f
        records = [issues[mid] for mid in mids if mid in issues]
        package = (readbacks or {}).get(name, {})
        staged = package.get("staged")
        msg = package.get("message_readback")
        firsts = package.get("first_deliveries", {})
        starts = _validate_kickoff_reports((actual_kickoffs or {}).get(name, {}), set(mids),
                                           rich_fixtures, kickoff_root, now)
        if jleague_kickoffs is not None:
            official = {mid: jleague_kickoffs['captures'][mid] for mid in mids if mid in jleague_kickoffs['captures']}
            starts, kickoff_audit = jleague_kickoff_proof.compare_sources(
                official, official_bindings, rich_fixtures, kickoff_root, now, starts, result_by_id)
        scopes = (approved_scopes or {}).get(name, {})
        rows = review_batch(rich_fixtures.values(), records, state=result_state, at=now,
                            staged=staged, room_id=package.get("room_id"), author_id=package.get("author_id"),
                            message_id=package.get("message_id"),
                            read_message=(lambda _: msg) if msg is not None else None,
                            lookup_original=firsts.get,
                            lookup_actual_kickoff=starts.get,
                            lookup_approved_scope=scopes.get)
        publication = _publication(ledger_records, name, plan_record["record_sha256"], mids,
                                   prefix=plan_record["payload"]["track"] + "-")
        prepared_verified = False
        native_pairs = {}
        if publication:
            if batch_root:
                folder = Path(batch_root) / name
                entries_path, message_path = folder / "entries.json", folder / "message.txt"
                if entries_path.is_file() and message_path.is_file():
                    _, prepared_fixtures, prepared_identity = prepared_batch(
                        Path(batch_root) / "registration", name, read(entries_path),
                        message_path.read_text(encoding="utf-8"), folder)
                    if (set(f["match_id"] for f in prepared_fixtures) != set(mids)
                            or prepared_identity != publication["payload"]["identity"]):
                        raise ValueError("display_windows.prepared_batch reconstruction differs from registered lock")
                    prepared_verified = True
            all_by_freeze = {r["freeze_id"]: r for r in ledger_records}
            for mid, row in publication["payload"]["identity"]["rows"].items():
                if row.get("authored") is None:
                    continue
                author = issues.get(mid)
                ref = row["authored"]
                if (author is None or author["freeze_id"] != ref["id"]
                        or author["record_sha256"] != ref["sha256"]
                        or author["track"] != DISPLAY_TRACK
                        or author["fixture"]["match_id"] != mid
                        or aware(author["recorded_at"]) >= aware(row["kickoff_at"])):
                    raise ValueError("published authored row differs from its original first freeze: " + mid)
                pair_ref = row.get("pair")
                pair = all_by_freeze.get(pair_ref.get("id")) if pair_ref else None
                if (pair is None or pair.get("track") != "current-test-pair"
                        or pair.get("record_sha256") != pair_ref.get("sha256")
                        or author["payload"].get("current_test_reference", {}).get("sha256") != pair["record_sha256"]):
                    raise ValueError("published row is not bound to its original Current/Test pair: " + mid)
                native_pairs[mid] = pair
                expected_row = (author["payload"].get("displayed_scores"), author["payload"].get("wdl"),
                                author["payload"].get("total_line"), author["payload"].get("total_direction"))
                observed_row = (row.get("displayed_scores"), row.get("wdl"), row.get("total_line"), row.get("total_direction"))
                if observed_row != expected_row:
                    raise ValueError("published score/WDL/market row differs from its first freeze: " + mid)
        quote_archives = Path(batch_root) / "evidence" if batch_root else None
        report, minima_ok, _ = _window_report(name, specs[name], rich_fixtures,
                                              {mid: issues[mid] for mid in mids if mid in issues},
                                              rows, publication, result_by_id,
                                              quote_archive_root=quote_archives, archive_cache={}, batch_root=batch_root,
                                              prepared_batch_verified=prepared_verified)
        if jleague_kickoffs is not None:
            report['jleague_kickoff_evidence'] = kickoff_audit
        if publication and aware(publication["recorded_at"]) <= now:
            report['native_pair_scores'] = review_native_scores(rich_fixtures, native_pairs, result_by_id, starts, now)
        report["same_fixture_market"] = _market_comparisons(
            report["per_match"], {mid: issues[mid] for mid in mids if mid in issues})
        paired = report["same_fixture_market"]["channel_confirmed_rows"]["paired"]
        eligible_pairs = {metric: value["eligible"] for metric, value in paired.items()}
        market_comparison_ok = bool(eligible_pairs) and all(eligible_pairs.values())
        report["market_comparison_coverage"] = dict(eligible_pairs=eligible_pairs,
                                                     verified_same_cutoff=market_comparison_ok)
        if minima_ok and not market_comparison_ok:
            report["blockers"].append("VERIFIED_SAME_CUTOFF_MARKET_COMPARISON_UNAVAILABLE")
        minima_ok = minima_ok and market_comparison_ok
        audit_rows = []
        audit_counts = dict(first_issued=0, first_settled=0, first_hits=0,
                            three_issued=0, three_settled=0, three_hits=0,
                            wdl_issued=0, wdl_settled=0, wdl_hits=0,
                            line_issued=0, line_settled=0, line_nonpush=0, line_full_wins=0)
        for mid in mids:
            candidates = [r for r in ledger_records if r.get("fixture", {}).get("match_id") == mid
                          and r.get("track") in repository_audit.get(mid, [])]
            if not candidates:
                continue
            record = min(candidates, key=lambda r: aware(r["recorded_at"]))
            pred = record["payload"].get("forecasts", {}).get("candidate", {})
            actual = result_by_id.get(mid)
            score_hit = wdl_hit = line_settlement = None
            if pred.get("primary"):
                audit_counts["first_issued"] += 1
                if actual:
                    audit_counts["first_settled"] += 1
                    score_hit = pred["primary"] == f'{actual["home_goals"]}-{actual["away_goals"]}'
                    audit_counts["first_hits"] += int(score_hit)
            if pred.get("top3"):
                audit_counts["three_issued"] += 1
                if actual:
                    audit_counts["three_settled"] += 1
                    audit_counts["three_hits"] += int(f'{actual["home_goals"]}-{actual["away_goals"]}' in pred["top3"])
            if pred.get("wdl"):
                audit_counts["wdl_issued"] += 1
                if actual:
                    audit_counts["wdl_settled"] += 1
                    observed = "home" if actual["home_goals"] > actual["away_goals"] else "away" if actual["home_goals"] < actual["away_goals"] else "draw"
                    wdl_hit = pred["wdl"] == observed
                    audit_counts["wdl_hits"] += int(wdl_hit)
            line = record["payload"].get("line")
            side = pred.get("total_side")
            if type(line) in (int, float) and side:
                audit_counts["line_issued"] += 1
                if actual:
                    audit_counts["line_settled"] += 1
                    line_settlement = _five_way(dict(line=line), (actual["home_goals"], actual["away_goals"]), side)
                    if line_settlement == "PUSH":
                        pass
                    else:
                        audit_counts["line_nonpush"] += 1
                        audit_counts["line_full_wins"] += int(line_settlement == "FULL_WIN")
            audit_rows.append(dict(match_id=mid, track=record["track"], freeze_id=record["freeze_id"],
                                   first_prediction=pred.get("primary"), first_hit=score_hit,
                                   three_predictions=pred.get("top3"), wdl_prediction=pred.get("wdl"),
                                   wdl_hit=wdl_hit, observed_line=line, total_direction=side,
                                   five_way_line_settlement=line_settlement,
                                   actual_result=(f'{actual["home_goals"]}-{actual["away_goals"]}' if actual else None),
                                   actual_result_observed_at=(actual.get("observed_at") if actual else None)))
        def rate(hits, den):
            return hits/den if den else None
        report["repository_preissued_audit_only"] = dict(
            count=len(audit_rows), by_track=dict(Counter(r["track"] for r in audit_rows)),
            metrics=dict(first_exact=dict(hits=audit_counts["first_hits"], eligible=audit_counts["first_settled"],
                                          issued=audit_counts["first_issued"], accuracy=rate(audit_counts["first_hits"], audit_counts["first_settled"])),
                         three_exact=dict(hits=audit_counts["three_hits"], eligible=audit_counts["three_settled"],
                                          issued=audit_counts["three_issued"], accuracy=rate(audit_counts["three_hits"], audit_counts["three_settled"])),
                         wdl=dict(hits=audit_counts["wdl_hits"], eligible=audit_counts["wdl_settled"],
                                  issued=audit_counts["wdl_issued"], accuracy=rate(audit_counts["wdl_hits"], audit_counts["wdl_settled"])),
                         actual_line=dict(full_wins=audit_counts["line_full_wins"], nonpush=audit_counts["line_nonpush"],
                                          issued=audit_counts["line_issued"], full_win_rate=rate(audit_counts["line_full_wins"], audit_counts["line_nonpush"])),
                         observed_only_line_settlements=dict(FULL_WIN=sum(r["five_way_line_settlement"]=="FULL_WIN" for r in audit_rows),
                             HALF_WIN=sum(r["five_way_line_settlement"]=="HALF_WIN" for r in audit_rows),
                             PUSH=sum(r["five_way_line_settlement"]=="PUSH" for r in audit_rows),
                             HALF_LOSS=sum(r["five_way_line_settlement"]=="HALF_LOSS" for r in audit_rows),
                             FULL_LOSS=sum(r["five_way_line_settlement"]=="FULL_LOSS" for r in audit_rows))),
            rows=audit_rows,
            note="Repository first-issued R8 rows and observed lines are an audit-only comparison, not actual comprehensive chat-display credits.")
        report["result_evidence_issues"] = {mid: result_issues.get(mid, []) for mid in mids if result_issues.get(mid)}
        report["latest_result_evidence_issues"] = {mid: latest_result_issues[mid] for mid in mids if latest_result_issues.get(mid)}
        report["display_review"] = summarize_display(rows)
        report["formal_comprehensive_metrics"] = dict(
            first_exact=dict(hits=report["top1"]["hits"], eligible=report["top1"]["settled_n"],
                             issued=report["top1"]["issued_n"], accuracy=report["top1"]["accuracy"]),
            three_exact=dict(hits=report["top3"]["hits"], eligible=report["top3"]["settled_n"],
                             issued=report["top3"]["issued_n"], accuracy=report["top3"]["accuracy"]),
            wdl=dict(hits=report["wdl_hits"], eligible=report["wdl_settled_n"],
                     issued=report["confirmed_wdl_n"], accuracy=report["wdl_accuracy"]),
            strict_actual_line=dict(full_wins=report["actual_bookmaker_quote"]["five_way_settlements"]["FULL_WIN"],
                                    eligible=report["actual_bookmaker_quote"]["nonpush_settled_n"],
                                    raw_verified_prices=report["actual_bookmaker_quote"]["raw_verified_price_snapshots_n"],
                                    strict_quotes=report["actual_bookmaker_quote"]["strict_quote_issues_n"],
                                    full_win_rate=report["actual_bookmaker_quote"]["full_win_rate"]))
        targets = plan_record["payload"]["identity"]["stages"]
        stage_results = []
        for stage in targets:
            t = stage["targets"]
            checks = dict(first_exact_score=(report["top1"]["accuracy"] or 0) >= t["first_exact_score"],
                          three_exact_scores=(report["top3"]["accuracy"] or 0) >= t["three_exact_scores"],
                          wdl=(report["wdl_accuracy"] or 0) >= t["wdl"],
                          actual_line_full_win=(report["actual_bookmaker_quote"]["full_win_rate"] or 0)
                              >= t["actual_line_full_win_nonpush"],
                          selected_three_primary_sweep=(report["selected_seats"]["all_three_unique_primary_hit_sweep"] is True)
                              if t.get("selected_three_primary_sweep") == 1.0 else True)
            local_pass = _stage_thresholds_met(minima_ok, checks)
            stage_results.append(dict(stage=stage["stage"], thresholds=t, checks=checks,
                                      window_thresholds_met=local_pass, pass_=False,
                                      blockers=[] if local_pass else list(report["blockers"])))
        report["phases"] = stage_results
        report["_window_phase_candidates"] = {s["stage"]: s["window_thresholds_met"] for s in stage_results}
        report["phase_pass"] = False
        reports[name] = report
    official_phases = _official_phases(reports)
    official_by_name = {p["stage"]: p for p in official_phases}
    for report in reports.values():
        for phase in report["phases"]:
            official = official_by_name.get(phase["stage"], dict(pass_=False))
            phase["official_both_windows_pass"] = official["pass_"]
            phase["pass_"] = official["pass_"]
            if not official["pass_"] and phase["window_thresholds_met"]:
                phase["blockers"].append("OTHER_REGISTERED_WINDOW_DID_NOT_PASS_SAME_STAGE")
        report.pop("_window_phase_candidates", None)
        report["phase_pass"] = any(p["pass_"] for p in report["phases"])
    return dict(schema="WCPP-J1-DISPLAY-WINDOW-REVIEW-1", at=now.isoformat(),
                registration=dict(record_sha256=plan_record["record_sha256"], track=plan_record["track"],
                                  windows={n: len(slates[n]) for n in specs}),
                windows=reports, combined=dict(registered_n=sum(x["registered_n"] for x in reports.values()),
                    completed_n=sum(x["completed_n"] for x in reports.values()),
                    pending_n=sum(x["pending_n"] for x in reports.values()),
                    phases=official_phases, phase_pass=any(p["pass_"] for p in official_phases),
                    rule="The same declared stage must pass independently in both registered windows; no pooling around a failed window."))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--registration", type=Path, required=True, help="original verified display_windows registration record")
    p.add_argument("--ledger-root", type=Path, default=Path(DEFAULT_ROOT))
    p.add_argument("--batch-root", type=Path, help="archive containing registration/, both window directories, and evidence/")
    p.add_argument("--results-state", type=Path, required=True, help="directory containing normalized-results.jsonl")
    p.add_argument("--result-evidence-root", type=Path, required=True)
    p.add_argument("--kickoff-evidence-root", type=Path, help="defaults to --result-evidence-root")
    p.add_argument("--readbacks", type=Path, help="JSON export of per-window staged body, original-message readback and first receipts")
    p.add_argument("--actual-kickoffs", type=Path, help="JSON map window -> match_id -> independently verified actual kickoff")
    p.add_argument("--jleague-kickoffs", type=Path, help="archive packet with the pregame binding record and official J.League captures")
    p.add_argument("--approved-scopes", type=Path, help="JSON map window -> match_id -> original approved issue scope")
    p.add_argument("--at", help="ISO-8601 review time; defaults to current UTC")
    args = p.parse_args()
    result = review(read(args.registration), ledger_root=args.ledger_root, batch_root=args.batch_root,
                    result_state=args.results_state, evidence_root=args.result_evidence_root,
                    kickoff_evidence_root=args.kickoff_evidence_root,
                    readbacks=read(args.readbacks) if args.readbacks else None,
                    actual_kickoffs=read(args.actual_kickoffs) if args.actual_kickoffs else None,
                    approved_scopes=read(args.approved_scopes) if args.approved_scopes else None,
                    jleague_kickoffs=read(args.jleague_kickoffs) if args.jleague_kickoffs else None, at=args.at)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
