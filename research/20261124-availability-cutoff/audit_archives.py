"""Reproduce personnel timing coverage from existing files only; no results, training or publication."""

import argparse
import csv
import gzip
import io
import json
import subprocess
import tarfile
from collections import Counter, defaultdict
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from competition_scope import permitted, policy
from research.analysis_freeze import digest, verify

from people_evidence import CLOCKS, admission, espn, jleague, sha, times, utc

REV = "a6d45024cf6cf622f05e4b378ab53cfe2d6ae329"


def save(path, value):
    path.write_text(
        json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )


def read(path):
    return json.loads(path.read_bytes())


def table(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=list(dict.fromkeys(k for v in rows for k in v)),
            lineterminator="\n",
        )
        w.writeheader()
        w.writerows(rows)


@contextmanager
def git_files(repo):
    """Read each immutable local Git object on demand without materializing a checkout."""
    with subprocess.Popen(
        ["git", "-C", str(repo), "cat-file", "--batch"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    ) as proc:

        def get(path):
            if "\n" in path:
                raise ValueError("Invalid archive path")
            proc.stdin.write((REV + ":" + path + "\n").encode())
            proc.stdin.flush()
            header = proc.stdout.readline().split()
            if len(header) != 3 or header[1] != b"blob":
                raise ValueError("Missing committed blob " + path)
            content = proc.stdout.read(int(header[2]))
            if proc.stdout.read(1) != b"\n":
                raise ValueError("Incomplete Git object")
            return content

        try:
            yield get
        finally:
            proc.stdin.close()


def allowed(f, config):
    if not permitted(f):
        return False
    code = f["competition"]
    stage = f.get("stage")
    cohort = f.get("cohort")
    if not stage or stage.upper() == "UNVERIFIED":
        return False
    if code == "jpn.league_cup":
        return (
            stage == "quarterfinals"
            and f.get("leg") in (1, 2)
            and "quarterfinals" in (cohort or "")
        )
    if code == "uefa.nations":
        return stage == "league-phase" and cohort in {
            f"uefa.nations:{lev}:league-phase" for lev in "ABCD"
        }
    if code == "bra.1":
        return stage == "regular" and cohort == code
    if (cohort, stage) in config:
        return True
    # The issued J1 regular-season label is explicit; its edition needs independent target verification before using any player signal.
    return code == "jpn.1" and cohort == code and stage == "regular-season"


def field_paths(obj):
    """Presence in an issued record is evidence of documentation, never proof of a learned coefficient."""
    result = set()

    def visit(x, parts=()):
        if isinstance(x, dict):
            for k, v in x.items():
                if k.lower() in {
                    "lineup",
                    "lineups",
                    "availability",
                    "injury_status",
                    "injuries",
                    "suspensions",
                    "official_starting_lineups",
                    "lineup_status",
                    "official_lineups",
                }:
                    result.add(".".join((*parts, k)))
                if isinstance(v, (dict, list)) and k not in (
                    "matrix",
                    "outcomes",
                    "result",
                    "results",
                    "comparisons",
                ):
                    visit(v, (*parts, k))
        elif isinstance(x, list):
            for v in x:
                if isinstance(v, dict):
                    visit(v, parts)

    visit(obj)
    return sorted(result)


def ledger(root, config):
    rows = []
    targets = []
    seen = set()
    comp = {r["code"] for r in policy()["competitions"]}
    for file in sorted(root.glob("*/000001.json")):
        r = read(file)
        if r["track"] != "comprehensive" or r["stage"] != "prematch":
            continue
        verify(r)
        f = r["fixture"]
        mid = r["match_id"]
        code = f.get("competition", "")
        if (
            r["revision"] != 1
            or r["previous_sha256"] is not None
            or r["evaluation_slot"] != "FIRST_PREMATCH"
            or f["match_id"] != mid
            or file.parent.name
            != digest({"match_id": mid, "stage": r["stage"], "track": r["track"]})
        ):
            raise ValueError("Invalid first comprehensive version or path")
        if mid in seen:
            raise ValueError("Duplicate fixture in the first-issue ledger")
        seen.add(mid)
        start = utc(f["kickoff_at"])
        cut = utc(f["cutoff_at"])
        issued = utc(r["recorded_at"])
        if code not in comp:
            reason = "OTHER_OR_UNRESOLVED_COMPETITION"
        elif not allowed(f, config):
            reason = "PHASE_OR_EDITION_NOT_BOUND_FOR_AUDIT"
        elif cut > issued or issued >= start or cut >= start:
            reason = "TIMING_NOT_PREMATCH"
        elif not (
            f.get("home")
            and f.get("away")
            and f["home"] != f["away"]
            and all(":" in f[s] for s in ("home", "away"))
        ):
            reason = "UNRESOLVED_ORDERED_CLUB_IDS"
        else:
            reason = "INCLUDED"
        flags = field_paths({"fixture": f, "payload": r["payload"]})
        row = {
            "match_id": mid,
            "competition": code,
            "cohort": f.get("cohort"),
            "stage": f.get("stage"),
            "leg": f.get("leg"),
            "home": f.get("home"),
            "away": f.get("away"),
            "kickoff_at": start.isoformat(),
            "cutoff_at": cut.isoformat(),
            "first_recorded_at": issued.isoformat(),
            "record_sha256": r["record_sha256"],
            "freeze_id": r["freeze_id"],
            "admission": reason,
            "personnel_fields_present": bool(flags),
            "field_paths": "|".join(flags),
        }
        rows.append(row)
        if reason == "INCLUDED":
            targets.append(row)
    return rows, targets


def collector(get):
    index = get("requests.jsonl")
    snap = defaultdict(list)
    out = []
    fail = []
    for line in index.splitlines():
        r = json.loads(line)
        url = urlsplit(r["source_url"])
        if (
            r["status"] != "OK"
            or url.hostname != "site.api.espn.com"
            or not url.path.endswith("/summary")
        ):
            continue
        ref = r["raw"]
        path = "raw/" + ref["sha256"] + ".json.gz"
        if ref["path"] != path:
            raise ValueError("Unexpected source object path")
        raw = gzip.decompress(get(path))
        if sha(raw) != ref["sha256"]:
            raise ValueError("Retained raw digest differs")
        try:
            o = espn(raw, r)
        except (KeyError, ValueError, TypeError) as e:
            fail.append({"source_sha256": sha(raw), "reason": str(e)})
            continue
        rank = (
            "PREGAME"
            if o["pregame"] and utc(o["received_at"]) < utc(o["kickoff_at"])
            else "LIVE_OR_POSTMATCH"
        )
        out.append({**o, "timing": rank})
        snap[o["match_id"]].append(o)
    return out, snap, fail, sha(index)


def bound_jcup_receipt(get, root):
    base = "workspace-updates/20261111-jleague-cup-lineups/"
    meta = json.loads(get(base + "manifest.json"))
    payload = get(base + "lineup-analysis-evidence.tar.gz")
    if sha(payload) != meta["archive_sha256"]:
        raise ValueError("Official source archive hash differs")
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as t:
        expected = t.extractfile("jcup-lineup-20261111/staging/sources.json").read()
    if expected != (root / "staging/sources.json").read_bytes():
        raise ValueError(
            "Official source capture timestamps differ from archived receipt"
        )
    return sha(expected)


def jcup_cases(root, targets, freezes):
    sheet = read(root / "staging/sources.json")["sources"]
    t = {r["match_id"]: r for r in targets}
    results = []
    obs = []
    for receipt in sheet:
        mid = receipt["match_id"]
        f = t[mid]
        file = root / "staging/raw" / Path(receipt["raw_path"]).name
        obs_item = jleague(file.read_bytes(), receipt, f)
        statuses = {
            label: admission(f, obs_item, cut) for label, cut in times(f).items()
        }
        directory = freezes / digest(
            {"match_id": mid, "stage": "prematch", "track": "comprehensive"}
        )
        first_bound = None
        previous = None
        clock = None
        for i, p in enumerate(sorted(directory.glob("[0-9]*.json")), 1):
            v = verify(read(p))
            at = utc(v["recorded_at"])
            if (
                v["match_id"] != mid
                or v["track"] != "comprehensive"
                or v["stage"] != "prematch"
                or v["revision"] != i
                or v["previous_sha256"] != previous
                or (clock and at < clock)
            ):
                raise ValueError(
                    "Different match, sequence or time in lineup amendment"
                )
            previous = v["record_sha256"]
            clock = at
            lineup = v["payload"].get("lineup") or {}
            if (
                lineup.get("source_sha256") == obs_item["source_sha256"]
                and first_bound is None
            ):
                if (
                    lineup.get("official_starting_lineups_published") is not True
                    or str(lineup["official_match_id"]) != mid.removeprefix("jleague:")
                    or lineup["team_codes"]
                    != [f[s].removeprefix("jleague:") for s in ("home", "away")]
                    or not all(
                        len(lineup["starting_xi"][s]) == 11 for s in ("home", "away")
                    )
                    or at < utc(obs_item["received_at"])
                    or v["payload"].get("matrix_changed") is not False
                ):
                    raise ValueError(
                        "Amendment does not bind the original published lineups"
                    )
                first_bound = at
        bound = {
            label: bool(first_bound and first_bound <= cut)
            for label, cut in times(f).items()
        }
        obs.append(obs_item)
        results.append(
            {
                "match_id": mid,
                "competition": f["competition"],
                "source_sha256": obs_item["source_sha256"],
                "source_kind": obs_item["kind"],
                "captured_at": obs_item["received_at"],
                "source_published_at": obs_item["published_at"],
                "kickoff_at": f["kickoff_at"],
                "cutoff_at": f["cutoff_at"],
                "freeze_id": f["freeze_id"],
                "stable_starters": 22,
                "first_version_with_XI": first_bound.isoformat()
                if first_bound
                else None,
                **{"bound_" + k: v for k, v in bound.items()},
                "goalkeeper_ids_present": len(obs_item["keeper_ids"]) == 2,
                **statuses,
            }
        )
    return results, obs


def existing_brazil(get):
    b = "workspace-updates/20261118-sequence/coverage/"
    index = json.loads(get(b + "latest.json"))
    raw = get(b + index["latest"])
    body = json.loads(raw)
    # research_supplement.persist binds the exact serialized run.
    if sha(raw) != index["snapshot_sha256"]:
        raise ValueError("Existing coverage report hash mismatch")
    if index["registered"] != len(body["fixtures"]):
        raise ValueError("Existing lineup denominator differs")
    return dict(
        n=len(body["fixtures"]),
        first_issue=sum(
            v["quality"]["original_first_issue"]["confirmed_lineups"]
            for v in body["fixtures"]
        ),
        window_states=dict(
            Counter(v["lineup_window_evidence"]["state"] for v in body["fixtures"])
        ),
        sha256=sha(raw),
    )


def run(a):
    a.out.mkdir(parents=True, exist_ok=True)
    with git_files(a.data) as get:
        config = {
            (r["cohort"], stage)
            for r in json.loads(get("config/scopes.json"))
            for stage in r["stages"]
        }
        reg, targets = ledger(a.freezes, config)
        provider, byid, rejected, idx = collector(get)
        brazil = existing_brazil(get)
        source_index_hash = bound_jcup_receipt(get, a.jcup)
    cases, official = jcup_cases(a.jcup, targets, a.freezes)
    for x in official:
        byid[x["match_id"]].append(x)
    rows = []
    for f in targets:
        for label, cut in times(f).items():
            state = Counter(admission(f, x, cut) for x in byid[f["match_id"]])
            rows.append(
                {
                    "match_id": f["match_id"],
                    "competition": f["competition"],
                    "day": f["kickoff_at"][:10],
                    "clock": label,
                    "cutoff": cut.isoformat(),
                    "official_jcup_both": bool(state["OFFICIAL_BOTH_XI"]),
                    "espn_provider_both": bool(state["PROVIDER_BOTH_XI"]),
                    "source_outcomes": json.dumps(state, sort_keys=True),
                }
            )

    def scope_counts(code):
        included = [f for f in targets if f["competition"] == code]
        batch = [r for r in rows if r["competition"] == code]
        return dict(
            competition=code,
            retained_named_first_freezes=sum(f["competition"] == code for f in reg),
            eligible_targets=len(included),
            first_date=min((f["kickoff_at"][:10] for f in included), default=None),
            last_date=max((f["kickoff_at"][:10] for f in included), default=None),
            has_personnel_fields=sum(f["personnel_fields_present"] for f in included),
            clocks={
                c: dict(
                    denominator=len(included),
                    jleague_official_both=sum(
                        r["official_jcup_both"] for r in batch if r["clock"] == c
                    ),
                    espn_provider_both=sum(
                        r["espn_provider_both"] for r in batch if r["clock"] == c
                    ),
                )
                for c in CLOCKS
            },
        )

    scope = [scope_counts(v["code"]) for v in policy()["competitions"]]
    monthly = Counter((f["competition"], f["kickoff_at"][:7]) for f in targets)
    summary = dict(
        original_ledger=dict(
            all=len(reg),
            admission=dict(Counter(r["admission"] for r in reg)),
            included=len(targets),
            structured_personnel_field_present=sum(
                r["personnel_fields_present"] for r in targets
            ),
            eligible_first_freeze_hashes=digest(
                sorted(r["record_sha256"] for r in targets)
            ),
            all_first_freeze_hashes=digest(sorted(r["record_sha256"] for r in reg)),
        ),
        collector=dict(
            receipts=len(provider) + len(rejected),
            accepted_raw=len(provider),
            identity_failures=len(rejected),
            unique_source_events=len({r["match_id"] for r in provider}),
            states=dict(Counter(r["timing"] + "/" + r["kind"] for r in provider)),
            request_ledger_sha256=idx,
        ),
        jcup_sources=len(official),
        jcup_first_comprehensive_version_with_XI={
            k: sum(r["bound_" + k] for r in cases) for k in CLOCKS
        },
        jcup_acquired={
            k: sum(r[k] == "OFFICIAL_BOTH_XI" for r in cases) for k in CLOCKS
        },
        jcup_acquisition_manifest_sha256=source_index_hash,
        brazil_existing=brazil,
        scope=scope,
        monthly=[
            {"competition": c, "month": m, "eligible_firsts": v}
            for (c, m), v in sorted(monthly.items())
        ],
    )
    table(
        a.out / "competition-coverage.csv",
        [
            dict(
                competition=r["competition"],
                first_date=r["first_date"],
                last_date=r["last_date"],
                first_freezes=r["retained_named_first_freezes"],
                eligible=r["eligible_targets"],
                personnel_field=r["has_personnel_fields"],
                **{
                    c + "_jleague_official": r["clocks"][c]["jleague_official_both"]
                    for c in CLOCKS
                },
                **{
                    c + "_espn_supplier": r["clocks"][c]["espn_provider_both"]
                    for c in CLOCKS
                },
            )
            for r in scope
        ],
    )
    table(a.out / "fixture-ledger.csv", reg)
    table(a.out / "same-cutoff.csv", rows)
    table(a.out / "jcup-replay.csv", cases)
    table(a.out / "espn-archive.csv", provider)
    save(a.out / "espn-rejections.json", rejected)
    save(a.out / "coverage.json", summary)
    print(
        json.dumps(
            {k: v for k, v in summary.items() if k not in ("scope", "monthly")},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("data", "freezes", "jcup", "out"):
        p.add_argument("--" + name, type=Path, required=True)
    run(p.parse_args())
