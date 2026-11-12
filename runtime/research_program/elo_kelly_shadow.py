"""Elo ratings and independent market-Kelly features, never active predictions.

Core functions check input consistency, not source truth. The CLI additionally
uses existing retained-evidence validators. No staking or learned score claim.
"""
from __future__ import annotations

import argparse
import copy
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from itertools import groupby
import json
import math
from pathlib import Path
from statistics import mean, pstdev

VERSION = "WCPP-ELO-KELLY-SHADOW-0.1"
SIDES = ("home", "draw", "away")


def aware(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("timezone-aware timestamp required")
    return result.astimezone(timezone.utc)


def digest(value) -> str:
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"), allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class EloConfig:
    # Uncalibrated research defaults, not FIFA or ClubElo fitted parameters.
    initial_rating: float = 1500.0
    k_factor: float = 20.0
    scale: float = 400.0
    home_advantage: float = 0.0

    def __post_init__(self):
        if not all(type(v) in (int, float) and math.isfinite(v) for v in asdict(self).values()):
            raise ValueError("finite Elo configuration required")
        if self.k_factor <= 0 or self.scale <= 0:
            raise ValueError("positive K and rating scale required")


def expectation(gap: float, scale: float) -> float:
    """Expected result P(win) + .5 P(draw), NOT P(win)."""
    x = math.log(10.0) * gap / scale
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def _target_clock(target, now):
    cutoff, kickoff = aware(target["cutoff_at"]), aware(target["kickoff_at"])
    if not cutoff < kickoff or cutoff > now or not target.get("cohort") or not target.get("stage"):
        raise ValueError("explicit competition, phase and observed prematch cutoff required")
    return cutoff, kickoff


def elo_snapshot(history, target, config=EloConfig(), *, now=None):
    """Rebuild one competition/phase from labels captured by this cutoff.

    Date-block replay excludes all same-UTC-day results. Late captures may enter
    later snapshots, never earlier ones. Supplied IDs must already be canonical.
    """
    cutoff, kickoff = _target_clock(target, now or datetime.now(timezone.utc))
    if type(target.get("neutral")) is not bool:
        raise ValueError("explicit neutral venue required for Elo")
    if not all(isinstance(target.get(k), str) and target[k] for k in ("home", "away")) or target["home"] == target["away"]:
        raise ValueError("two canonical team IDs required")
    eligible, excluded, ids, fixtures = [], {}, set(), set()
    def exclude(reason):
        excluded[reason] = excluded.get(reason, 0) + 1
    for row in history:
        if (row["cohort"], row.get("stage")) != (target["cohort"], target["stage"]):
            exclude("other_competition_or_phase"); continue
        event, captured = aware(row["kickoff_at"]), aware(row["observed_at"])
        identity = (event, row["home"], row["away"])
        if row["match_id"] in ids or identity in fixtures:
            raise ValueError("duplicate historical fixture")
        ids.add(row["match_id"]); fixtures.add(identity)
        if row["match_id"] == target["match_id"] or event.date() >= kickoff.date():
            exclude("target_same_day_or_future"); continue
        if captured > cutoff:
            exclude("captured_after_cutoff"); continue
        if captured < event + timedelta(minutes=90) or row.get("event_status") != "FT" or row.get("score_basis") != "regulation_90":
            raise ValueError("captured completed 90-minute label required")
        if row.get("first_known_at") is not None and aware(row["first_known_at"]) > captured:
            raise ValueError("source availability after capture")
        if type(row.get("neutral")) is not bool or row["home"] == row["away"]:
            raise ValueError("historical identity and neutral venue required")
        if any(type(row.get(k)) is not int or row[k] < 0 for k in ("home_goals", "away_goals")):
            raise ValueError("nonnegative integer regulation goals required")
        eligible.append(row)
    eligible.sort(key=lambda r: (aware(r["kickoff_at"]), r["match_id"]))
    ratings = {}
    for _, block in groupby(eligible, key=lambda r: aware(r["kickoff_at"]).date()):
        changes, used = {}, set()
        for row in block:
            home, away = row["home"], row["away"]
            if home in used or away in used:
                raise ValueError("ambiguous same-team same-day history")
            used.update((home, away))
            rh, ra = (ratings.get(t, config.initial_rating) for t in (home, away))
            gap = rh - ra + (0.0 if row["neutral"] else config.home_advantage)
            score = 1.0 if row["home_goals"] > row["away_goals"] else 0.5 if row["home_goals"] == row["away_goals"] else 0.0
            delta = config.k_factor * (score - expectation(gap, config.scale))
            changes[home], changes[away] = rh + delta, ra - delta
        ratings.update(changes)
    home, away = target["home"], target["away"]
    rh, ra = (ratings.get(t, config.initial_rating) for t in (home, away))
    gap = rh - ra + (0.0 if target["neutral"] else config.home_advantage)
    return {"schema": VERSION, "status": "RESEARCH_FEATURES_ONLY",
            "config": asdict(config), "home_rating": rh, "away_rating": ra,
            "rating_gap": gap, "expected_home_result": expectation(gap, config.scale),
            "expected_result_definition": "P(home_win) + 0.5 * P(draw), not home-win probability",
            "cold_start_teams": [t for t in (home, away) if t not in ratings],
            "history_n": len(eligible), "history_sha256": digest(eligible), "excluded": excluded,
            "wdl": None, "score_matrix": None, "hyperparameters_calibrated": False,
            "source_truth_certified": False, "active_model_changed": False}


def devig(odds):
    if set(odds) != set(SIDES) or any(type(odds[s]) not in (int, float) or not math.isfinite(odds[s]) or odds[s] <= 1 for s in SIDES):
        raise ValueError("complete decimal 1X2 odds greater than one required")
    inverse = {s: 1.0 / odds[s] for s in SIDES}
    total = sum(inverse.values())
    return {s: inverse[s] / total for s in SIDES}, 1.0 / total


def kelly_snapshot(observations, target, *, provider, bookmaker,
                   reference_bookmakers, max_age_seconds, now=None):
    """External-consensus odds index, NOT Kelly bankroll sizing.

    One fixed provider and a predeclared peer panel. All peers must be present;
    the target bookmaker is excluded. Duplicate feeds never add peer votes.
    Raw evidence validation is done by the CLI before this core function.
    """
    cutoff, kickoff = _target_clock(target, now or datetime.now(timezone.utc))
    peers = tuple(reference_bookmakers)
    if len(peers) < 2 or len(set(peers)) != len(peers) or bookmaker in peers:
        raise ValueError("at least two unique external reference bookmakers required")
    if not all(isinstance(x, str) and x for x in (provider, bookmaker, *peers)) or type(max_age_seconds) not in (int, float) or not math.isfinite(max_age_seconds) or max_age_seconds <= 0:
        raise ValueError("explicit provider, books and positive freshness window required")
    panel, seen, excluded = set(peers) | {bookmaker}, {}, {}
    def exclude(reason):
        excluded[reason] = excluded.get(reason, 0) + 1
    for row in observations:
        if row.get("provider") != provider or row.get("field") != "market_quote":
            exclude("other_provider_or_field"); continue
        quote = row["value"]
        if quote.get("market") != "h2h" or quote.get("bookmaker") not in panel:
            exclude("other_market_or_book"); continue
        if row.get("period") != "regulation_90" or (row["match_id"], row["cohort"], row.get("stage")) != (target["match_id"], target["cohort"], target["stage"]) or aware(row["event_kickoff_at"]) != kickoff:
            exclude("identity_or_phase"); continue
        captured, quoted = aware(row["observed_at"]), aware(quote["quoted_at"])
        if quoted > captured or quoted >= kickoff:
            raise ValueError("source quote chronology conflict")
        if captured > cutoff or quoted > cutoff:
            exclude("unavailable_at_cutoff"); continue
        if (cutoff - quoted).total_seconds() > max_age_seconds:
            exclude("stale_quote"); continue
        devig(quote["odds"])
        key = (quote["bookmaker"], quoted)
        if key in seen and seen[key]["value"]["odds"] != quote["odds"]:
            raise ValueError("conflicting same-book same-time odds")
        if key not in seen or captured < aware(seen[key]["observed_at"]):
            seen[key] = row
    latest = {}
    for (book, quoted), row in sorted(seen.items(), key=lambda pair: pair[0][1]):
        latest[book] = row
    missing = sorted(panel - latest.keys())
    common = {"schema": VERSION, "provider": provider, "bookmaker": bookmaker,
              "reference_bookmakers": list(peers), "max_age_seconds": max_age_seconds,
              "excluded": excluded, "score_matrix": None, "active_model_changed": False,
              "stake_recommendation": None, "source_truth_certified": False}
    if missing:
        return common | {"status": "NOT_READY_MISSING_REFERENCE_QUOTES", "missing_books": missing,
                         "indices": None, "consensus_wdl": None}
    peer_probabilities = [devig(latest[b]["value"]["odds"])[0] for b in peers]
    consensus = {s: mean(p[s] for p in peer_probabilities) for s in SIDES}
    odds = latest[bookmaker]["value"]["odds"]
    own, payout = devig(odds)
    indices = {s: odds[s] * consensus[s] for s in SIDES}
    return common | {"status": "RESEARCH_INDEX_AVAILABLE", "consensus_wdl": consensus,
                     "target_devig_wdl": own, "target_payout_ratio": payout,
                     "indices": indices, "relative_to_payout": {s: indices[s] / payout - 1 for s in SIDES},
                     "peer_probability_std": {s: pstdev(p[s] for p in peer_probabilities) for s in SIDES},
                     "quote_sha256": {b: digest(latest[b]) for b in sorted(panel)},
                     "quoted_at": {b: latest[b]["value"]["quoted_at"] for b in sorted(panel)},
                     "interpretation": "Price disagreement against fixed peers; not an outcome probability, fund flow or low-index win rule."}


def report(request, evidence_root):
    # Project-level identity and retained-source contracts remain authoritative.
    from competition_scope import require
    from research_program.replay import eligible_labels
    from v2_test.data_contract import validate, safe_content
    safe_content(request)
    target = require(request["target"])
    labels, excluded = eligible_labels(request["history"], target, evidence_root)
    config = EloConfig(**request["elo_config"])
    elo = elo_snapshot(labels, target, config)
    observations = request["observations"]
    for row in observations:
        validate(row, evidence_root)
    kelly = kelly_snapshot(observations, target, **request["kelly_config"])
    return {"version": VERSION, "target": copy.deepcopy(target), "elo": elo, "kelly": kelly,
            "label_exclusions": excluded, "retained_evidence_consistency_checked": True,
            "source_truth_certified": False, "active_model_changed": False,
            "score_prediction_generated": False, "trained_fusion": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
    parser.add_argument("--evidence-root", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    result = report(json.loads(Path(args.request).read_text(encoding="utf-8")), args.evidence_root)
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


if __name__ == "__main__":
    main()
