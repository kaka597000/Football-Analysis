"""Join existing private validation receipts to original score rankings; output aggregates and optionally rows."""

import argparse
from collections import Counter
import csv
import json
from pathlib import Path


def read(path):
    return json.loads(path.read_bytes())


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def goal_band(score):
    total = sum(map(int, score.split("-")))
    return "0-2" if total <= 2 else "3" if total == 3 else "4+"


def collect(early, domestic, survey, recent, scores):
    entries = []
    for e in early["events"]:
        score = "-".join(map(str, e["regulation_score"]))
        for track in ["Current", "Current+market"]:
            missed = not e["current_top3"] and (
                track == "Current" or not e["market_top3"]
            )
            entries.append(
                ("Nov4+6", track, e["id"], score, missed, e["both_complete"])
            )

    panel_codes = {
        "historical_february": "Feb",
        "nov21_original_first_version": "Nov21",
    }
    for row in domestic:
        panel = panel_codes[row["panel"]]
        assessment = survey[
            "historical_february_panel"
            if panel == "Feb"
            else "nov21_original_first_version_panel"
        ]
        if assessment["certified_complete_recent_three"] != 0:
            raise ValueError("use individual domestic evidence for any positive join")
        if row["current_top3_miss"] not in ("True", "False"):
            raise ValueError("missing Current rank classification")
        entries.append(
            (
                panel,
                "Current",
                row["row_key"],
                row["actual_score"],
                row["current_top3_miss"] == "True",
                False,
            )
        )

    refs = {str(r["event_id"]).removeprefix("espn:"): r for r in scores}
    targets = {r["match_id"].removeprefix("espn:"): r for r in recent["targets"]}
    if (
        len(refs) != len(scores)
        or len(targets) != len(recent["targets"])
        or refs.keys() != targets.keys()
    ):
        raise ValueError(
            "late roster differs between score verification and source audit"
        )
    for event, r in targets.items():
        score_row = refs[event]
        if (
            score_row["official_label_checker"] != "PASS"
            or int(score_row["native_completed_period"]) != 2
        ):
            raise ValueError("uncertified late result")
        home, away = r["sides"]

        def complete(side):
            return len(side["slots"]) == 3 and all(
                (s["shot_evidence"] or {}).get("complete_native_shot_event_any_version")
                and s["shot_evidence"]["source_retained_by_target_cutoff"]
                and s["shot_evidence"]["provider_team_ids_crosswalked_to_ESPN"]
                for s in side["slots"]
            )

        ok = complete(home) and complete(away)
        # This join describes the measured source search. Positive coverage needs the same full prior-profile validation as the early replay.
        if ok or r["full_native_event_field_slots"]:
            raise ValueError(
                "validate newly found native pages with the prior-profile contract"
            )
        for track, key, missed in (
            (
                "Author",
                "author_top3_miss",
                score_row["author_actual_score_position"] == "MISS",
            ),
            ("Current", "current_matrix_top3_miss", int(score_row["rank_current"]) > 3),
        ):
            if bool(r[key]) != missed:
                raise ValueError("conflicting late rank")
            entries.append(("Nov24", track, event, score_row["actual_90m"], missed, ok))

    if len({e[:3] for e in entries}) != len(entries):
        raise ValueError("duplicate target within panel and track")
    return entries


def october_entries(rows):
    """Use only the original in-scope Current and its certified regular-time label."""
    result, rosters = [], {}
    seen = set()
    for row in rows:
        key = (row["window"], row["number"])
        if key in seen:
            raise ValueError("duplicate October target")
        seen.add(key)
        r = rosters.setdefault(row["window"], Counter())
        r["listed"] += 1
        if not row["scoped"] or not row["original_current_verified"]:
            continue
        r["original_current"] += 1
        r["complete_sources"] += int(row["complete_both_three"])
        if not row["certifiable_ft90"]:
            r["unsettled_in_archive"] += 1
            continue
        if row["actual_current_matrix_top3"] is None:
            raise ValueError("no original Current rank for certified result")
        if not row.get("result_raw_sha256") or not row.get("freeze_sha256"):
            raise ValueError("unbound October inputs")
        value = row["regulation_score"]
        score = "-".join(map(str, value)) if isinstance(value, list) else value
        result.append(
            (
                row["window"],
                "Current",
                row["match_id"],
                score,
                not row["actual_current_matrix_top3"],
                row["complete_both_three"],
            )
        )
        r["scoreable"] += 1
    return result, rosters


def summarize(entries):
    result = {}
    for panel, track, _, score, miss, ok in entries:
        group = result.setdefault(panel + "/" + track, {})
        for bucket in ("all", goal_band(score)):
            c = group.setdefault(bucket, Counter())
            c.update(
                targets=1,
                complete=int(ok),
                misses=int(miss),
                complete_misses=int(miss and ok),
            )
    for group in result.values():
        for bucket in ("all", "0-2", "3", "4+"):
            c = group.get(bucket, {})
            group[bucket] = dict(
                targets=c.get("targets", 0),
                complete=c.get("complete", 0),
                misses=c.get("misses", 0),
                complete_misses=c.get("complete_misses", 0),
                unavailable_misses=c.get("misses", 0) - c.get("complete_misses", 0),
            )
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    for name in ["early", "domestic", "domestic-survey", "recent", "recent-scores"]:
        p.add_argument("--" + name, required=True, type=Path)
    p.add_argument(
        "--october",
        type=Path,
        help="optional per-fixture certified prior-archive ledger",
    )
    args = p.parse_args()
    rows = collect(
        read(args.early),
        read_csv(args.domestic),
        read(args.domestic_survey),
        read(args.recent),
        read_csv(args.recent_scores),
    )
    rosters = {}
    if args.october:
        extra, rosters = october_entries(read(args.october))
        rows.extend(extra)
    print(
        json.dumps(
            {"panels": summarize(rows), "october_rosters": rosters},
            sort_keys=True,
            indent=2,
        )
    )
