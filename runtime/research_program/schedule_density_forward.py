"""Issue complete, unstarted competition/date slates from the registered R7 forecasts."""

import argparse
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from itertools import groupby
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from publication import atomic_json
from research.analysis_freeze import DEFAULT_ROOT, aware, digest, save, verify
from storage_lock import exclusive_lock
from . import all_scope_model as base
from . import continuation_forward as r7
from . import goal_state_forward as ledger
from . import schedule_density as model
from . import schedule_density_study as study
from .goal_state_study import sha
from .r8_score_study import read
from .r9_score_study import verified_archive
from .territory_forward import FirstIssues


TRACK = "research-r17-schedule-density-20261125"
BEIJING = ZoneInfo("Asia/Shanghai")


def inputs(r5, seventh, folder, tmp):
    metadata = read(folder/"inputs.json")
    if (metadata["version"] != model.VERSION or metadata["protocol_sha256"] != study.PROTOCOL_SHA
            or sha(study.PROTOCOL) != study.PROTOCOL_SHA):
        raise ValueError("schedule study protocol differs")
    batches = []
    for name, source, archive, prefix in (("r5",r5,"forward-first-issues.tar.gz","issued"),
                                          ("r7",seventh,"first-issues.tar.gz","")):
        if metadata["manifests"][name] != sha(source/"manifest.json"):
            raise ValueError("forward source differs from training input: "+name)
        verified_archive(source, read(source/"manifest.json"), archive, tmp/name)
        batches.append(FirstIssues.load(tmp/name/prefix))
    fifth, old = batches
    if (fifth.registration["payload"]["identity"]["model"] != base.VERSION
            or old.registration["payload"]["identity"]["version"] != r7.model.VERSION):
        raise ValueError("original R5 and R7 first issues required")
    manifest = read(seventh/"manifest.json")
    recipes = {}
    for code in manifest["scope"]:
        verified_archive(seventh, manifest, "models/"+code+".tar.gz", tmp/"models"/code)
        native = tmp/"models"/code/"main/forward.json"
        path = folder/"main"/code/"forward.json"
        recipe, baseline = read(path), read(native)
        if (recipe["version"] != model.VERSION or recipe["competition"] != code or baseline["competition"] != code
                or recipe["r7_forward_sha256"] != sha(native) or recipe["fit_sha256"] != sha(path.with_name("fit.json"))
                or recipe["through"] != baseline["through"]):
            raise ValueError("calendar forward training chain differs: "+code)
        recipes[code] = (recipe, baseline, sha(path))
    return fifth, old, recipes, metadata


def payload(fifth, old, recipes, metadata, cutoff):
    groups = defaultdict(list)
    games = defaultdict(list)
    fixtures = old.registration["payload"]["fixtures"]
    for mid, registered in fixtures.items():
        kickoff = aware(registered["kickoff_at"])
        before = min(cutoff, kickoff - timedelta(microseconds=1))
        original, previous = fifth.match(registered, before), old.match(registered, before)
        if (mid != registered["match_id"] or previous["payload"]["input_sha256"] != original["record_sha256"]
                or previous["payload"]["line"] != original["payload"]["line"]):
            raise ValueError("R7 original and R5 source disagree: "+mid)
        game = r7.frozen_game(original["fixture"], original["payload"])
        code = game["competition"]
        if base.actual_line(game) != previous["payload"]["line"] or game["date"] <= recipes[code][0]["through"]:
            raise ValueError("schedule row overlaps the training period or source line differs: "+mid)
        groups[code,kickoff.astimezone(BEIJING).date().isoformat()].append(registered)
        games[code].append((game, previous, original))
    eligible, excluded = [], []
    for (code, day), members in sorted(groups.items()):
        ids = sorted(f["match_id"] for f in members)
        first = min(aware(f["kickoff_at"]) for f in members)
        group = dict(competition=code, date=day, first_kickoff=first.isoformat(), ids=ids)
        if first <= cutoff:
            excluded.append(group)
        else:
            eligible.append(group)
    if not eligible:
        raise ValueError("all original R7 slates have started")
    active = {mid for group in eligible for mid in group["ids"]}
    issued = {}
    for code, calendar_games in sorted(games.items()):
        recipe, native, recipe_hash = recipes[code]
        if recipe["through"] >= cutoff.astimezone(timezone.utc).date().isoformat():
            raise ValueError("forward training must precede issuance")
        calendar = model.Calendar(recipe["calendar"])
        ordered = sorted(calendar_games, key=lambda item:(item[0]["date"], item[0]["id"]))
        for stamp, items in groupby(ordered, key=lambda item:item[0]["date"]):
            day = date.fromisoformat(stamp).toordinal()
            matches = list(items)
            for game, previous, original in matches:
                mid = game["id"]
                if mid not in active:
                    continue
                p = previous["payload"]
                x, coverage = calendar.features(game, day)
                prior = r7.matrix(game, native)
                chosen, probability, _ = study.predict(prior, x, recipe["fitted"], recipe["doses"], p["line"],
                                                       p["forecasts"]["candidate"], p["quality"]["p_first"])
                issued[mid] = dict(fixture=fixtures[mid], home_name=p["home_name"], away_name=p["away_name"],
                    line=p["line"], coverage=coverage, recipe_sha256=recipe_hash,
                    r5_first=original["record_sha256"], r7_first=previous["record_sha256"],
                    forecast=dict(candidate=chosen, r7=p["forecasts"]["candidate"], market=p["forecasts"]["market"]),
                    primary_probability=dict(candidate=probability, r7=p["quality"]["p_first"]))
            for game, _, _ in matches:
                calendar.observe(game, day, scheduled=True)
    for group in eligible:
        group["selected"] = {arm:sorted(group["ids"], key=lambda mid:(-issued[mid]["primary_probability"][arm],mid))[:3]
                             for arm in ("candidate", "r7")}
    return dict(version=model.VERSION, protocol_sha256=study.PROTOCOL_SHA, source_manifests=metadata["manifests"],
                r7_original_registration=old.registration["record_sha256"], r5_original_registration=fifth.registration["record_sha256"],
                original_population=len(fixtures), cutoff=cutoff.isoformat(), included=eligible, excluded=excluded, matches=issued)


def lock(r5, seventh, study_folder, out, *, freeze_root=DEFAULT_ROOT):
    out.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory() as tmp:
        fifth, old, recipes, meta = inputs(r5, seventh, study_folder, Path(tmp))
        match_id = TRACK+":"+old.registration["record_sha256"]
        scope = dict(match_id=match_id, track=TRACK, stage="prematch")
        first_dir = Path(freeze_root)/digest(scope)
        first_dir.mkdir(parents=True, exist_ok=True)
        with exclusive_lock(first_dir/".batch.lock"):
            first = first_dir/"000001.json"
            existing = verify(read(first)) if first.is_file() else None
            cutoff = aware(existing["fixture"]["cutoff_at"]) if existing else datetime.now(timezone.utc)
            body = payload(fifth, old, recipes, meta, cutoff)
            earliest = min(aware(group["first_kickoff"]) for group in body["included"])
            fixture = dict(match_id=match_id, cutoff_at=cutoff.isoformat(), kickoff_at=earliest.isoformat())
            if existing and (existing["payload"] != body or existing["fixture"] != fixture or existing["revision"] != 1
                             or existing["evaluation_slot"] != "FIRST_PREMATCH" or existing["track"] != TRACK):
                raise ValueError("existing first calendar allocation conflicts with these inputs")
            ref = ledger._copy_receipt(save(body, fixture, stage="prematch", track=TRACK, root=freeze_root,
                                            idempotency_key=TRACK), out)
            index = out/"plan.json"
            if index.exists() and read(index) != ref:
                raise ValueError("output already refers to a different first allocation")
            if not index.exists():
                atomic_json(index,ref)
            return ref


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("r5", "r7", "study", "out"):
        parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--freeze-root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    print(json.dumps(lock(args.r5,args.r7,args.study,args.out,freeze_root=args.freeze_root)))
