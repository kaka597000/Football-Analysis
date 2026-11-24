"""Replay the October 11 club-page packet with its original SHA-bound fixture inputs."""

import argparse
import html
import json
import re
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit

from research.lineup_evidence import assess

from audit_archives import save, table
from people_evidence import CLOCKS, SIDES, admission, sha, times, utc

MANIFEST = "e61a01208f2ea57f545f76a8d61c6bfc209cb2520213d602b3cb92431314a2f2"
# The original signed-off packet contains four manually mapped club publications.
PAGES = {
    "012": ("012-lineup", "cpfc.co.uk"),
    "013": ("013-lineups", "sslazio.it"),
    "014": ("014-official-xi", "uslecce.it"),
    "015": ("015-live", "ogcnice.com"),
}


def normalized(text):
    text = html.unescape(text).casefold().replace("’", "'")
    return " ".join(
        "".join(
            c
            for c in unicodedata.normalize("NFKD", text)
            if not unicodedata.combining(c)
        ).split()
    )


def view(root):
    manifest = (root / "manifest.json").read_bytes()
    if sha(manifest) != MANIFEST:
        raise ValueError("Different original October packet")
    indexed = {r["path"]: r["sha256"] for r in json.loads(manifest)["files"]}

    def get(path):
        raw = (root / path).read_bytes()
        if sha(raw) != indexed[path]:
            raise ValueError("Club packet member digest differs")
        return raw

    mapped = json.loads(get("official-starting-xi.json"))
    rows = []
    for key in ("011", "012", "013", "014", "015"):
        f = json.loads(get(f"fixture-{key}.json"))
        if (
            f["identity_verified"] is not True
            or f["phase"] != "PreMatch"
            or f["stage"] != "regular"
            or f["competition"] not in {"eng.1", "ita.1", "fra.1"}
            or f["home"] == f["away"]
        ):
            raise ValueError("Different match identity or phase")
        row = dict(
            match_id=f["match_id"],
            competition=f["competition"],
            kickoff_at=f["kickoff_at"],
            cutoff_at=f["cutoff_at"],
        )
        sides = [f["decision_evidence"][s]["lineup"] for s in SIDES]
        if key not in PAGES:
            if any(s.get("official_confirmed") is True for s in sides):
                raise ValueError("Unreviewed first-party page")
            rows.append(row | dict.fromkeys(CLOCKS, "PROVIDER_ONLY"))
            continue
        stem, domain = PAGES[key]
        meta = json.loads(get(f"official/{stem}.json"))
        raw = get(f"official/{stem}.html")
        if (
            meta["http_status"] != 200
            or sha(raw) != meta["sha256"]
            or any(
                urlsplit(meta[k]).scheme != "https"
                or urlsplit(meta[k]).hostname.removeprefix("www.") != domain
                for k in ("source_url", "actual_url")
            )
        ):
            raise ValueError("Source or official club origin differs")
        body = raw.decode()
        visible = normalized(
            re.sub(
                "<[^>]+>",
                " ",
                re.sub(
                    r"<(script|style)\b[^>]*>.*?</\1>", " ", body, flags=re.I | re.S
                ),
            )
        )
        for side, s in zip(SIDES, sides):
            names = mapped[key][side]
            refs = [
                r
                for r in s["evidence"]
                if r["source_tier"] == "L1"
                and r["source_url"] == meta["source_url"]
                and utc(r["query_time"]) == utc(meta["query_time"])
            ]
            if (
                not assess(s, f)["official_confirmed"]
                or len(refs) != 1
                or names != s["starters"]
                or len(names) != 11
                or len(set(names)) != 11
                or not all(normalized(n) in visible for n in names)
            ):
                raise ValueError(
                    "Official article, original side list or acquisition differs"
                )
        published = re.search(
            r'<meta[^>]*property=["\']article:published_time["\'][^>]*content=["\']([^"\']+)',
            body,
            re.I,
        )
        ob = f | dict(
            kind="OFFICIAL_BOTH_XI",
            pregame=True,
            received_at=meta["query_time"],
            published_at=published[1] if published else None,
        )
        rows.append(
            row
            | dict(
                received_at=ob["received_at"],
                published_at=ob["published_at"],
                source_sha256=sha(raw),
            )
            | {k: admission(f, ob, c) for k, c in times(f).items()}
        )
    return rows


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--packet", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    a = p.parse_args()
    rs = view(a.packet)
    a.out.mkdir(parents=True, exist_ok=True)
    table(a.out / "oct11-club-cases.csv", rs)
    counts = {k: sum(r[k] == "OFFICIAL_BOTH_XI" for r in rs) for k in CLOCKS}
    result = dict(
        fixtures=len(rs),
        official_at=counts,
        club_page_already_bound_to_original_fixture=counts["original"],
        by_competition={
            co: dict(
                total=sum(r["competition"] == co for r in rs),
                official_at={
                    k: sum(
                        r["competition"] == co and r[k] == "OFFICIAL_BOTH_XI"
                        for r in rs
                    )
                    for k in CLOCKS
                },
            )
            for co in sorted({r["competition"] for r in rs})
        },
        packet_manifest_sha256=MANIFEST,
    )
    save(a.out / "oct11-coverage.json", result)
    print(result)
