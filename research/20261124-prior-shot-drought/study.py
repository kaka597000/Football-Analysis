"""Fit one team-side zero hurdle, seal both historical forecasts, then grade separately."""
import argparse
from hashlib import sha256
import json
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit, logit

from history import HERE, canonical, digest, read_rows, write_rows
from research_program.score_form import poisson_matrix
from research_program.three_arm_model import score_picks, top_three_scores


def file_hash(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def save(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def side_inputs(sequences):
    values = np.asarray(sequences)
    if (values.shape != (2, 6) or values.dtype.kind not in "iu" or (values < 0).any()):
        raise ValueError("six observed, nonnegative counts from each side required")
    means = values.mean(axis=1)
    volume = np.log((.5 + means.mean()) / 4.5)
    residual = ((values <= 1).mean(axis=1) - np.exp(-means) * (1 + means)).mean()
    return float(volume), float(residual)


def logistic_fit(x, y, offsets, bounds=None):
    def loss(beta):
        eta = offsets + x @ beta
        value = np.sum(np.logaddexp(0., eta) - y * eta) + 2.5 * (beta @ beta)
        return value, x.T @ (expit(eta) - y) + 5 * beta
    result = minimize(loss, np.zeros(x.shape[1]), jac=True, method="L-BFGS-B",
                      bounds=bounds, options={"ftol": 1e-14, "gtol": 1e-8})
    if not result.success:
        raise ValueError(f"zero-probability fit failed: {result.message}")
    return result.x


def fit(rows):
    values, outcomes, offsets = [], [], []
    for row in rows:
        if row["for_against"] is None or row["score"] is None:
            continue
        for i, sequences in enumerate(row["for_against"]):
            values.append(side_inputs(sequences))
            outcomes.append(int(row["score"][i] == 0))
            offsets.append(logit(np.exp(-row["rates"][i])))
    if not values:
        raise ValueError("no training data with complete lagged shot histories")
    volumes, drought = np.asarray(values).T
    y, offset = np.array(outcomes), np.array(offsets)
    common = np.column_stack((np.ones(len(y)), volumes))
    gamma = logistic_fit(common, y, offset)
    beta = logistic_fit(drought[:, None], y, offset + common @ gamma, [(-4., 4.)])
    return dict(n_training_fixtures=len(rows), eligible_training_fixtures=len(y) // 2,
                common_volume_coefficients=gamma.tolist(), drought_coefficient=float(beta[0]),
                protocol_sha256=file_hash(HERE / "protocol.json"))


def zero_probabilities(row, fitted):
    p = np.exp(-np.asarray(row["rates"], dtype=float))
    if row["for_against"] is None:
        return p, p.copy()
    g0, g1 = fitted["common_volume_coefficients"]
    features = np.array([side_inputs(s) for s in row["for_against"]])
    common = logit(p) + g0 + g1 * features[:, 0]
    return expit(common), expit(common + fitted["drought_coefficient"] * features[:, 1])


def matrix(rates, p_zero):
    prior = poisson_matrix(np.asarray(rates, dtype=float), (31, 31))
    margins = []
    for axis, blank in ((1, p_zero[0]), (0, p_zero[1])):
        pmf = prior.sum(axis=axis)
        pmf[1:] *= (1 - blank) / pmf[1:].sum()
        pmf[0] = blank
        margins.append(pmf)
    return np.outer(*margins)


def prediction(rates, p0, line):
    m = matrix(rates, p0)
    picks = score_picks(m, line=line)
    return {"p_zero": list(p0), "top": top_three_scores(m), "wdl": picks["wdl"],
            "total": picks["total"]["direction"] if picks["total"] else None,
            "matrix_sha256": sha256(np.ascontiguousarray(m.round(12), dtype="<f8")).hexdigest()}


def forecasts(rows, fitted):
    output = []
    for row in rows:
        if set(("score", "actual", "FTHG", "FTAG")).intersection(row):
            raise ValueError("outcome-bearing rows cannot be sealed as predictions")
        entry = {k: row[k] for k in ("id", "date", "league", "line", "rates")}
        entry.update(activated=row["for_against"] is not None, base=None, candidate=None)
        if row.get("freeze_sha"):
            entry["original_freeze_sha256"] = row["freeze_sha"]
        if row["rates"] is not None:
            p0, p1 = zero_probabilities(row, fitted)
            entry["base"] = prediction(row["rates"], p0, row["line"])
            entry["candidate"] = (entry["base"].copy() if np.array_equal(p0, p1)
                                  else prediction(row["rates"], p1, row["line"]))
        output.append(entry)
    return output


def seal(inputs, fitted, out, panel):
    fit_data = json.loads(fitted.read_bytes())
    if fit_data["protocol_sha256"] != file_hash(HERE / "protocol.json"):
        raise ValueError("different fit protocol")
    rows = read_rows(inputs)
    ids = sorted(r["id"] for r in rows)
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("empty or duplicate roster")
    out.mkdir(parents=True, exist_ok=True)
    forecast_file = out / "forecasts.jsonl"
    if forecast_file.exists() or (out / "lock.json").exists():
        raise FileExistsError("seal to a new directory to preserve the prior forecast lock")
    preds = forecasts(rows, fit_data)
    write_rows(forecast_file, preds)
    lock = dict(panel=panel, n=len(ids), n_predicted=sum(r["base"] is not None for r in preds),
                activated=sum(r["activated"] for r in preds), inputs_sha256=file_hash(inputs),
                roster_sha256=digest(ids), forecast_sha256=file_hash(forecast_file),
                fit_sha256=file_hash(fitted), protocol_sha256=file_hash(HERE / "protocol.json"))
    save(out / "lock.json", lock)
    print(canonical(lock))


def read_lock(lockpath, forecasts_path, panel):
    lock = json.loads(Path(lockpath).read_bytes())
    if (lock["protocol_sha256"] != file_hash(HERE / "protocol.json") or lock["panel"] != panel
            or lock["forecast_sha256"] != file_hash(forecasts_path)):
        raise ValueError("forecast/registration identity mismatch")
    rows = read_rows(forecasts_path)
    ids = sorted(r["id"] for r in rows)
    if (len(set(ids)) != len(ids) or len(ids) != lock["n"] or digest(ids) != lock["roster_sha256"]):
        raise ValueError("sealed roster identity mismatch")
    return rows


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="mode", required=True)
    train = sub.add_parser("fit")
    train.add_argument("--inputs", type=Path, required=True)
    train.add_argument("--out", type=Path, required=True)
    lock = sub.add_parser("seal")
    lock.add_argument("--inputs", type=Path, required=True)
    lock.add_argument("--fit", type=Path, required=True)
    lock.add_argument("--out", type=Path, required=True)
    lock.add_argument("--panel", choices=["historical", "clocked"], required=True)
    args = p.parse_args()
    if args.mode == "fit":
        result = fit(read_rows(args.inputs))
        result["training_inputs_sha256"] = file_hash(args.inputs)
        save(args.out, result)
        print(canonical(result))
    else:
        seal(args.inputs, args.fit, args.out, args.panel)
