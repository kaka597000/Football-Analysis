"""Check the original source bytes of the main lines selected in this archive replay."""

import gzip
import io
import json
import subprocess
import tarfile
from datetime import datetime

from build_inputs import git_file, sha


def same_time(a, b):
    return datetime.fromisoformat(a.replace("Z", "+00:00")) == datetime.fromisoformat(
        b.replace("Z", "+00:00")
    )


def event_identity(q, event_id, kickoff, home, away):
    if (q["raw_event_id"], q["home"], q["away"]) != (
        str(event_id),
        home,
        away,
    ) or not same_time(q["kickoff_at"], kickoff):
        raise ValueError("Selected quote conflicts with original event identity")


def quote_prices(q, line, over, under):
    if tuple(float(q[k]) for k in ("line", "over_price", "under_price")) != tuple(
        map(float, (line, over, under))
    ):
        raise ValueError("Original two-sided line differs from selected line")


def validate(data, commit, rows):
    snapshots = [
        json.loads(s)
        for s in git_file(data, commit, "market-snapshots.jsonl").splitlines()
    ]
    archives = {}
    verified = []
    try:
        for row in rows:
            q = row["market"]
            if not q:
                continue
            if not q["quote_at"]:
                refs = [
                    x
                    for x in snapshots
                    if x["raw_reference"]["sha256"] == q["source_sha256"]
                    and x["bookmaker"] == q["bookmaker"]
                    and same_time(q["captured_at"], x["captured_at"])
                ]
                if len(refs) != 1 or refs[0].get("quote_time"):
                    raise ValueError(
                        "Capture-only market receipt is missing or ambiguous"
                    )
                path = refs[0]["raw_reference"]["path"]
                raw = gzip.decompress(git_file(data, commit, path))
                matches = [
                    e
                    for e in json.loads(raw)["events"]
                    if str(e["event_id"]) == q["raw_event_id"]
                ]
                if len(matches) != 1:
                    raise ValueError("Ambiguous market snapshot event")
                e = matches[0]
                event_identity(q, e["event_id"], e["start_ts"], e["home"], e["away"])
                line = e["markets"]["ou"]
                quote_prices(q, line["line"], line["over"], line["under"])
            else:
                bundle, path = q["source_ref"].split(":", 1)
                if bundle not in archives:
                    package, stem = bundle.split("/")
                    tree = (
                        subprocess.check_output(
                            [
                                "git",
                                "-C",
                                str(data),
                                "ls-tree",
                                "-r",
                                "--name-only",
                                commit,
                                "--",
                                "workspace-updates/" + package + "/parts",
                            ]
                        )
                        .decode()
                        .splitlines()
                    )
                    parts = sorted(
                        p for p in tree if p.split("/")[-1].startswith(stem + ".part")
                    )
                    archives[bundle] = tarfile.open(
                        fileobj=io.BytesIO(
                            b"".join(git_file(data, commit, p) for p in parts)
                        )
                    )
                archive = archives[bundle]
                raw = archive.extractfile(path).read()
                meta = json.load(archive.extractfile(path + ".meta.json"))
                if (
                    meta["sha256"] != q["source_sha256"]
                    or meta["http_status"] != 200
                    or not same_time(meta["queried_utc"], q["captured_at"])
                ):
                    raise ValueError("Original archive receipt differs")
                matches = [e for e in json.loads(raw) if e["id"] == q["raw_event_id"]]
                if len(matches) != 1:
                    raise ValueError("Ambiguous provider event")
                e = matches[0]
                event_identity(
                    q, e["id"], e["commence_time"], e["home_team"], e["away_team"]
                )
                markets = [
                    m
                    for b in e["bookmakers"]
                    if b["title"] == q["bookmaker"]
                    for m in b["markets"]
                    if m["key"] == "totals"
                ]
                if len(markets) != 1 or not same_time(
                    markets[0]["last_update"], q["quote_at"]
                ):
                    raise ValueError("Original market or quote timestamp differs")
                legs = {leg["name"]: leg for leg in markets[0]["outcomes"]}
                if legs["Over"]["point"] != legs["Under"]["point"]:
                    raise ValueError("Original market lines differ")
                quote_prices(
                    q,
                    legs["Over"]["point"],
                    legs["Over"]["price"],
                    legs["Under"]["price"],
                )
            if sha(raw) != q["source_sha256"]:
                raise ValueError("Original market bytes have changed")
            verified.append(
                dict(
                    espn_id=row["espn_id"],
                    tier=row["market_timing"],
                    path=path,
                    raw_sha256=sha(raw),
                )
            )
    finally:
        for archive in archives.values():
            archive.close()
    return verified
