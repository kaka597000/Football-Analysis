"""Read the original Current pairs and the pre-cutoff corners. Run with the pinned #69 reader."""

import argparse
from collections import defaultdict
from datetime import timedelta, timezone
from functools import lru_cache
from hashlib import sha256
import io
import json
from pathlib import Path
import subprocess
import tarfile
from tempfile import TemporaryDirectory

import archive as audit
import replay as reader
from research.analysis_freeze import aware, digest, verify
from corners import PriorGames

P = json.loads(Path(__file__).with_name("protocol.json").read_bytes())
SPEC = P["archive_check"]
BJT = timezone(timedelta(hours=8))
AUDIT_PIN = "54ec6d70fdff7145c1889ffe6071b2188663bef2"
REGISTRY_SHA = "923e34fc44cda33fe965498a24a2435abb37a01a80c539108d8ec20d490bf013"


def check_auditor():
    root = Path(audit.__file__).resolve().parents[2]
    if (
        Path(reader.__file__).resolve().parent != root / "research/20261124-prior-dismissals"
        or subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
        != AUDIT_PIN
        or audit.PIN != P["data_pin"]
        or audit.EARLY != SPEC["season_source"]
    ):
        raise ValueError("unexpected archive auditor")
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "diff",
            "--quiet",
            "HEAD",
            "--",
            "runtime",
            "research/20261124-prior-dismissals",
        ],
        check=True,
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
        or audit.identity(f) != audit.identity(author["fixture"])
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


def first_roster(archives):
    manifest = json.load(archives.assets.extractfile("ASSET-MANIFEST.json"))
    roster = []
    for mid, name in manifest["wcpp_frozen_records"].items():
        record = verify(
            json.loads(audit.checked(archives.assets.extractfile(name).read(), manifest["files"][name]))
        )
        f = record["fixture"]
        date = aware(f["kickoff_at"]).astimezone(BJT).date().isoformat()
        if date not in ("2026-11-20", "2026-11-21"):
            continue
        if (
            mid != record["match_id"]
            or mid != f["match_id"]
            or name != f"issued/{record['freeze_id']}.json"
            or record["track"] != "comprehensive"
            or record["evaluation_slot"] != "FIRST_PREMATCH"
            or record["revision"] != 1
            or record["stage"] != "prematch"
            or not aware(f["cutoff_at"]) <= aware(record["recorded_at"]) < aware(f["kickoff_at"])
        ):
            raise ValueError("roster identity or clock conflict")
        roster.append(record)
    if len(roster) != 33 or len({audit.identity(r["fixture"]) for r in roster}) != len(roster):
        raise ValueError("unexpected bounded roster")
    return roster


@lru_cache(maxsize=None)
def original_anchor(root, league):
    reg = json.loads(audit.checked((root / "runtime/active-model.json").read_bytes(), REGISTRY_SHA))
    ref = reg["models"][league]
    path = root / Path(ref["artifact_path"]).relative_to("/workspace/WCPP")
    anchor = json.loads(audit.checked(path.read_bytes(), ref["artifact_sha256"]))["anchor"]
    if (
        anchor["version"] != "WCPP-MAIN-1.2"
        or anchor["execution_recipe"] != "baseline"
        or anchor["cohort"] != league
    ):
        raise ValueError("unexpected Current recipe")
    return dict(
        hash=digest(anchor),
        version=anchor["version"],
        recipe=anchor["execution_recipe"],
        train_end=anchor["train_end"],
    )


def prepare(archives, repo, wcpp, tmp):
    rows = []
    roster = first_roster(archives)
    blob = audit.checked(
        audit.blob(repo, SPEC["current_pairs_commit"], SPEC["current_pairs_archive"]),
        SPEC["current_pairs_sha256"],
    )
    manifest_path = SPEC["current_pairs_archive"].rsplit("/", 1)[0] + "/manifest.json"
    manifest = json.loads(audit.blob(repo, SPEC["current_pairs_commit"], manifest_path))
    if manifest["archive_sha256"] != SPEC["current_pairs_sha256"]:
        raise ValueError("pair archive manifest conflict")
    with tarfile.open(fileobj=io.BytesIO(blob)) as pairs:
        originals = {}
        for name in pairs.getnames():
            if name.startswith("latest/pairs/") and name.endswith(".json"):
                pair = verify(
                    json.loads(audit.checked(pairs.extractfile(name).read(), manifest["files"][name]))
                )
                if name != "latest/pairs/" + pair["match_id"].removeprefix("espn:") + ".json":
                    raise ValueError("archived pair file identity conflict")
                originals[pair["match_id"]] = pair
        first_clock = min(aware(v["fixture"]["cutoff_at"]) for v in originals.values())
        aliases = reader.prior_aliases(archives, first_clock, tmp)
        for record in sorted(roster, key=lambda r: r["match_id"]):
            f = record["fixture"]
            mid, league = record["match_id"], f["cohort"]
            row = dict(
                id=mid,
                league=league,
                day=aware(f["kickoff_at"]).astimezone(BJT).date().isoformat(),
                fixture={k: f[k] for k in ("match_id", "home", "away", "cohort", "stage", "kickoff_at")},
            )
            rows.append(row)
            if mid in archives.r7_ids or audit.identity(f) in archives.r7_keys:
                row["status"] = "protected_overlap"
                continue
            if league not in P["league_codes"].values() or f["stage"] != "regular":
                row["status"] = "outside_study_leagues_or_stage"
                continue
            pair = originals.get(mid)
            if not pair or pair["revision"] != 1:
                row["status"] = "revision_only" if pair else "first_pair_unavailable"
                continue
            original = verify_pair(pair, record, original_anchor(wcpp, league))
            target = pair["fixture"]
            cutoff = aware(target["cutoff_at"])
            season, available = archives.season(league, 2026)
            row.update(
                status="fallback_missing_signal",
                signal=None,
                matrix=original["matrix"],
                cutoff=target["cutoff_at"],
                first_sha256=pair["record_sha256"],
                matrix_sha256=original["matrix_hash"],
                original_three=[s["score"] for s in original["top3"]],
                original_first=original["primary"],
                original_wdl={"主胜": "home", "平局": "draw", "客胜": "away"}[original["wdl_reference"]],
                original_total=(original.get("total") or {}).get("reference_direction"),
                line=archives.quote_line(target),
            )
            if available is None or available >= cutoff:
                continue
            past = PriorGames(season, cutoff.astimezone(BJT).date())
            names = [
                audit.resolve_club({target[side + "_name"], *aliases[league, target[side]]}, past.by_team)
                for side in ("home", "away")
            ]
            if None not in names and names[0] != names[1]:
                row["signal"] = past.signal(*names)
            row["source_available_at"] = available.isoformat()
            if row["signal"] is not None:
                row["status"] = "active"
    return rows


def labels(repo, rows, tmp):
    eligible = {r["id"]: r for r in rows if "matrix_sha256" in r}
    choices = defaultdict(list)
    for raw in audit.blob(repo, P["data_pin"], "normalized-results.jsonl").splitlines():
        r = json.loads(raw)
        if r["match_id"] in eligible and audit.identity(r) == audit.identity(
            eligible[r["match_id"]]["fixture"]
        ):
            choices[r["match_id"]].append(r)
    output = {}
    for mid in eligible:
        if len(choices[mid]) != 1:
            continue
        (r,) = choices[mid]
        comp = reader.verified_summary(repo, P["data_pin"], r, tmp)
        # Independently check both native half totals when supplied.
        for side in comp["competitors"]:
            halves = side.get("linescores")
            if halves is not None and (
                len(halves) != 2
                or sum(int(h["displayValue"]) for h in halves) != r[side["homeAway"] + "_goals"]
            ):
                raise ValueError("half totals disagree with regulation score")
        output[mid] = dict(
            score=[r["home_goals"], r["away_goals"]],
            raw_sha256=r["raw_reference"]["sha256"],
            captured_at=r["raw_reference"]["captured_at"],
        )
    return output


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode", choices=("prepare", "labels"))
    p.add_argument("--data", type=Path, required=True)
    p.add_argument("--wcpp", type=Path, default=Path("/workspace/WCPP"))
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--prepared", type=Path)
    p.add_argument("--expected-sha")
    args = p.parse_args()
    check_auditor()
    with TemporaryDirectory() as d:
        if args.mode == "prepare":
            result = prepare(audit.Archives(args.data), args.data, args.wcpp, Path(d))
        else:
            prepared = json.loads(audit.checked(args.prepared.read_bytes(), args.expected_sha))
            result = dict(
                prepared_sha256=args.expected_sha,
                data_commit=P["data_pin"],
                results=labels(args.data, prepared, Path(d)),
            )
    data = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    reader.put(args.out, data)
    print(args.mode, len(result if args.mode == "prepare" else result["results"]), sha256(data).hexdigest())
