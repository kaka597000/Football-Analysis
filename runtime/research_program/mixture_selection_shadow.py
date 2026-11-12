"""Calibration-only DC/ML mixture experiment; pure DC is an allowed choice.

This does not change ML-0.1 CONFIG, trained artifacts, or active routing. Callers
must supply independently generated, time-separated calibration forecasts.
"""
import math
import numpy as np

ALPHAS = (0.0, 0.25, 0.5, 0.75, 1.0)


def select(calibration_rows, *, log_loss_tolerance=0.0):
    if not calibration_rows or not math.isfinite(log_loss_tolerance) or log_loss_tolerance < 0:
        raise ValueError("nonempty calibration and finite nonnegative tolerance required")
    seen = set()
    for row in calibration_rows:
        if row["match_id"] in seen:
            raise ValueError("duplicate calibration fixture")
        seen.add(row["match_id"])
        a, b = np.asarray(row["dc"], float), np.asarray(row["ml"], float)
        h, v = row["home_goals"], row["away_goals"]
        if a.ndim != 2 or a.shape != b.shape or type(h) is not int or type(v) is not int or not (0 <= h < a.shape[0] and 0 <= v < a.shape[1]):
            raise ValueError("paired support and regulation score required")
        for m in (a, b):
            if not np.isfinite(m).all() or (m < 0).any() or not np.isclose(m.sum(), 1, atol=1e-9, rtol=0):
                raise ValueError("valid normalized probability matrix required")
    trials = []
    for alpha in ALPHAS:
        hits, loss = 0, 0.0
        for row in calibration_rows:
            m = (1 - alpha) * np.asarray(row["dc"], float) + alpha * np.asarray(row["ml"], float)
            actual = (row["home_goals"], row["away_goals"])
            hits += tuple(np.unravel_index(m.argmax(), m.shape)) == actual
            loss -= math.log(max(float(m[actual]), 1e-15))
        trials.append({"alpha": alpha, "hits": int(hits), "n": len(calibration_rows),
                       "score_log_loss": loss / len(calibration_rows)})
    valid = [t for t in trials if t["score_log_loss"] <= trials[0]["score_log_loss"] + log_loss_tolerance]
    best = min(valid, key=lambda t: (-t["hits"], t["score_log_loss"], t["alpha"]))
    return {"schema": "WCPP-MIXTURE-SELECTION-SHADOW-1", "selected_alpha": best["alpha"],
            "trials": trials, "active_model_changed": False, "prospective_validation": False,
            "partition": "SUPPLIED_CALIBRATION_ONLY_PROVENANCE_REQUIRES_SEPARATE_AUDIT"}
