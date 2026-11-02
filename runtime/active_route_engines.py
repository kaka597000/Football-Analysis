"""Explicit adapters for non-weighted active routes.

The adapters keep source artifacts immutable, fail closed on scope or identity
errors, and normalize genuine model matrices to the paired-output contract.
They do not create a comprehensive score or claim formal promotion.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
GENERIC_CONTRACT = "WCPP-GENERIC-POISSON-ROUTE-1"


def file_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def aware(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone required")
    return parsed.astimezone(timezone.utc)


def _prematch_clock(fixture, *, built_at=None):
    cutoff = aware(fixture["cutoff_at"])
    kickoff = aware(fixture["kickoff_at"])
    now = datetime.now(timezone.utc)
    if cutoff > now or cutoff >= kickoff or kickoff <= now:
        raise ValueError("genuinely future PreMatch required")
    if fixture.get("phase") != "PreMatch":
        raise ValueError("phase must be PreMatch")
    if built_at and aware(built_at) > cutoff:
        raise ValueError("model artifact was unavailable at cutoff")


def _normalized(raw, fixture, *, version, role, artifact_path,
                model_status=None, training_cutoff=None):
    """Return one common prediction schema without changing probabilities."""
    status = str(raw.get("status", model_status or "PREDICTED"))
    matrix = raw.get("matrix")
    if matrix is None or status.upper().startswith("ABSTAIN"):
        return {
            "version": version, "role": role, "status": status,
            "reason": raw.get("reason", "MODEL_ABSTAIN"), "input": fixture,
            "matrix": None, "primary": None, "formal_kpi_eligible": False,
            "training_data_cutoff": training_cutoff,
            "source_artifact_sha256": file_sha256(artifact_path),
            "comprehensive_primary_ready": False,
        }
    m = np.asarray(matrix, dtype=float)
    if m.ndim != 2 or not all(m.shape) or not np.isfinite(m).all() or (m < 0).any():
        raise ValueError("invalid routed score matrix")
    if not math.isclose(float(m.sum()), 1.0, rel_tol=0, abs_tol=1e-9):
        raise ValueError("routed score matrix is not normalized")
    primary = raw.get("primary", raw.get("statistical_top1"))
    if isinstance(primary, (list, tuple)):
        primary = f"{int(primary[0])}-{int(primary[1])}"
    if not isinstance(primary, str):
        index = np.unravel_index(np.argmax(m), m.shape)
        primary = f"{int(index[0])}-{int(index[1])}"
    h, a = map(int, primary.split("-"))
    ranked = np.argsort(-m.ravel(), kind="stable")[:3]
    wdl = {
        "主胜": float(np.tril(m, -1).sum()),
        "平局": float(np.trace(m)),
        "客胜": float(np.triu(m, 1).sum()),
    }
    result = dict(raw)
    result.update({
        "version": version, "role": role, "status": status,
        "input": fixture, "matrix": m.tolist(), "matrix_hash": digest(m.tolist()),
        "primary": primary, "primary_probability": float(m[h, a]),
        "top3": [{"score": f"{i // m.shape[1]}-{i % m.shape[1]}",
                  "probability": float(m.ravel()[i])} for i in ranked],
        "wdl": wdl, "wdl_reference": max(wdl, key=wdl.get),
        "training_data_cutoff": training_cutoff,
        "source_artifact_sha256": file_sha256(artifact_path),
        "primary_label": "统计参考比分；综合判断仍须独立完成",
        "comprehensive_primary_ready": False, "formal_kpi_eligible": False,
        "probability_calibration_status": "NOT_PROSPECTIVELY_VALIDATED",
    })
    return result


def predict_v4_compat(artifact_path, fixture):
    """Run an immutable WCPP-MAIN-1.1 artifact through the numerical-equivalent 1.2 core.

    MAIN-1.2 only added decision documentation around the 1.1 numerical path.
    The adapter records that compatibility boundary rather than rewriting the
    stored artifact or pretending it was retrained.
    """
    sys.path.insert(0, str(ROOT / "v4"))
    import main_model

    source = json.loads(Path(artifact_path).read_text())
    original_version = source.get("version")
    if original_version not in ("WCPP-MAIN-1.1", "WCPP-MAIN-1.2"):
        raise ValueError("unsupported main artifact version")
    adapted = dict(source)
    adapted["version"] = main_model.VERSION
    adapted["code_hash"] = main_model.code_hash()
    model_fixture = dict(fixture)
    supplied_date = model_fixture.pop("date", None)
    if supplied_date and supplied_date != aware(fixture["kickoff_at"]).date().isoformat():
        raise ValueError("fixture date conflicts with kickoff timestamp")
    result = main_model.predict(adapted, model_fixture)
    # Preserve the one common paired input after the legacy engine derives the
    # same calendar date from kickoff_at.
    result["input"] = fixture
    result["version"] = original_version + "+MAIN1.2-DIAGNOSTIC-ADAPTER"
    result["status"] = "PREDICTED_RESEARCH_ONLY"
    result["artifact_original_version"] = original_version
    result["execution_adapter"] = {
        "version": "WCPP-MAIN-1.1-COMPAT-1",
        "numerical_path_change": False,
        "added_diagnostics": "MAIN-1.2 decision audit",
        "date_adapter": "removed redundant date field after equality check",
        "source_artifact_immutable": True,
    }
    result["source_artifact_sha256"] = file_sha256(artifact_path)
    result["formal_kpi_eligible"] = False
    return result


def predict_systematic_candidate(artifact_path, fixture):
    sys.path.insert(0, str(ROOT / "optimization_tools"))
    import candidate

    _prematch_clock(fixture, built_at=json.loads(Path(artifact_path).read_text())["created_at"])
    raw = candidate.calculate(artifact_path, fixture)
    artifact = json.loads(Path(artifact_path).read_text())
    return _normalized(raw, fixture, version=raw.get("model", artifact["version"]),
                       role="TEST_SYSTEMATIC_RESEARCH", artifact_path=artifact_path,
                       training_cutoff=artifact["core"]["fit_end"])


def predict_aus_expert(artifact_path, fixture):
    sys.path.insert(0, str(ROOT))
    from aus_expert.runner import calculate

    artifact = json.loads(Path(artifact_path).read_text())
    raw = calculate(artifact_path, fixture)
    return _normalized(raw, fixture, version=raw.get("model", artifact["version"]),
                       role="TEST_AUS_EXPERT_RESEARCH", artifact_path=artifact_path,
                       training_cutoff=artifact["core"]["fit_end"])


def predict_generic_poisson(artifact_path, fixture):
    """Predict from a scoped constant or regularized team-Poisson artifact."""
    artifact_path = Path(artifact_path)
    artifact = json.loads(artifact_path.read_text())
    if artifact.get("engine_contract") != GENERIC_CONTRACT:
        raise ValueError("generic route artifact contract mismatch")
    manifest = Path(artifact["dataset_manifest"])
    if file_sha256(manifest) != artifact["dataset_manifest_sha256"]:
        raise ValueError("generic route dataset manifest changed")
    _prematch_clock(fixture, built_at=artifact["created_at"])
    scope = artifact["forecast_scope"]
    if (fixture.get("cohort") != scope["cohort"]
            or fixture.get("competition") != scope["competition"]
            or fixture.get("gender") not in ("male", "men")
            or fixture.get("age") != "senior"
            or fixture.get("stage") not in scope["stages"]):
        raise ValueError("generic route scope mismatch")
    if fixture["date"] <= artifact["core"]["fit_end"]:
        raise ValueError("target must be after fitted history")
    sys.path.insert(0, str(ROOT / "optimization"))
    from models import matrix, rates
    from schema import fixture as safe_fixture

    kind = artifact["model_kind"]
    if kind == "constant":
        mu = np.asarray(artifact["core"]["constant"], dtype=float)
    elif kind == "team_poisson":
        missing = [fixture[k] for k in ("home", "away")
                   if fixture[k] not in artifact["core"]["teams"]]
        if missing:
            return _normalized({"status": "ABSTAIN_COLD_START_TEAM", "reason": ",".join(missing)},
                               fixture, version=artifact["version"],
                               role=artifact["role"], artifact_path=artifact_path,
                               training_cutoff=artifact["core"]["fit_end"])
        mu = rates(artifact["core"], [safe_fixture(fixture)])[0]
    else:
        raise ValueError("unknown generic route model kind")
    m = matrix(mu)
    raw = {
        "status": "PREDICTED_RESEARCH_ONLY", "matrix": m.tolist(),
        "expected_goals": [float(mu[0]), float(mu[1])],
        "statistical_top1": list(map(int, np.unravel_index(m.argmax(), m.shape))),
        "training_n": artifact["core"]["fit_n"],
        "model_kind": kind, "trained_xg_channel": False,
        "injury_status": "NOT_TRAINED_NO_ASOF_HISTORY",
    }
    return _normalized(raw, fixture, version=artifact["version"], role=artifact["role"],
                       artifact_path=artifact_path,
                       training_cutoff=artifact["core"]["fit_end"])


def align_matrix_support(current, test):
    """Zero-pad finite score support so paired diagnostics compare equal axes."""
    if current.get("matrix") is None or test.get("matrix") is None:
        return {"status": "NOT_APPLIED_ABSTAIN", "probability_values_changed": False}
    matrices = [np.asarray(x["matrix"], dtype=float) for x in (current, test)]
    rows = max(x.shape[0] for x in matrices)
    cols = max(x.shape[1] for x in matrices)
    audit = {"status": "ALREADY_EQUAL", "target_shape": [rows, cols],
             "method": "ZERO_PAD_ONLY", "probability_values_changed": False,
             "tail_probability_added": 0.0, "models": {}}
    for label, prediction, matrix in zip(("current", "test"), (current, test), matrices):
        original_hash = digest(matrix.tolist())
        if matrix.shape != (rows, cols):
            padded = np.zeros((rows, cols), dtype=float)
            padded[:matrix.shape[0], :matrix.shape[1]] = matrix
            prediction["matrix"] = padded.tolist()
            prediction["matrix_hash"] = digest(prediction["matrix"])
            prediction["matrix_support_adapter"] = {
                "method": "ZERO_PAD_ONLY", "original_shape": list(matrix.shape),
                "target_shape": [rows, cols], "original_matrix_hash": original_hash,
                "probability_values_changed": False, "tail_probability_added": 0.0,
            }
            audit["status"] = "ZERO_PAD_APPLIED"
        audit["models"][label] = {"original_shape": list(matrix.shape),
                                   "original_matrix_hash": original_hash}
    return audit
