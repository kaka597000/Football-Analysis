"""Build scoped route artifacts for AUS regular season and AFC Elite league phase.

This is an auditable one-time builder. It never changes active-model.json; the
registry switch is a separate reviewed step.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "optimization"))
from models import fit_core, matrix, rates
from schema import file_hash

WCPP = Path("/workspace/WCPP")
OUT = WCPP / "models/active-routes-20261027"
AFC_SOURCE = WCPP / "reports/afc-history-20261013/afc-training-90.json"
AUS_DATASET = WCPP / "reports/aus-expert-20261016/dataset-boundary-refit-v1"
CONTRACT = "WCPP-GENERIC-POISSON-ROUTE-1"


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)


def load_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def generic_artifact(*, version, role, kind, core, dataset_manifest, scope,
                     source_evidence):
    return {
        "version": version,
        "role": role,
        "status": "ACTIVE_RESEARCH_ROUTE_NOT_FORMALLY_PROMOTED",
        "formal_kpi_eligible": False,
        "engine_contract": CONTRACT,
        "model_kind": kind,
        "core": core,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset_manifest": str(dataset_manifest),
        "dataset_manifest_sha256": file_hash(dataset_manifest),
        "forecast_scope": scope,
        "source_evidence": source_evidence,
        "trained_xg": False,
        "trained_injuries": False,
        "trained_lineups": False,
        "comprehensive_primary": "SEPARATE_EVIDENCE_TRACK_REQUIRED",
        "target_attainment_proven": False,
    }


def metrics(core, rows, kind):
    records = []
    for row in rows:
        mu = (np.asarray(core["constant"], dtype=float) if kind == "constant"
              else rates(core, [row])[0])
        m = matrix(mu)
        actual = (row["home_goals"], row["away_goals"])
        pick = tuple(map(int, np.unravel_index(m.argmax(), m.shape)))
        wdl = np.asarray([np.tril(m, -1).sum(), np.trace(m), np.triu(m, 1).sum()])
        actual_wdl = 0 if actual[0] > actual[1] else 1 if actual[0] == actual[1] else 2
        records.append({
            "match_id": row["match_id"], "date": row["date"],
            "primary": list(pick), "actual": list(actual), "hit": pick == actual,
            "score_logloss": -math.log(max(float(m[actual]), 1e-300)),
            "wdl_hit": int(np.argmax(wdl)) == actual_wdl,
            "expected_total": float(mu.sum()), "actual_total": sum(actual),
        })
    return {
        "n": len(records), "top1_hits": sum(x["hit"] for x in records),
        "top1": sum(x["hit"] for x in records) / len(records),
        "score_logloss": sum(x["score_logloss"] for x in records) / len(records),
        "wdl_accuracy": sum(x["wdl_hit"] for x in records) / len(records),
        "total_bias": sum(x["expected_total"] - x["actual_total"] for x in records) / len(records),
        "records": records,
    }


def build_aus():
    fixtures = load_jsonl(AUS_DATASET / "fixtures.jsonl")
    labels = {x["match_id"]: x for x in load_jsonl(AUS_DATASET / "labels.jsonl")}
    rows = [{**x, **labels[x["match_id"]]} for x in fixtures]
    core = {
        "constant": [float(np.mean([x["home_goals"] for x in rows])),
                     float(np.mean([x["away_goals"] for x in rows]))],
        "fit_end": rows[-1]["date"], "fit_n": len(rows),
    }
    artifact = generic_artifact(
        version="WCPP-AUS-CURRENT-CONSTANT-0.1",
        role="CURRENT_AUS_SIMPLE_BASELINE",
        kind="constant", core=core, dataset_manifest=AUS_DATASET / "manifest.json",
        scope={"cohort": "aus.1", "competition": "aus.1",
               "gender": "men", "age": "senior",
               "stages": ["regular", "regular-season"]},
        source_evidence={
            "dataset": str(AUS_DATASET),
            "reason": "Simple independent current baseline; test route uses the separate AUS team expert",
            "neutral_policy": "UNKNOWN_PRESERVED_IN_DATASET; constant baseline does not infer venue",
        })
    path = OUT / "aus.1/current-artifact.json"
    save(path, artifact)
    return {"artifact": str(path), "sha256": file_hash(path), "fit_n": len(rows),
            "fit_end": core["fit_end"], "constant": core["constant"]}


def build_afc():
    source = json.loads(AFC_SOURCE.read_text())
    selected = sorted((x for x in source if x["competition"] == "afc.champions"
                       and x["era"] == "ELITE" and x["phase"] == "league_phase"
                       and x["training_eligible"] == 1),
                      key=lambda x: (x["date"], x["match_id"]))
    cohort = "afc.champions:ELITE:league_phase"
    fixtures = []
    labels = []
    rows = []
    for x in selected:
        fixture = {
            "match_id": x["match_id"], "cohort": cohort,
            "competition": "afc.champions", "date": x["date"],
            "season": x["season"], "home": x["home_id"], "away": x["away_id"],
            "neutral": x["neutral"], "stage": "league_phase",
            "gender": "male", "age": "senior",
        }
        label = {"match_id": x["match_id"], "home_goals": x["h90"],
                 "away_goals": x["a90"], "first_known_at": None,
                 "source": "AFC classified accepted regulation-90 history"}
        fixtures.append(fixture); labels.append(label); rows.append({**fixture, **label})
    dataset = OUT / "afc.champions-ELITE-league_phase/dataset"
    dataset.mkdir(parents=True, exist_ok=False)
    for name, values in (("fixtures.jsonl", fixtures), ("labels.jsonl", labels),
                         ("facts.jsonl", [])):
        with (dataset / name).open("x") as handle:
            for value in values:
                handle.write(json.dumps(value, ensure_ascii=False) + "\n")
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": str(AFC_SOURCE), "source_sha256": file_hash(AFC_SOURCE),
        "cohort": cohort, "competition": "afc.champions", "era": "ELITE",
        "phase": "league_phase", "score_scope": "regulation_90",
        "rows": len(rows), "first_known_at_policy": "MISSING_RETROSPECTIVE_ONLY",
        "neutral_policy": "THREE_STATE_PRESERVED",
        "excluded": "legacy AFC Champions League, knockout phases and AFC Champions League Two",
    }
    save(dataset / "manifest.json", manifest)

    fit_rows = [x for x in rows if x["date"] < "2026-01-01"]
    audit_rows = [x for x in rows if x["date"] >= "2026-01-01"]
    if len(fit_rows) < 80 or not audit_rows:
        raise ValueError("AFC temporal validation split unavailable")
    validation_constant = {
        "constant": [float(np.mean([x["home_goals"] for x in fit_rows])),
                     float(np.mean([x["away_goals"] for x in fit_rows]))],
        "fit_end": fit_rows[-1]["date"], "fit_n": len(fit_rows),
    }
    validation_team = fit_core(fit_rows, half_life=365, ridge=20)
    validation = {
        "mode": "RETROSPECTIVE_FIXED_CONFIGURATION_DIAGNOSTIC_NOT_PROMOTION",
        "fit_before": "2026-01-01", "audit_start": "2026-01-01",
        "already_available_history_not_future_unseen": True,
        "current_constant": metrics(validation_constant, audit_rows, "constant"),
        "test_team_poisson": metrics(validation_team, audit_rows, "team_poisson"),
        "target_attainment_proven": False,
    }
    save(OUT / "afc.champions-ELITE-league_phase/validation.json", validation)

    constant_core = {
        "constant": [float(np.mean([x["home_goals"] for x in rows])),
                     float(np.mean([x["away_goals"] for x in rows]))],
        "fit_end": rows[-1]["date"], "fit_n": len(rows),
    }
    team_core = fit_core(rows, half_life=365, ridge=20)
    scope = {"cohort": cohort, "competition": "afc.champions",
             "gender": "men", "age": "senior", "stages": ["league_phase"]}
    evidence = {
        "dataset": str(dataset), "source": str(AFC_SOURCE),
        "identity_isolation": "ELITE + league_phase only",
        "first_known_at": "missing; retrospective history; not prospective validation",
    }
    current = generic_artifact(
        version="WCPP-AFC-ELITE-CURRENT-CONSTANT-0.1",
        role="CURRENT_AFC_ELITE_SIMPLE_BASELINE", kind="constant",
        core=constant_core, dataset_manifest=dataset / "manifest.json",
        scope=scope, source_evidence=evidence)
    test = generic_artifact(
        version="WCPP-AFC-ELITE-TEST-POISSON-0.1",
        role="TEST_AFC_ELITE_TEAM_POISSON", kind="team_poisson",
        core=team_core, dataset_manifest=dataset / "manifest.json",
        scope=scope, source_evidence=evidence)
    current_path = OUT / "afc.champions-ELITE-league_phase/current-artifact.json"
    test_path = OUT / "afc.champions-ELITE-league_phase/test-artifact.json"
    save(current_path, current); save(test_path, test)
    return {
        "dataset_rows": len(rows), "fit_end": rows[-1]["date"],
        "current_artifact": str(current_path), "current_sha256": file_hash(current_path),
        "test_artifact": str(test_path), "test_sha256": file_hash(test_path),
        "validation": str(OUT / "afc.champions-ELITE-league_phase/validation.json"),
    }


def main():
    OUT.mkdir(parents=True, exist_ok=False)
    result = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "builder": str(Path(__file__).resolve()), "builder_sha256": file_hash(__file__),
        "aus": build_aus(), "afc_elite": build_afc(),
        "activation_performed": False, "formal_promotion": False,
    }
    save(OUT / "build-report.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
