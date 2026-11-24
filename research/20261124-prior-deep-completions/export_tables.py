"""Export the complete November roster and competition-level denominators from the replay."""

import argparse
import csv
import gzip
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from build_inputs import HERE


def save(path, rows, columns):
    with path.open("w", newline="", encoding="utf-8-sig") as output:
        writer = csv.DictWriter(output, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def export(grade, dest):
    dest.mkdir(exist_ok=True, parents=True)
    summary = json.loads((grade / "study.json").read_bytes())
    roster = json.loads((grade / "roster.json").read_bytes())
    metrics = {
        key: summary[key]
        for key in (
            "historical",
            "source_timed",
            "market_tier",
            "roster_by_competition",
            "independent_signal_coverage",
            "checked_market_receipts",
            "protected",
        )
    }
    (dest / "metrics.json").write_text(
        json.dumps(metrics, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    )
    with gzip.open(grade / "paired.jsonl.gz", "rt", encoding="utf-8") as f:
        predictions = {
            r["id"]: r for r in map(json.loads, f) if r["window"] == "source_timed"
        }
    fixtures = []
    for game in roster:
        row = {
            k: game.get(k)
            for k in (
                "id",
                "cohort",
                "home",
                "away",
                "scheduled",
                "espn_id",
                "actual_kickoff",
                "status",
            )
        }
        r = predictions.get("understat:" + game["id"])
        if r:
            row.update(
                cutoff_utc=r["cutoff_at"],
                regulation_90="–".join(map(str, r["actual"])),
                last_home_input_date=r["latest_dates"][0],
                last_away_input_date=r["latest_dates"][1],
                result_sha256=r["result_sha256"],
                result_source_path=r["result_ref"],
                market_tier=r["market_timing"],
            )
            if q := r["market"]:
                row.update(
                    line=q["line"],
                    bookmaker=q["bookmaker"],
                    over_price=q["over_price"],
                    under_price=q["under_price"],
                    captured_at=q["captured_at"],
                    quoted_at=q["quote_at"],
                    market_sha256=q["source_sha256"],
                )
            for arm, p in r["forecast"].items():
                row.update(
                    {
                        arm + "_matrix_first": p["primary"],
                        arm + "_matrix_top3": " | ".join(p["top3"]),
                        arm + "_wdl": p["wdl"],
                        arm + "_total_side": p["total_side"],
                        arm + "_total_settlement": p.get("settlement"),
                        arm + "_score_log_loss": p["score_log_loss"],
                    }
                )
        fixtures.append(row)
    save(
        dest / "november-fixtures.csv",
        fixtures,
        dict.fromkeys(k for row in fixtures for k in row),
    )
    table = [
        "# 11 月原排 77 场",
        "",
        "表内比分均为离线模型矩阵。可核实开球的行列北京时间日期，其余保留来源的原排日期。",
        "",
        "| 赛事 | 比赛 | 日期 | 状态 | 90 分钟 | 基线前三（按序） | 候选前三（按序） | 基线 / 候选胜平负 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    states = {
        "SCORED": "已核实",
        "PRIOR_61_EXCLUDED": "旧 61 场",
        "R7_EXCLUDED": "R7 已锁",
        "ACTUAL_TIME_OUTSIDE_WINDOW": "实际开球超窗",
        "NO_UNIQUE_SOURCE_IDENTITY": "两队身份未唯一匹配",
    }
    for row in fixtures:
        fixture = row["home"] + " – " + row["away"]
        if row.get("espn_id"):
            fixture += " (" + row["espn_id"] + ")"
        values = (
            row["cohort"],
            fixture,
            (
                datetime.fromisoformat(row["actual_kickoff"])
                .astimezone(ZoneInfo("Asia/Shanghai"))
                .date()
                .isoformat()
                if row.get("actual_kickoff")
                else row["scheduled"][:10]
            ),
            states.get(row["status"], row["status"]),
            row.get("regulation_90"),
            row.get("base_matrix_top3", "").replace(" | ", "、"),
            row.get("candidate_matrix_top3", "").replace(" | ", "、"),
            (row["base_wdl"] + " / " + row["candidate_wdl"])
            if row.get("base_wdl")
            else None,
        )
        table.append("| " + " | ".join(v or "—" for v in values) + " |")
    (dest / "november.md").write_text("\n".join(table) + "\n")
    scope = json.loads((HERE.parents[1] / "configs/analysis-scope.json").read_bytes())[
        "competitions"
    ]
    groups = []
    for c in scope:
        code = c["code"]
        status = summary["independent_signal_coverage"][code]
        pool = summary["roster_by_competition"][code]
        for window, stats in summary["by_competition"][code].items():
            row = dict(
                competition=code, name=c["name"], window=window, input_status=status
            )
            if stats:
                row.update(
                    registered=sum(pool.values())
                    if window == "source_timed"
                    else stats["population"],
                    paired=stats["population"],
                    new_signal_used=stats["signal_used"],
                    base_log_loss=stats["score_log_loss"]["base"],
                    candidate_log_loss=stats["score_log_loss"]["candidate"],
                )
                for k, pair in stats["paired"].items():
                    row.update(
                        {
                            k + "_n": pair["eligible"],
                            k + "_base": stats["tracks"]["base"]["metrics"][k]["hits"]
                            if pair["eligible"]
                            else None,
                            k + "_candidate": stats["tracks"]["candidate"]["metrics"][
                                k
                            ]["hits"]
                            if pair["eligible"]
                            else None,
                            k + "_gained": pair["gained"] if pair["eligible"] else None,
                            k + "_lost": pair["lost"] if pair["eligible"] else None,
                        }
                    )
            groups.append(row)
    save(
        dest / "competition-coverage.csv",
        groups,
        dict.fromkeys(k for row in groups for k in row),
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--grade", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    export(args.grade, args.out)
