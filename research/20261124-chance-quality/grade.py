"""Score the sealed historical inputs against separately certified regulation labels."""

import argparse
from collections import Counter
from hashlib import sha256
import json
from math import log
from pathlib import Path
import tempfile

import numpy as np
from opportunities import clock, require
from optimization.evaluate import settle_ou
from research_program.three_arm_model import score_picks, top_three_scores
from research_program.replay import _finished_espn_label
from replay import evidence, packed_value, ROOT

BLIND_SHA = "92cb011cf76e4676cb036f0224fc91a143fcbed32ee844782b1979571949994c"
TRACKS = ("team", "market", "candidate", "same_xg_poisson")


def certified(repo, records):
    review = "WCPP/reports/review-europe-20261105/"
    with tempfile.TemporaryDirectory() as work:
        tmp = Path(work)
        with evidence(repo, tmp) as arc:
            results = packed_value(arc, review + "normalized-results.json")
            labels = []
            for r in records:
                mid = r["id"].split(":")[1]
                f = packed_value(arc, ROOT + f"fixtures/{mid}.json")
                (o,) = [row for row in results if row["match_id"] == r["id"]]
                raw = arc.extractfile(review + f"raw/espn-summary-{mid}.json").read()
                require(
                    sha256(raw).hexdigest() == o["source_sha256"]
                    and o["score_basis"] == "regulation_90"
                    and o["source_url"].endswith("/uefa.champions/summary?event=" + mid)
                    and clock(o["observed_at"]) > clock(f["kickoff_at"]),
                    "label_source",
                )
                local = tmp / "result.json"
                local.write_bytes(raw)
                expected = f | {k: o[k] for k in ("home_goals", "away_goals")}
                _finished_espn_label(expected, {"url": o["source_url"]}, local)
                (game,) = json.loads(raw)["header"]["competitions"]
                for team in game["competitors"]:
                    parts = team["linescores"]
                    require(
                        len(parts) == 2
                        and sum(int(p["displayValue"]) for p in parts)
                        == o[team["homeAway"] + "_goals"],
                        "regulation_period_conflict",
                    )
                labels.append(
                    dict(
                        id=r["id"],
                        home=f["home"],
                        away=f["away"],
                        kickoff=f["kickoff_at"],
                        competition=f["competition"],
                        status="FT90",
                        score=[o["home_goals"], o["away_goals"]],
                    )
                )
    return labels


def assessment(matrix, score, line):
    m = np.asarray(matrix)
    # The project's Asian-settlement and WDL utilities accept a square matrix.
    size = max(m.shape)
    m = np.pad(m, ((0, size - m.shape[0]), (0, size - m.shape[1])))
    p = score_picks(m, line=line)
    h, a = score
    actual = f"{h}-{a}"
    three = top_three_scores(m)
    prob = m[h, a] if max(h, a) < size else 0.0
    ou = settle_ou(h + a, line, p["total"]["direction"])
    return dict(
        top1=p["primary"] == actual,
        top3=actual in three,
        wdl=p["wdl"] == ("home" if h > a else "away" if a > h else "draw"),
        fullwin=ou == "FULL_WIN",
        settlement=ou,
        loss=-log(max(float(prob), 1e-15)),
        picks=three,
    )


def section(rows):
    rates = {}
    for t in TRACKS:
        cases = [r[t] for r in rows]
        rates[t] = dict(
            n=len(rows),
            hits={
                k: sum(v[k] for v in cases) for k in ("top1", "top3", "wdl", "fullwin")
            },
            totals=dict(Counter(v["settlement"] for v in cases)),
            mean_log_loss=sum(v["loss"] for v in cases) / len(rows) if rows else None,
        )
    changes = {}
    for t in ("team", "market", "same_xg_poisson"):
        changes[t] = {
            k: dict(
                gained=sum(r["candidate"][k] and not r[t][k] for r in rows),
                lost=sum(r[t][k] and not r["candidate"][k] for r in rows),
            )
            for k in ("top1", "top3", "wdl", "fullwin")
        }
    missed = [r for r in rows if not r["team"]["top3"] and not r["market"]["top3"]]
    return dict(
        arms=rates,
        candidate_vs=changes,
        double_miss=len(missed),
        rescued=sum(r["candidate"]["top3"] for r in missed),
        rank1_changed=sum(
            r["team"]["picks"][0] != r["candidate"]["picks"][0] for r in rows
        ),
        fixtures_add_outside_both=sum(
            bool(
                set(r["candidate"]["picks"])
                - set(r["team"]["picks"])
                - set(r["market"]["picks"])
            )
            for r in rows
        ),
    )


def grade(records, labels):
    require(len({r["id"] for r in records}) == len(records), "duplicate_forecast")
    require(
        len(labels) == len({r["id"] for r in labels})
        and {r["id"] for r in labels} == {r["id"] for r in records},
        "result_roster",
    )
    by_id = {r["id"]: r for r in labels}
    cases = []
    for r in records:
        o = by_id[r["id"]]
        require(
            o["home"] == r["home"]
            and o["away"] == r["away"]
            and clock(o["kickoff"]) == clock(r["kickoff"])
            and o["competition"] == r.get("competition", "uefa.champions")
            and o["status"] == "FT90"
            and len(o["score"]) == 2
            and all(type(v) is int and v >= 0 for v in o["score"]),
            "result_identity_or_period",
        )
        case = {"used": "candidate" in r}
        for t in TRACKS:
            case[t] = assessment(r[t] if t in r else r["team"], o["score"], r["line"])
        cases.append(case)
    return dict(
        original_n=len(records),
        with_opportunities=section([r for r in cases if r["used"]]),
        all_with_team_fallback=section(cases),
        main_line_confirmed=sum(r["main_asian_line_verified"] for r in records),
        actual_display_paired=0,
        prelocked_top_three_paired_batches=0,
    )


def australia_label(repo, record):
    from australia import MID

    require(record["id"] == MID, "aus_result_identity")
    review = "workspace-updates/20261107-review-and-aleague/"
    pack = "d6e9811a8e3c735e6a3875ec9a85bde284fbbfa5e276fcc272d50fda30d2e2ac"
    with tempfile.TemporaryDirectory() as work:
        tmp = Path(work)
        with evidence(repo, tmp, review, pack) as arc:
            raw = arc.extractfile(
                "WCPP/reports/review-all-20261107/raw/401892144_espn.json"
            ).read()
        require(
            sha256(raw).hexdigest()
            == "26ad1dd8552cc0a7b9d27179406dee5b48f0e09adfcfc820c94932e57d2d1297",
            "aus_result_digest",
        )
        raw_label = tmp / "result.json"
        raw_label.write_bytes(raw)
        (event,) = json.loads(raw)["header"]["competitions"]
        by_side = {v["homeAway"]: v for v in event["competitors"]}
        scores = [
            sum(int(p["displayValue"]) for p in by_side[s]["linescores"])
            for s in ("home", "away")
        ]
        require(
            clock(event["date"]) == clock(record["kickoff"])
            and all(len(v["linescores"]) == 2 for v in by_side.values()),
            "aus_regulation",
        )
        _finished_espn_label(
            dict(
                match_id=MID,
                cohort="aus.1",
                home=record["home"],
                away=record["away"],
                kickoff_at=record["kickoff"],
                home_goals=scores[0],
                away_goals=scores[1],
            ),
            {
                "url": "https://site.api.espn.com/apis/site/v2/sports/soccer/aus.1/summary?event=401892144"
            },
            raw_label,
        )
    return dict(
        id=MID,
        home=record["home"],
        away=record["away"],
        competition="aus.1",
        status="FT90",
        kickoff=record["kickoff"],
        score=scores,
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--blind", required=True, type=Path)
    p.add_argument("--australia-blind", type=Path)
    p.add_argument("--data-repo", required=True, type=Path)
    args = p.parse_args()
    raw = args.blind.read_bytes()
    require(sha256(raw).hexdigest() == BLIND_SHA, "paired_input_digest")
    records = json.loads(raw)
    labels = certified(args.data_repo, records)
    if args.australia_blind:
        aus = args.australia_blind.read_bytes()
        lock = json.loads(Path(__file__).with_name("australia-lock.json").read_bytes())
        require(
            sha256(aus).hexdigest() == lock["blind_sha256"], "australia_blind_digest"
        )
        (record,) = json.loads(aus)
        records.append(record)
        labels.append(australia_label(args.data_repo, record))
    print(json.dumps(grade(records, labels), indent=2, sort_keys=True))
