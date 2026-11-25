"""Fit and score the registered fixture calendar experiment on archived real panels."""

import argparse
from collections import defaultdict
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from . import all_scope_model as base
from . import continuation_report as report
from . import continuation_scores as r7
from . import continuation_study as archive
from . import r10_shot_model as decision
from . import r8_score_study as control
from . import schedule_density as model
from . import schedule_density_results
from .all_scope_study import check_panel
from .goal_state_study import sha, write_new
from .r9_score_study import verified_archive


PROTOCOL = Path(__file__).resolve().parents[2] / "research/20261125-r17-schedule/protocol.json"
PROTOCOL_SHA = "810439de79b0c7eb1e050beb383bdafa6fcd4dbb683b360d375dd2b88f048d96"
OLDER = ("eng.1", "esp.1", "fra.1", "ger.1", "ita.1", "ned.1")
OLDER_BOUNDS = ["2017-07-01", "2018-07-01", "2019-07-01"]
SETTLEMENTS = ("FULL_WIN", "HALF_WIN", "PUSH", "HALF_LOSS", "FULL_LOSS")


def predict(prior, x, fitted, doses, line, original, original_probability):
    full = model.apply(prior, x, fitted)
    candidate, heads = decision.decisions(prior, full, doses, line, original)
    matrix = heads["score"]
    home, away = map(int, candidate["primary"].split("-"))
    probability = original_probability if matrix is prior else float(matrix[home, away])
    return candidate, probability, original if full is prior else base.pick(full, line)


def cohort_batches(rows):
    days = defaultdict(list)
    for row in rows:
        days[row["cohort"], row["date"]].append(row)
    results = []
    for (code, day), games in sorted(days.items()):
        batch = dict(cohort=code, date=day, population=len(games), seats={})
        for arm in ("candidate", "r7"):
            ranked = sorted(games, key=lambda g: (-g["primary_probability"][arm], g["id"]))[:3]
            seats = []
            for i, row in enumerate(ranked, 1):
                pick = row["forecast"][arm]
                exact = "-".join(map(str, row["actual"]))
                seats.append(dict(seat=i, id=row["id"], primary=pick["primary"], first_hit=pick["primary"] == exact,
                                  ordered_three_hit=exact in pick["top3"]))
            batch["seats"][arm] = seats
        results.append(batch)
    return results


def summary(rows, batches):
    arms = ("candidate", "r7", "market", "fixed_full")
    selected = {}
    for arm in ("candidate", "r7"):
        seats = [g["seats"][arm] for g in batches]
        order = []
        for i in range(3):
            filled = [group[i] for group in seats if len(group) > i]
            order.append(dict(seat=i+1, filled=len(filled), first=sum(s["first_hit"] for s in filled),
                              ordered_three=sum(s["ordered_three_hit"] for s in filled)))
        selected[arm] = dict(ordered_seats=order, full_batches=len([v for v in seats if len(v) == 3]),
            short_batches=len([v for v in seats if len(v) < 3]), planned_seats=3*len(seats),
            filled_seats=sum(map(len, seats)),
            all_three_first=sum(len(v) == 3 and all(s["first_hit"] for s in v) for v in seats))
    rates = {}
    for arm in arms:
        rate = report.rates(rows, arm)
        rate["settlements"] = {s:rate["settlements"].get(s, 0) for s in SETTLEMENTS}
        rate["issued"] = {key:sum(r["forecast"][arm].get(field) is not None for r in rows) for key,field in
                          (("score_top1","primary"),("score_top3","top3"),("wdl","wdl"),("actual_ou","total_side"))}
        rates[arm] = rate
    pairs = {}
    for arm in ("r7", "market"):
        paired = report.paired(rows, arm, "candidate")
        for metric in paired.values():
            metric["gained"] = (metric["disagreements"] + metric["net"]) // 2
            metric["lost"] = (metric["disagreements"] - metric["net"]) // 2
        pairs["vs_"+arm] = paired
    return dict(fixtures=len(rows), actual_lines=sum(r["line"] is not None for r in rows), rates=rates, **pairs,
                any_calendar_signal=sum(r["calendar_signal"] for r in rows),
                both_within_14=sum(all(side["games_in_14"] for side in r["coverage"].values()) for r in rows),
                ranked=selected)


def replay(panel, code, published, mode, out, r5, manifest):
    games = check_panel(panel, code)
    withheld = schedule_density_results.verify(games, r5, manifest)
    usable = np.asarray([g["id"] not in withheld for g in games], dtype=bool)
    meta = control.read(published / "fit.json")
    bounds = meta["bounds"]
    panel_sha = sha(panel)
    if mode == "independent2019":
        if meta["panel_sha256"] != [panel_sha] or bounds != OLDER_BOUNDS:
            raise ValueError("old panel or chronology changed")
        settings = control.read(published / "r7-control.json")
        for g in games:
            role = "train" if g["date"] < bounds[0] else "weight_selection" if g["date"] < bounds[1] else "test"
            if g["independent_split_role"] != role or g["date"] >= bounds[2]:
                raise ValueError("old calendar split differs")
    else:
        if meta["source_panel_sha256"] != panel_sha or meta["competition"] != code:
            raise ValueError("R5 panel differs from published R7 inputs")
        settings = meta
    cut, test = (sum(g["date"] < b for g in games) for b in bounds[:2])
    if not 0 <= cut <= test <= len(games) or not games:
        raise ValueError("invalid original calendar boundaries")
    scores = np.array([(g["home_goals"], g["away_goals"]) for g in games])
    anchor, z, _ = r7.inputs(games, code)
    x, coverage, history = model.walk(games, withheld)
    train = np.flatnonzero(usable[:cut])
    fitted = model.fit(anchor[train], x[train], scores[train])
    priors = control.matrices(anchor[cut:], z[cut:], settings)
    val = priors[:test - cut]
    full = [model.apply(p, v, fitted) for p, v in zip(val, x[cut:test], strict=True)]
    validation = np.flatnonzero(usable[cut:test])
    doses, trials = decision.choose([games[cut+i] for i in validation], val[validation], [full[i] for i in validation])
    out.mkdir(parents=True, exist_ok=False)
    write_new(out / "fit.json", dict(version=model.VERSION, competition=code, panel_sha256=panel_sha,
              r7_fit_sha256=sha(published / ("r7-control.json" if mode == "independent2019" else "fit.json")),
              bounds=bounds, train_games=len(train), validation_games=len(validation), withheld=withheld,
              registered_games=len(games), through_train=games[cut-1]["date"] if cut else None,
              through_validation=games[test-1]["date"] if test > cut else None,
              fitted=fitted, doses=doses, validation_trials=trials))
    if mode == "main":
        recipe = published / "forward.json"
        through = games[-1]["date"]
        if control.read(recipe)["through"] != through:
            raise ValueError("R7 forward recipe chronology differs")
        write_new(out / "forward.json", dict(version=model.VERSION, competition=code, through=through,
                  fit_sha256=sha(out / "fit.json"), r7_forward_sha256=sha(recipe), doses=doses,
                  calendar=history.snapshot(date.fromisoformat(through).toordinal()),
                  fitted=model.fit(anchor[usable], x[usable], scores[usable])))
    refs = archive.read_gzip(published / "forecasts.jsonl.gz")
    records = []
    for i, (g, ref) in enumerate(zip(games[test:], refs, strict=True), test):
        window = ("evaluation" if g["date"] < bounds[2] else "recent") if mode == "main" else mode
        line = base.actual_line(g)
        identity = dict(id=g["id"], cohort=code, stage=g["stage"], date=g["date"], home=g["home"],
                        away=g["away"], actual=scores[i].tolist(), line=line, source_sha256=g["source_sha256"])
        if any(ref[k] != v for k, v in identity.items()) or (mode == "main" and ref["window"] != window):
            raise ValueError("published reference identity, outcome or actual line differs: " + g["id"])
        if not usable[i]:
            continue
        original_arm = "candidate" if mode == "main" else "r7"
        original = ref["forecast"][original_arm]
        original_p = ref["quality"][original_arm]["p_first"]
        chosen, probability, full_pick = predict(priors[i-cut], x[i], fitted, doses, line, original, original_p)
        records.append(dict(identity, window=window, coverage=coverage[i], calendar_signal=bool(x[i].any()),
                       primary_probability=dict(r7=original_p, candidate=probability),
                       forecast=dict(r7=original, candidate=chosen, fixed_full=full_pick, market=ref["forecast"]["market"])))
    archive.put(out / "forecasts.jsonl.gz", records)
    return records


def run(r5, r7_dir, r10, scope, out):
    if sha(PROTOCOL) != PROTOCOL_SHA:
        raise ValueError("calendar study protocol changed")
    manifests = [control.read(d / "manifest.json") for d in (r5, r7_dir, r10)]
    codes = [v["code"] for v in control.read(scope)["competitions"]]
    if manifests[1]["scope"] != codes:
        raise ValueError("R7 league population changed")
    out.mkdir(parents=True, exist_ok=False)
    windows = defaultdict(list)
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name in ("inputs", "replay"):
            verified_archive(r10, manifests[2], name+"/independent.tar.gz", root/name)
        for code in codes:
            model_archive = "models/"+code+".tar.gz"
            verified_archive(r5, manifests[0], model_archive, root/code/"r5")
            verified_archive(r7_dir, manifests[1], model_archive, root/code/"r7")
            rows = replay(root/code/"r5/panel.jsonl", code, root/code/"r7/main", "main", out/"main"/code, r5, manifests[0])
            if code in OLDER:
                rows += replay(root/"inputs/panels"/(code+".jsonl"), code, root/"replay/independent"/code,
                               "independent2019", out/"independent2019"/code, r5, manifests[0])
            for row in rows:
                windows[row["window"]].append(row)
    result = {}
    for window, rows in sorted(windows.items()):
        batches = cohort_batches(rows)
        archive.put(out / (window+"-seats.jsonl.gz"), batches)
        result[window] = dict(overall=summary(rows, batches), competitions={code:summary(
            [r for r in rows if r["cohort"] == code], [b for b in batches if b["cohort"] == code]) for code in codes})
    write_new(out/"metrics.json", result)
    write_new(out/"inputs.json", dict(version=model.VERSION, protocol_sha256=PROTOCOL_SHA, scope_sha256=sha(scope),
              manifests={name:sha(folder/"manifest.json") for name, folder in (("r5",r5),("r7",r7_dir),("r10",r10))}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("r5", "r7", "r10", "out"):
        parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--scope", type=Path, default=Path("configs/analysis-scope.json"))
    args = parser.parse_args()
    run(args.r5, args.r7, args.r10, args.scope, args.out)
