"""Build both arms from the same earlier match dates. Read historical labels separately."""
import argparse
import csv
from collections import defaultdict
from datetime import date, datetime, timedelta
from hashlib import sha256
from io import StringIO
import json
import math
from pathlib import Path
import tarfile

from research.analysis_freeze import verify
from research_program.prior_team_strength import LeagueStrength, observations


HERE = Path(__file__).resolve().parent
PROTOCOL = json.loads((HERE / "protocol.json").read_bytes())
TRAIN = ("1920", "2021", "2122")
TEST = ("2223", "2324", "2425")
LEAGUES = PROTOCOL["scope"]


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return sha256(canonical(value).encode()).hexdigest()


def write_rows(path, rows):
    Path(path).write_text("".join(canonical(r) + "\n" for r in sorted(rows, key=lambda r: r["id"])))


def read_rows(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines()]


def as_day(raw):
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"invalid football-data match date {raw!r}")


def count(raw):
    value = (raw or "").strip()
    return int(value) if value.isascii() and value.isdecimal() else None


def score(row):
    h, a = count(row["FTHG"]), count(row["FTAG"])
    if h is None or a is None:
        return None
    if row["FTR"] != ("H" if h > a else "A" if a > h else "D"):
        raise ValueError("inconsistent full-time score and result")
    return (h, a)


def historical_line(row):
    for field in ("B365>2.5", "B365<2.5"):
        try:
            price = float(row.get(field, "") or "")
        except ValueError:
            return None
        if not math.isfinite(price) or price <= 1:
            return None
    return 2.5


class History:
    def __init__(self, rows, cutoff):
        self.cutoff = cutoff
        self.strength = LeagueStrength()
        self.teams = defaultdict(list)
        days = defaultdict(list)
        for row in rows:
            if row["date"] < cutoff - timedelta(days=1) and row["goals"] is not None:
                days[row["date"]].append(row)
        self.n = sum(len(v) for v in days.values())
        for day, games in sorted(days.items()):
            updates = []
            seen = set()
            for game in games:
                home, away = game["home"], game["away"]
                if home == away or seen.intersection((home, away)):
                    raise ValueError("duplicate or conflicting same-day team identity")
                seen.update((home, away))
                shots = game["sot"] or (None, None)
                self.teams[home].append((day, *shots))
                self.teams[away].append((day, shots[1], shots[0]))
                updates.append((home, away, observations(game["goals"])))
            self.strength.update_day(day, updates)
        self.coef = self.strength.coefficients(cutoff)

    def inputs(self, home, away):
        if self.n < 60 or not all(t in self.teams for t in (home, away)):
            return {"rates": None, "for_against": None}
        rates = self.strength.rates(self.coef, home, away)[:, 0].tolist()
        result = {"rates": rates, "for_against": None}
        h, a = self.teams[home][-6:], self.teams[away][-6:]
        if any(len(side) != 6 or (self.cutoff - side[0][0]).days > 90
               or any(x is None or y is None for _, x, y in side) for side in (h, a)):
            return result
        def series(side, index):
            return [g[index] for g in side]
        result["for_against"] = [[series(h, 1), series(a, 2)], [series(a, 1), series(h, 2)]]
        return result


def make_state(history, cutoff):
    return History(history, cutoff)


def blocks(season, mode):
    first = 2000 + int(season[:2])
    if mode == "train":
        return [date(first, 11, 1)] + [date(first + 1, m, 1) for m in (1, 3, 5)]
    return [date(first + 1, 5, 1)]


def unpack_season(stream, division, season):
    rows = []
    seen = set()
    try:
        decoded = stream.decode("utf-8-sig")
    except UnicodeDecodeError:
        decoded = stream.decode("cp1252")
    for source_row in csv.DictReader(StringIO(decoded)):
        if not source_row.get("Date") and not source_row.get("HomeTeam"):
            continue
        day = as_day(source_row["Date"])
        home, away = (source_row[k].strip() for k in ("HomeTeam", "AwayTeam"))
        years = (2000 + int(season[:2]), 2000 + int(season[2:]))
        if (day.year not in years or source_row["Div"].lstrip("\ufeff") != division
                or not home or not away or home == away or (home, away) in seen):
            raise ValueError(f"invalid or duplicate regular-season game {division} {day} {home} {away}")
        seen.add((home, away))
        rows.append((day, home, away, source_row))
    return rows


def from_archive(path, mode):
    if sha256(path.read_bytes()).hexdigest() != PROTOCOL["archive_sha256"]:
        raise ValueError("source archive hash mismatch")
    out = []
    with tarfile.open(path) as tar:
        for season in (TRAIN if mode == "train" else TEST):
            for division, league in LEAGUES.items():
                rows = unpack_season(tar.extractfile(f"raw/close/{season}_{division}.csv").read(), division, season)
                for origin in blocks(season, mode):
                    past = []
                    if mode != "test-labels":
                        for day, home, away, raw in rows:
                            if day < origin - timedelta(days=1):
                                past.append(dict(date=day, home=home, away=away, goals=score(raw),
                                                 sot=(count(raw.get("HST")), count(raw.get("AST")))))
                        state = make_state(past, origin)
                    for day, home, away, raw in rows:
                        if (day.year, day.month) != (origin.year, origin.month):
                            continue
                        mid = digest([division, day.isoformat(), home, away])
                        if mode == "test-labels":
                            out.append(dict(id=mid, score=score(raw), line=historical_line(raw)))
                            continue
                        item = dict(id=mid, league=league, date=day.isoformat(), home=home, away=away,
                                    origin=origin.isoformat(), line=2.5, **state.inputs(home, away))
                        if mode == "train":
                            item.update(score=score(raw), observed_line=historical_line(raw))
                        out.append(item)
    if len({r["id"] for r in out}) != len(out):
        raise ValueError("duplicate fixture identity across blocks")
    return out


def verify_r7(path, historical):
    if sha256(path.read_bytes()).hexdigest() != PROTOCOL["r7_sha256"]:
        raise ValueError("R7 archive hash mismatch")
    with tarfile.open(path) as tar:
        issues = []
        for member in tar:
            if member.isfile() and member.name.endswith(".json"):
                row = json.load(tar.extractfile(member))
                if row.get("track") == "research-prematch-continuation-20261123":
                    issues.append(verify(row))
    if (len(issues) != 192 or len({x["match_id"] for x in issues}) != 192
            or max(x["date"] for x in historical) >= min(x["fixture"]["kickoff_at"][:10] for x in issues)):
        raise ValueError("historical roster and the protected R7 queue are not disjoint by date")
    return len(issues)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["train", "test-inputs", "test-labels"])
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--r7", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--forecasts", type=Path)
    parser.add_argument("--lock", type=Path)
    args = parser.parse_args()
    if args.mode == "test-labels":
        if not args.forecasts or not args.lock:
            parser.error("supply the previously sealed forecast file and lock before grading")
        from study import read_lock
        read_lock(args.lock, args.forecasts, "historical")
    rows = from_archive(args.archive, args.mode)
    if args.mode != "test-labels":
        verify_r7(args.r7, rows)
    write_rows(args.out, rows)
    print(f"{args.mode}: {len(rows)} rows")
