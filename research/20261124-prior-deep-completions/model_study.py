"""One offset term for attacking entries on a fixed, past-only score-form control."""

import argparse
import gzip
import itertools
import json
import math
from collections import defaultdict, deque
from datetime import date
from pathlib import Path

import numpy as np
from research_program import score_form
from scipy.optimize import minimize_scalar
from sklearn.linear_model import PoissonRegressor

HERE = Path(__file__).resolve().parent


def prior_observation(game, side):
    other = 1 - side
    return dict(
        id=game["id"],
        date=game["date"],
        gf=game["goals"][side],
        ga=game["goals"][other],
        xgf=game["xg"][side],
        xga=game["xg"][other],
        deep_for=game["deep"][side],
        deep_against=game["deep"][other],
    )


def entry_value(games, field):
    values = [
        g[field]
        for g in games
        if g["deep_for"] is not None and g["deep_against"] is not None
    ]
    if len(values) < 3:
        return None
    return math.log1p((sum(values) + 4 * 6.0) / (len(values) + 4))


def describe(game, histories):
    day = date.fromisoformat(game["date"])
    h = list(histories[game["home_id"]])
    a = list(histories[game["away_id"]])
    hx = score_form.summarize(h, day)
    ax = score_form.summarize(a, day)
    entries = [
        entry_value(h, "deep_for"),
        entry_value(a, "deep_against"),
        entry_value(a, "deep_for"),
        entry_value(h, "deep_against"),
    ]
    pressure = (
        None
        if None in entries
        else [(entries[0] + entries[1]) / 2, (entries[2] + entries[3]) / 2]
    )
    return dict(
        game=game,
        base=score_form.features(hx, ax),
        pressure=pressure,
        history_ids=[[x["id"] for x in side] for side in (h, a)],
        latest_dates=[side[0]["date"] if side else None for side in (h, a)],
    )


def examples(season, future=False):
    histories = defaultdict(lambda: deque(maxlen=score_form.WINDOW))
    output = []
    for _, games in itertools.groupby(
        sorted(season["completed"], key=lambda g: (g["date"], g["id"])),
        key=lambda g: g["date"],
    ):
        group = list(games)
        if not future:
            output.extend(describe(g, histories) for g in group)
        for g in group:
            histories[g["home_id"]].appendleft(prior_observation(g, 0))
            histories[g["away_id"]].appendleft(prior_observation(g, 1))
    if future:
        for game in season["scheduled"]:
            if "2026-11-02" <= game["date"] < "2026-11-22":
                output.append(describe(game, histories))
    return output


def fit(training):
    x = np.vstack([r["base"] for r in training])
    goals = np.concatenate([r["game"]["goals"] for r in training])
    base = PoissonRegressor(alpha=0.01, max_iter=1000, tol=1e-9).fit(x, goals)
    eligible = [r for r in training if r["pressure"] is not None]
    raw = np.concatenate([r["pressure"] for r in eligible])
    mean, scale = float(raw.mean()), float(raw.std())
    if scale <= 0:
        raise ValueError("No variation in the training covariate")
    feature = (raw - mean) / scale
    rates = base.predict(np.vstack([r["base"] for r in eligible]))
    y = np.concatenate([r["game"]["goals"] for r in eligible])

    # Y log(base-rate) is constant in beta. Fit only the new offset coefficient.
    def objective(beta):
        return (
            np.sum(rates * np.exp(beta * feature) - y * beta * feature) + 0.5 * beta**2
        )

    optim = minimize_scalar(
        objective, bounds=(-0.5, 0.5), method="bounded", options={"xatol": 1e-12}
    )
    if not optim.success:
        raise ValueError("Offset coefficient did not converge")
    return dict(
        intercept=float(base.intercept_),
        base_weights=base.coef_.tolist(),
        training_matches=len(training),
        eligible_training_matches=len(eligible),
        training_through=max(r["game"]["date"] for r in training),
        iterations=base.n_iter_,
        pressure_mean=mean,
        pressure_std=scale,
        beta=float(optim.x),
    )


def forecast(example, model):
    mu = np.exp(
        model["intercept"] + example["base"] @ np.asarray(model["base_weights"])
    )
    new = mu.copy()
    if example["pressure"] is not None:
        z = (np.asarray(example["pressure"]) - model["pressure_mean"]) / model[
            "pressure_std"
        ]
        new = mu * np.exp(model["beta"] * z)
    return mu.tolist(), new.tolist()


def write_rows(path, records):
    data = "".join(
        json.dumps(r, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
        for r in records
    )
    path.write_bytes(gzip.compress(data.encode(), mtime=0))


def run(input_dir, out):
    protocol = json.loads((HERE / "protocol.json").read_bytes())
    seasons = json.loads(
        gzip.decompress((input_dir / "team-inputs.json.gz").read_bytes())
    )
    out.mkdir(parents=True, exist_ok=True)
    models, rows = {}, []
    for code in protocol["covered_source"]:
        training = []
        for season in protocol["training_season_start"]:
            training.extend(examples(seasons[f"{code}:{season}"]))
        model = fit(training)
        models[code] = model
        for group, year in (("historical", 2025), ("source_timed", 2026)):
            for ex in examples(
                seasons[f"{code}:{year}"], future=group == "source_timed"
            ):
                game = ex["game"]
                base, candidate = forecast(ex, model)
                # The strict scoring target and all its market data live outside the fitting input.
                record = {
                    k: game[k]
                    for k in (
                        "id",
                        "scheduled",
                        "date",
                        "home",
                        "away",
                        "home_id",
                        "away_id",
                    )
                }
                record.update(
                    cohort=code,
                    window=group,
                    base=base,
                    candidate=candidate,
                    signal_used=ex["pressure"] is not None,
                    latest_dates=ex["latest_dates"],
                    history_ids=ex["history_ids"],
                )
                if group == "historical":
                    record["actual"] = game["goals"]
                rows.append(record)
    (out / "fit.json").write_text(json.dumps(models, sort_keys=True, indent=2) + "\n")
    write_rows(out / "estimates.jsonl.gz", rows)
    print(
        "Fitted competition controls and offsets",
        len(models),
        "research forecasts",
        len(rows),
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--inputs", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    arg = ap.parse_args()
    run(arg.inputs, arg.out)
