"""Lock two preregistered seat orders for whole unstarted Beijing-date R7 slates."""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

import numpy as np

from publication import atomic_json
from research.analysis_freeze import DEFAULT_ROOT, aware, digest, save, verify
from storage_lock import exclusive_lock
from . import first_score_ranking as model
from . import first_score_ranking_study as study
from . import goal_state_forward as ledger
from .continuation_scores import VERSION as R7
from .goal_state_study import sha
from .r8_score_study import read
from .r9_score_study import verified_archive
from .territory_forward import FirstIssues


TRACK = 'research-r16-first-score-seats-20261125'
ZONE = ZoneInfo('Asia/Shanghai')


def _load(r7, folder, tmp):
    manifest = read(r7/'manifest.json')
    inputs, weights = read(folder/'inputs.json'), read(folder/'weights.json')
    codes = [r['code'] for r in read(study.SCOPE)['competitions']]
    if ((inputs['version'], inputs['protocol_sha256'], inputs['scope_sha256'], inputs['r7_manifest_sha256']) !=
            (model.VERSION, sha(study.PROTOCOL), sha(study.SCOPE), sha(r7/'manifest.json'))
            or set(weights) != set(codes) or set(inputs['inputs']) != set(codes)):
        raise ValueError('ranking study does not match its protocol, scope or original R7 archives')
    before = model.day(inputs['forward_training_before'])
    for code in codes:
        rows, member = study.panel(r7, manifest['files'], code)
        ref = inputs['inputs'][code]
        if ref != dict(archive=manifest['files']['models/' + code + '.tar.gz'], forecast_member=member):
            raise ValueError('forward recipe uses a different original R7 population')
        rebuilt = study.fit([r for r in rows if r['date'] < before])
        known = weights[code]['forward'].copy()
        coefficients = known.pop('coefficients')
        expected = rebuilt.pop('coefficients')
        if known != rebuilt or not np.allclose(coefficients, expected, atol=1e-9, rtol=1e-7):
            raise ValueError('ranking weights fail independent refit for ' + code)
    verified_archive(r7, manifest, 'first-issues.tar.gz', tmp)
    issued = FirstIssues.load(tmp)
    if issued.registration['payload']['identity']['version'] != R7:
        raise ValueError('original R7 registration required')
    return issued, inputs, weights


def payload(issued, inputs, weights, cutoff, fit_sha):
    if inputs['forward_training_before'] > cutoff.astimezone(timezone.utc).date().isoformat():
        raise ValueError('forward training cutoff follows seat selection')
    group = defaultdict(list)
    for mid, f in issued.registration['payload']['fixtures'].items():
        if f['match_id'] != mid or f['cohort'] not in weights:
            raise ValueError('registered R7 fixture or competition differs')
        local = aware(f['kickoff_at']).astimezone(ZONE).date().isoformat()
        group[f['cohort'], local].append(f)
    included, excluded = [], []
    for (code, day), fixtures in sorted(group.items()):
        arranged = sorted(fixtures, key=lambda f: f['match_id'])
        earliest = min(aware(f['kickoff_at']) for f in arranged)
        if earliest <= cutoff:
            excluded.append(dict(cohort=code, date=day, earliest=earliest.isoformat(),
                                 reason='SLATE_ALREADY_STARTED', ids=[f['match_id'] for f in arranged]))
            continue
        entries = []
        for f in arranged:
            row = issued.match(f, cutoff)
            orig = row['payload']
            spec = model.from_first(orig['forecasts'], orig['quality'])
            first = orig['forecasts']['candidate']
            if (len(first['top3']) != 3 or len(set(first['top3'])) != 3 or first['primary'] != first['top3'][0]):
                raise ValueError('original R7 ordered first and backups differ')
            entries.append(dict(id=f['match_id'], kickoff_at=f['kickoff_at'], home=f['home'], away=f['away'],
                primary=first['primary'], top3=first['top3'], original_forecast=first,
                original_recorded_at=row['recorded_at'], original_sha256=row['record_sha256'],
                raw=spec['p_first'], calibrated=model.predict(spec, weights[code]['forward'])))
        included.append(dict(cohort=code, date=day, earliest=earliest.isoformat(), slots=3, size=len(entries),
                             fixtures=entries, selected={rule:model.select(entries,rule) for rule in study.RULES}))
    if not included:
        raise ValueError('no whole unstarted R7 slate remains')
    return dict(version=model.VERSION, scope_sha256=inputs['scope_sha256'], protocol_sha256=inputs['protocol_sha256'],
                source_registration=issued.registration['record_sha256'], r7_manifest_sha256=inputs['r7_manifest_sha256'],
                weights_sha256=fit_sha, training_before=inputs['forward_training_before'], timezone='Asia/Shanghai',
                publication='separate prospective research selection', formal_stage_certified=False,
                source_population=len(issued.matches), included=included, excluded=excluded)


def lock(r7, folder, out, *, freeze_root=DEFAULT_ROOT):
    root = Path(freeze_root)
    root.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory() as tmp:
        firsts, inputs, weights = _load(r7, folder, Path(tmp))
        scope = dict(match_id=TRACK + ':' + firsts.registration['record_sha256'], track=TRACK, stage='prematch')
        directory = root/digest(scope)
        directory.mkdir(parents=True,exist_ok=True)
        with exclusive_lock(directory/'.seat-allocation.lock'):
            first = directory/'000001.json'
            old = verify(read(first)) if first.is_file() else None
            cutoff = aware(old['fixture']['cutoff_at']) if old else datetime.now(timezone.utc)
            selection = payload(firsts, inputs, weights, cutoff, sha(folder/'weights.json'))
            first_time = min(aware(s['earliest']) for s in selection['included'])
            fixture = dict(match_id=scope['match_id'], cutoff_at=cutoff.isoformat(), kickoff_at=first_time.isoformat())
            if old and (old['fixture'] != fixture or old['payload'] != selection or old['revision'] != 1
                        or old['evaluation_slot'] != 'FIRST_PREMATCH' or old['track'] != TRACK):
                raise ValueError('existing first-ranked allocation conflicts with requested study')
            receipt = save(selection, fixture, stage='prematch', track=TRACK, root=root, idempotency_key=TRACK)
            ref = ledger._copy_receipt(receipt, out)
            index = out/'plan.json'
            if index.exists() and read(index) != ref:
                raise ValueError('output already refers to a different allocation')
            if not index.exists():
                atomic_json(index,ref)
            return ref


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('r7','study','out'):
        p.add_argument('--'+name, type=Path, required=True)
    p.add_argument('--freeze-root', type=Path, default=DEFAULT_ROOT)
    args = p.parse_args()
    print(json.dumps(lock(args.r7,args.study,args.out,freeze_root=args.freeze_root)))
