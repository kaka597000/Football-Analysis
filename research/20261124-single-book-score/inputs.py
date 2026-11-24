"""Read only the specified editions. Historical source prices have no clock."""

import csv
import hashlib
import io
import json
import tarfile
from collections import Counter
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROTOCOL = json.loads((HERE / "protocol.json").read_text())
PROTOCOL_SHA = "45bc47858729b473ab33a4be481a1428be1fa5f9ed72cc32029eec22f1af9705"


def sha(path):
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def dump(path, value):
    with path.open("x", encoding="utf-8") as f:
        json.dump(value, f, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)
        f.write("\n")


def read_csvs(archive, seasons, want_labels):
    """Request labels only when fitting the past or grading already-locked forecasts."""
    with tarfile.open(archive, "r:gz") as t:
        for season in seasons:
            for code, comp in PROTOCOL["regular_season_csv"].items():
                name = f"raw/close/{season}_{code}.csv"
                rows = csv.DictReader(io.StringIO(t.extractfile(name).read().decode("utf-8-sig")))
                for source_line, row in enumerate(rows, 2):
                    if not row.get("Date"):
                        continue
                    date = datetime.strptime(row["Date"], "%d/%m/%Y").date().isoformat()
                    if row["Div"] != code or not all(row.get(k) for k in ("HomeTeam", "AwayTeam")):
                        raise ValueError("Unexpected historical identity")
                    key = "|".join([comp, date, row["HomeTeam"], row["AwayTeam"]])
                    item = dict(match_id=key, competition=comp, date=date, book="Bet365",
                                season=season, source_member=name, source_line=source_line)
                    if want_labels:
                        goals = [row["FTHG"], row["FTAG"]]
                        item["label"] = tuple(map(int, goals)) if all(v.isdecimal() for v in goals) else None
                    try:
                        h, d, a, over, under = (float(row[k]) for k in
                            ("B365H", "B365D", "B365A", "B365>2.5", "B365<2.5"))
                        if not all(1 < v < float("inf") for v in (h, d, a, over, under)):
                            raise ValueError("Invalid price")
                        item.update(h2h=dict(home=h, draw=d, away=a), total=dict(line=2.5, over=over, under=under))
                    except (ValueError, TypeError):
                        item["gap"] = "MISSING_COMPLETE_ONE_BOOK_PRICES"
                    yield item


def identities(rows):
    seen = set()
    for row in rows:
        key = row["match_id"]
        if key in seen:
            raise ValueError("Duplicate source fixture: " + key)
        seen.add(key)
        yield row


def wdl(score):
    h, a = score
    return "home" if h > a else "away" if h < a else "draw"


def tier_counts(rows):
    return dict(sorted(Counter(r["competition"] for r in rows).items()))
