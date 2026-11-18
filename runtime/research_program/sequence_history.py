"""Rebuild timestamp-separated team sequences and the fixed research market baseline."""

import argparse
from collections import Counter, defaultdict, deque
import csv
from datetime import datetime
import gzip
import hashlib
import importlib.util
from itertools import groupby
import io
import json
from pathlib import Path
import tarfile

import numpy as np
from scipy.stats import poisson

from .three_arm_model import _market_matrix, _quote_values

SOURCE_PREFIX = "WCPP/research/remaining-nine-20261105/raw/"
ORIGINAL_STUDY = Path("workspace-updates/20261118-three-arm/training")
SEASONS = ("2425", "2526")
WINDOW = 5


def _historical_inputs(data):
    # Reuse the versioned archive verifier and original forecast identity joins.
    path = data / ORIGINAL_STUDY / "fit_driver.py"
    spec = importlib.util.spec_from_file_location("retained_three_arm_archive", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    targets, _ = module.load_proxies(data)
    source, expected_sha = module.ARCHIVES["quotes"]
    archive_path = data / module.APPENDIX / source
    archive = archive_path.read_bytes()
    if hashlib.sha256(archive).hexdigest() != expected_sha:
        raise ValueError("football-data archive hash mismatch")
    games = []
    sources = {}
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        for member in bundle:
            if not member.name.startswith(SOURCE_PREFIX) or not member.name.endswith(".csv"):
                continue
            filename = member.name.removeprefix(SOURCE_PREFIX)
            raw = bundle.extractfile(member).read()
            reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
            for line_idx, row in enumerate(reader, 2):
                league = row.get("Div")
                if league is None or not league.strip():
                    continue
                if league not in module.LEAGUES or not all(row[k] for k in ("FTHG", "FTAG")):
                    raise ValueError(f"incomplete labeled source {filename}:{line_idx}")
                games.append(
                    dict(source_file=filename, source_line=line_idx, cohort=module.LEAGUES[league],
                         game_date=datetime.strptime(row["Date"], "%d/%m/%Y").date().isoformat(),
                         home=row["HomeTeam"], away=row["AwayTeam"],
                         home_goals=int(row["FTHG"]), away_goals=int(row["FTAG"]))
                )
            sources[filename] = hashlib.sha256(raw).hexdigest()
    return games, targets, {"archive_path": str(archive_path.relative_to(data)),
                            "archive_sha256": expected_sha, "csv_sha256": dict(sorted(sources.items()))}


def rolling_sequences(games, targets):
    """Construct histories before processing any outcomes of the target's calendar date."""
    wanted = {(row["source_file"], row["source_line"]): row for row in targets}
    if len(wanted) != len(targets):
        raise ValueError("duplicate target identity")
    team_histories = defaultdict(lambda: deque(maxlen=WINDOW))
    results = []
    for _, group in groupby(sorted(games, key=lambda g: g["game_date"]), key=lambda g: g["game_date"]):
        block = list(group)
        for g in block:
            target = wanted.get((g["source_file"], g["source_line"]))
            if target is None:
                continue
            if any(target[k] != g[k] for k in ("game_date", "cohort", "home", "away", "home_goals", "away_goals")):
                raise ValueError("target and original source row disagree")
            sequences, sources = [], []
            for team in (g["home"], g["away"]):
                prior = team_histories[g["cohort"], team]
                padding = WINDOW - len(prior)
                sequences.append([[0, 0, 0, 0] for _ in range(padding)] + [p[0] for p in prior])
                sources.append([None] * padding + [p[1] for p in prior])
            results.append(dict(target, sequence=sequences, sequence_sources=sources))
        for g in block:
            h, a = g["home_goals"], g["away_goals"]
            ref = [g["source_file"], g["source_line"], g["game_date"]]
            team_histories[g["cohort"], g["home"]].append(([h, a, 1, 1], ref))
            team_histories[g["cohort"], g["away"]].append(([a, h, 0, 1], ref))
    if len(results) != len(targets):
        raise ValueError("some target rows have no historical source")
    return sorted(results, key=lambda r: (r["game_date"], r["match_id"]))


def fixed_market_baseline(rates, market):
    """Project a preexisting goals-only PMF to the observed panel with a fixed unit weight."""
    values = poisson.pmf(np.arange(26)[:, None], np.asarray(rates)[None, :])
    joint = np.outer(values[:, 0], values[:, 1])
    if not np.isclose(joint.sum(), 1.0, rtol=0, atol=1e-10):
        raise ValueError("insufficient PMF support for original retained rates")
    return _market_matrix(joint / joint.sum(), _quote_values(market, None, proxy=True))


def prepare_rows(path):
    rows = []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            rows.append(dict(row, baseline_matrix=fixed_market_baseline(row["rates"], row["market"])))
    return rows


def prepare(data, out):
    games, targets, ledger = _historical_inputs(data)
    rows = rolling_sequences(games, targets)
    out.mkdir(parents=True, exist_ok=True)
    ledger.update(
        date_policy="Football-Data source calendar date; all matches of a date predicted before updating that date.",
        baseline="Fixed unit-weight market projection of retained out-of-time goals-only rates using "
                 "the available Bet365 closing 1X2 and total 2.5. The projection weight uses no fit labels.",
        sources_observed_at="Per-quote and per-result observations unavailable for European historical CSVs.",
        state_layout="home,away; previous five same-league fixtures oldest first, left padding; "
                     "goals for, goals against, home indicator, valid indicator.",
        population="Included rows follow the two prior archived European research cohorts. "
                   "All matches in the 35 league-season source CSV files may contribute prior state, including "
                   "matches omitted from the target prediction cohort. Both seasons were researched previously.",
        source_games=len(games), files={},
    )
    for season in SEASONS:
        sample = [r for r in rows if r["season"] == season]
        masks = Counter(sum(step[3] for side in r["sequence"] for step in side) for r in sample)
        path = out / f"samples-{season}.jsonl.gz"
        body = "".join(json.dumps(r, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
                       for r in sample)
        path.write_bytes(gzip.compress(body.encode(), mtime=0))
        ledger["files"][path.name] = dict(
            n=len(sample), dates=[sample[0]["game_date"], sample[-1]["game_date"]],
            valid_prior_steps_histogram=dict(sorted(masks.items())), sha256=hashlib.sha256(path.read_bytes()).hexdigest()
        )
    (out / "sequence-inputs.json").write_text(json.dumps(ledger, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.data.resolve(), args.out)
