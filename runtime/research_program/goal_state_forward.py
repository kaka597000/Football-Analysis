"""Save first goal-state research issues and settle the registered slate from retained results."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from research.analysis_freeze import DEFAULT_ROOT, aware, digest, save, verify
from storage_lock import exclusive_lock
from .goal_state_study import write_new
from .kpi_validation import _actual, fixture_identity
from .paired_kpi_inference import compare


def _now():
    return datetime.now(timezone.utc).isoformat()


def _copy_receipt(receipt, folder):
    body = Path(receipt['path']).read_bytes()
    record = verify(json.loads(body))
    if record['record_sha256'] != receipt['sha256'] or record['evaluation_slot'] != 'FIRST_PREMATCH':
        raise ValueError('research arm requires its original prematch issue')
    path = folder / (receipt['freeze_id'] + '.json')
    if path.exists():
        if path.read_bytes() != body:
            raise ValueError('different archived freeze already exists')
    else:
        with path.open('xb') as stream:
            stream.write(body)
    return dict(id=receipt['freeze_id'], sha256=receipt['sha256'], file=path.name,
                recorded_at=receipt['recorded_at'])


def register(fixtures, identity, track, folder, *, freeze_root=DEFAULT_ROOT):
    folder.mkdir(parents=True, exist_ok=True)
    fields = ('match_id', 'cohort', 'stage', 'kickoff_at', 'home', 'away')
    ids = {f['match_id']: {k: f[k] for k in fields} for f in fixtures}
    if not ids or len(ids) != len(fixtures):
        raise ValueError('unique registered fixtures required')
    payload = dict(identity=identity, fixtures=ids, track=track)
    match_id = track+'-plan:'+digest(payload)
    plan_track = track+'-registration'
    scope = dict(match_id=match_id, stage='prematch', track=plan_track)
    canonical = Path(freeze_root) / digest(scope)
    canonical.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(canonical/'.registration.lock'):
        first = canonical/'000001.json'
        if first.exists():
            record = verify(json.loads(first.read_bytes()))
            if record['payload'] != payload or record['track'] != plan_track:
                raise ValueError('stored registration differs from requested study')
            return _copy_receipt(dict(path=str(first), sha256=record['record_sha256'],
                                 freeze_id=record['freeze_id'], recorded_at=record['recorded_at']), folder)
        fixture = dict(match_id=match_id, cutoff_at=_now(),
                       kickoff_at=min(aware(f['kickoff_at']) for f in fixtures).isoformat())
        receipt = save(payload, fixture, stage='prematch', track=plan_track, root=freeze_root,
                       idempotency_key=digest(payload))
        return _copy_receipt(receipt, folder)


def issue(plan, fixture, payload, track, folder, *, freeze_root=DEFAULT_ROOT):
    reg = _read(folder, plan)
    if (reg['payload']['track'] != track or
            fixture_identity(fixture) != fixture_identity(reg['payload']['fixtures'][fixture['match_id']]) or
            aware(fixture['cutoff_at']) < aware(reg['recorded_at'])):
        raise ValueError('first issue must follow registration of this fixture')
    receipt = save(dict(payload, registration_sha256=plan['sha256']), fixture,
                   stage='prematch', track=track, root=freeze_root, idempotency_key=plan['sha256'])
    return _copy_receipt(receipt, folder)


def _read(folder, ref):
    record = verify(json.loads((folder / ref['file']).read_bytes()))
    if (record['record_sha256'] != ref['sha256'] or record['freeze_id'] != ref['id']
            or record['evaluation_slot'] != 'FIRST_PREMATCH' or record['stage'] != 'prematch'
            or record['revision'] != 1 or record['previous_sha256'] is not None):
        raise ValueError('expected the registered first issue')
    return record


def review(folder, results, evidence_root, *, by_competition=False):
    index = json.loads((folder / 'receipts.json').read_bytes())
    reg = _read(folder, index['registration'])
    plan = reg['payload']
    population = plan['fixtures']
    result_by_id = {r['match_id']: r for r in results}
    if (set(index['matches']) != set(population) or len(result_by_id) != len(results)
            or result_by_id.keys() - population.keys()):
        raise ValueError('review must retain the full registered fixture population')
    rows = []
    for mid, ref in index['matches'].items():
        record = _read(folder, ref)
        payload, f = record['payload'], record['fixture']
        if (record['track'] != plan['track'] or payload['registration_sha256'] != reg['record_sha256']
                or f['match_id'] != mid or fixture_identity(f) != fixture_identity(population[mid])
                or not aware(reg['recorded_at']) <= aware(f['cutoff_at']) <= aware(record['recorded_at']) < aware(f['kickoff_at'])):
            raise ValueError('research issue identity, scope, or chronology mismatch')
        actual = _actual(result_by_id[mid], f, evidence_root, datetime.now(timezone.utc)) if mid in result_by_id else None
        rows.append(dict(id=mid, date=aware(f['kickoff_at']).astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat(),
                         cohort=f['cohort'], actual=actual, line=payload['line'], forecast=payload['forecasts']))
    def summarize(group):
        reports = {pair: compare(group, baseline=names[0], candidate=names[1])
                   for pair, names in plan['identity']['pairs'].items()}
        for report in reports.values():
            report['evidence'].update(mode='VERIFIED_FIRST_ISSUE_RESEARCH', registration_sha256=reg['record_sha256'],
                                      first_issue_integrity_verified=True, matchday_basis='Asia/Shanghai')
        return reports

    if by_competition:
        codes = plan['identity'].get('competitions', sorted({r['cohort'] for r in rows}))
        grouped = {c: [] for c in codes}
        for row in rows:
            grouped[row['cohort']].append(row)
        return {c: dict(registered=len(g), settled=sum(v['actual'] is not None for v in g),
                        comparisons=summarize(g) if g else {}) for c, g in grouped.items()}
    return summarize(rows)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--issued', type=Path, required=True)
    p.add_argument('--results', type=Path, required=True, help='JSON array in kpi_validation._actual evidence format')
    p.add_argument('--evidence-root', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--by-competition', action='store_true', help='Keep each registered competition and empty schedule separate.')
    args = p.parse_args()
    write_new(args.out, review(args.issued, json.loads(args.results.read_bytes()), args.evidence_root, by_competition=args.by_competition))
