"""One additional past-match signal on the Current score matrix."""

from collections import defaultdict
from datetime import timedelta
import math

import numpy as np
from scipy.optimize import brentq

from optimization.evaluate import settle_ou
from research.analysis_freeze import aware, digest
from research_program.three_arm_model import top_three_scores
from v2.model import probabilities, total_settlement


def count(value):
    text = str(value).strip()
    return int(text) if text.isascii() and text.isdecimal() else None


def score(raw):
    h, a = count(raw.get("FTHG")), count(raw.get("FTAG"))
    if h is None or a is None:
        return None
    if raw.get("FTR") != ("H" if h > a else "A" if a > h else "D"):
        raise ValueError("full-time score and outcome disagree")
    return h, a


class PriorGames:
    def __init__(self, season, cutoff):
        self.cutoff = cutoff
        self.training = []
        self.by_team = defaultdict(list)
        paired_corners = []
        seen = set()
        for game in sorted(season, key=lambda g: g["day"]):
            day, home, away = game["day"], game["home"], game["away"]
            if day >= cutoff - timedelta(days=1):
                continue
            raw = game["raw"]
            goals = score(raw)
            if goals is None:
                continue
            if home == away or {(day, home), (day, away)} & seen:
                raise ValueError("ambiguous same-day team identity")
            seen.update(((day, home), (day, away)))
            self.training.append(
                dict(date=day.isoformat(), home=home, away=away, home_goals=goals[0], away_goals=goals[1])
            )
            h, a = count(raw.get("HC")), count(raw.get("AC"))
            self.by_team[home].append((day, 0, h, a))
            self.by_team[away].append((day, 1, a, h))
            if h is not None and a is not None:
                paired_corners.append((h, a))
        self.means = None
        if len(paired_corners) >= 60:
            means = np.mean(paired_corners, axis=0)
            if np.all(means > 0):
                self.means = means

    def signal(self, home, away):
        if self.means is None:
            return None
        totals = []
        for club in (home, away):
            six = self.by_team.get(club, [])[-6:]
            if len(six) != 6 or (self.cutoff - six[0][0]).days > 90:
                return None
            earned = conceded = 0.0
            for _, venue, cf, ca in six:
                if cf is None or ca is None:
                    return None
                earned += cf / self.means[venue]
                conceded += ca / self.means[1 - venue]
            totals.append((earned, conceded))
        return [math.log((2 + totals[i][0] + totals[1 - i][1]) / 14) for i in (0, 1)]


def fit(observations):
    if not observations:
        raise ValueError("no eligible early training observations")
    rates, x, y = np.asarray(observations, dtype=float).T

    def derivative(beta):
        return float(x @ (rates * np.exp(beta * x) - y) + 10 * beta)

    if derivative(0) >= 0:
        return 0.0
    if derivative(1) <= 0:
        return 1.0
    return float(brentq(derivative, 0, 1))


def adjusted(base, x, beta):
    if x is None or beta == 0:
        return base
    h, a = np.ogrid[: base.shape[0], : base.shape[1]]
    adjusted_mass = base * np.exp(beta * (x[0] * h + x[1] * a))
    return adjusted_mass / adjusted_mass.sum()


def decisions(matrix, line):
    scores = top_three_scores(matrix)
    wdl = ("home", "draw", "away")[np.argmax(probabilities(matrix))]
    side = None
    if line is not None:
        # Same five-state unit-odds objective and tie ordering as v4.main_model.predict.
        def balance(pick):
            p = total_settlement(matrix, line, pick)
            return p["win"] + 0.5 * p["half_win"] - 0.5 * p["half_loss"] - p["loss"]

        side = max(("over", "under"), key=balance)
    return dict(scores=scores, wdl=wdl, total=side)


def hits(pick, actual, line, matrix):
    h, a = actual
    result = f"{h}-{a}"
    wdl = "home" if h > a else "away" if a > h else "draw"
    ou = settle_ou(h + a, line, pick["total"]) if line is not None else None
    probability = matrix[h, a] if h < matrix.shape[0] and a < matrix.shape[1] else 0.0
    return dict(
        first=pick["scores"][0] == result,
        three=result in pick["scores"],
        wdl=pick["wdl"] == wdl,
        full_win=None if ou in (None, "PUSH") else ou == "FULL_WIN",
        settlement=ou,
        log_loss=-math.log(max(float(probability), 1e-15)),
    )


def verify_pair(pair, author, anchor):
    f, p = pair["fixture"], pair["payload"]["current_model"]
    ref = author["payload"]["current_test_reference"]
    if (
        pair["revision"] != 1
        or pair["evaluation_slot"] != "FIRST_PREMATCH"
        or pair["stage"] != "prematch"
        or pair["track"] != "current-test-pair"
        or pair["previous_sha256"] is not None
        or ref["freeze_id"] != pair["freeze_id"]
        or ref["sha256"] != pair["record_sha256"]
        or ref["recorded_at"] != pair["recorded_at"]
        or not pair["match_id"] == author["match_id"] == f["match_id"]
        or p["input"] != f
        or any(f[k] != author["fixture"][k] for k in ("cohort", "home", "away", "stage", "score_basis"))
        or aware(f["kickoff_at"]) != aware(author["fixture"]["kickoff_at"])
        or f["stage"] != "regular"
        or f["score_basis"] != "regulation_90"
        or f.get("neutral") is not False
        or f.get("identity_verified") is not True
        or p["version"] != anchor["version"]
        or p["recipe"] != anchor["recipe"]
        or p.get("context_numeric_adjustment_applied") is not False
        or p["model_hash"] != anchor["hash"]
        or p["matrix_hash"] != digest(p["matrix"])
        or p["training_data_cutoff"] != anchor["train_end"]
        or anchor["train_end"] >= f["cutoff_at"][:10]
        or not max(aware(p["model_observed_at"]), aware(p["model_built_at"]))
        <= aware(f["cutoff_at"])
        <= aware(p["generated_at"])
        <= aware(pair["recorded_at"])
        < aware(f["kickoff_at"])
    ):
        raise ValueError("first Current pair, clock, matrix or artifact conflict")
    return p
