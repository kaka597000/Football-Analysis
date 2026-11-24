"""Export anonymized aggregate tables and safe evidence digests for the public research report."""

import argparse
from collections import Counter
from pathlib import Path

from audit import SLUG, args_for, csvout, dump, jread, original_seasons, utc


def build(args):
    out = args.out
    models = jread(out / "research-fit.json")
    coverage = jread(out / "coverage.json")
    fm = jread(out / "fotmob-coverage.json")
    grades = jread(out / "diagnostic.json")
    targets = jread(out / "windows.json")
    first = original_seasons(args)
    tally = Counter()
    for target in targets:
        if target["window"] != "november":
            continue
        comp = target["competition"]
        snap = first[comp, 2026]
        known = max(snap["observed"], snap["known_by"])
        matches = {
            g["id"]: g
            for g in snap["completed"]
            if all(x is not None for x in g["npx"])
        }
        for side in target["sides"]:
            for slot in side["slots"]:
                g = matches.get(slot["id"])
                if (
                    g
                    and known <= utc(target["cutoff"])
                    and g["date"] == slot["day"]
                    and {g["home_id"], g["away_id"]} == {slot["team"], slot["opponent"]}
                ):
                    tally[comp] += 1
    extras = {
        "jpn.1": "FotMob 有 6 场原生值与精确采集回执，阶段待核",
        "aus.1": "FotMob 有 4 场原生值与精确采集回执，阶段待核",
        "uefa.europa": "FotMob 有 17 场原生值仅能从 Git 确定保存上界，缺本窗完整名单和阶段审定",
        "uefa.nations": "29 场去重射门档，25 场可重建非点球值，45 队各最多两场",
    }
    scopes = jread(args.scope)["competitions"]
    rows = []
    for s in scopes:
        k = s["code"]
        r = {"code": k, "competition": s["name"]}
        if k in SLUG:
            oct = coverage["by_league"][k]["october"]
            nov = coverage["by_league"][k]["november"]
            f = fm["by_league"][k]
            r.update(
                training_matches=models[k]["matches"],
                october_targets=oct["fixtures"],
                october_both_six=oct["complete_fixtures"],
                november_targets=nov["fixtures"],
                november_slots=nov["actual_prior_slots"],
                november_understat_oct15=tally[k],
                november_understat_all=nov["scored_slots"],
                november_fotmob_exact=f["states"].get("CAPTURE_AND_GIT", 0),
                november_fotmob_bound_only=f["states"].get("GIT_BOUND_ONLY", 0),
                november_fotmob_verified=f["verified_slots"],
                november_understat_both_six=nov["complete_fixtures"],
                november_fotmob_both_six=f["complete_fixtures"],
                status="历史同场覆盖已核",
            )
        else:
            r["status"] = extras.get(k, "本轮留存源未建立完整球队历史与固定目标分母")
        rows.append(r)
    csvout(out / "scope-coverage.csv", rows)
    dump(out / "scope-coverage.json", rows)
    result = []
    groups = {
        "all_49": grades["windows"]["october"],
        "activated_38": grades["active_only"],
        **grades["by_league"],
    }
    for group, g in groups.items():
        for m, p in g["paired"].items():
            b = g["tracks"]["base"]["metrics"][m]
            c = g["tracks"]["candidate"]["metrics"][m]
            result.append(
                dict(
                    group=group,
                    metric=m,
                    paired_eligible=p["eligible"],
                    retained_population=g["population"],
                    base_hits=b["hits"],
                    candidate_hits=c["hits"],
                    gained=p["gained"],
                    lost=p["lost"],
                    ci95_paired_rate_delta=p["date_cluster_ci"],
                    unit="research matrix / WDL / capture-only real total line",
                )
            )
    csvout(out / "scorecard.csv", result)
    dump(out / "scorecard.json", result)
    dump(
        out / "audit-summary.json",
        {
            "oct15_only_slots_in_november": sum(tally.values()),
            "november": coverage["november"],
            "october": coverage["october"],
            "november_fotmob": fm["november"],
            "five_league_fit": {c: v["matches"] for c, v in models.items()},
            "log_loss_october": grades["windows"]["october"]["mean_score_log_loss"],
            "activated_only_log_loss": grades["active_only"]["mean_score_log_loss"],
            "real_line_settlements": {
                a: v["total_settlements"]
                for a, v in grades["windows"]["october"]["tracks"].items()
            },
            "new_paired_real_display_receipts": grades[
                "official_displayed_pair_fixtures"
            ],
            "new_official_picked_three_batches": grades[
                "official_selected_three_batches"
            ],
            "new_live_forward_protocols": 0,
            "retained_R7_count": 192,
            "two_clocks_real_lines": 0,
            "known_by_bound_october": 0,
        },
    )
    print(
        "All-scope table",
        len(rows),
        "historical fit",
        sum(v["matches"] for v in models.values()),
        "Oct15-alone November slots",
        sum(tally.values()),
    )


if __name__ == "__main__":
    p = args_for(argparse.ArgumentParser(description=__doc__))
    p.add_argument("--scope", required=True, type=Path)
    build(p.parse_args())
