"""Issue a first J1 market-only forecast from a retained pregame checkpoint."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from publication import atomic_json
from research.analysis_freeze import DEFAULT_ROOT, PrematchDeadlineElapsed, aware, digest, save, verify
from storage_lock import exclusive_lock
from . import display_window_review as display
from . import goal_state_forward as ledger
from . import j1_checkpoint_review as checkpoint
from . import market_baseline as market


def issue(directory, plan, fixtures, *, freeze_root=DEFAULT_ROOT):
    plan = verify(plan)
    name, entries, manifest_sha, receipts = checkpoint.capture_wave(directory, fixtures)
    track = checkpoint.benchmark_track(name)
    archive = directory / 'benchmark'
    archive.mkdir(exist_ok=True)
    index = archive / 'index.json'
    unavailable = {}
    with exclusive_lock(archive / '.issue.lock'):
        if not index.exists():
            references = []
            ordered = sorted(entries, key=lambda mid: (aware(entries[mid]['market_checkpoint']['deadline']), mid))
            for mid in ordered:
                source = entries[mid]
                if source['market_availability'] != 'FRESH_SAME_BOOK_PAIR':
                    unavailable[mid] = source['market_availability']
                    continue
                first, reason = _first_issue(directory, plan, source, receipts, manifest_sha, track, freeze_root)
                if first is None:
                    unavailable[mid] = reason
                    continue
                ref = ledger._copy_receipt(first, archive)
                references.append(dict(match_id=mid, file=ref['file'], freeze_id=ref['id'],
                                       sha256=ref['sha256'], recorded_at=ref['recorded_at']))
            atomic_json(index, references)
        _, records = checkpoint.read_wave(directory, plan, fixtures)
    return dict(checkpoint=name, registered=len(fixtures), frozen=len(records),
                match_ids=sorted(records), unavailable=unavailable)


def _first_issue(directory, plan, source, receipts, manifest_sha, track, root):
    mid = source['match_id']
    timing = source['market_checkpoint']
    scope = dict(match_id=mid, stage='prematch', track=track)
    key = digest([plan['freeze_id'], track, mid, manifest_sha])
    folder = Path(root) / digest(scope)
    folder.mkdir(parents=True, exist_ok=True)
    first = folder / '000001.json'
    with exclusive_lock(folder / '.checkpoint.lock'):
        if first.exists():
            record = verify(json.loads(first.read_bytes()))
            if record.get('idempotency_key') != key:
                return None, 'ORIGINAL_CHECKPOINT_ALREADY_FROZEN'
            return dict(path=str(first), freeze_id=record['freeze_id'], sha256=record['record_sha256'],
                        recorded_at=record['recorded_at']), None
        cutoff = datetime.now(timezone.utc)
        if cutoff > aware(timing['deadline']):
            return None, 'CHECKPOINT_ELAPSED'
        checkpoint.verify_capture_time(plan, source, receipts, cutoff)
        fixture = dict(source, cutoff_at=cutoff.isoformat())
        forecast = market.predict(fixture)
        if forecast['status'] != 'AVAILABLE':
            return None, forecast['reasons']
        checkpoint.verify_market_quote(source, fixture, forecast['quote_snapshot'], directory)
        payload = dict(registration_id=plan['freeze_id'], registration_sha256=plan['record_sha256'],
                       checkpoint=timing['name'], checkpoint_deadline=timing['deadline'],
                       capture_manifest_sha256=manifest_sha, forecast=forecast)
        try:
            receipt = save(payload, fixture, stage='prematch', track=track, root=root, idempotency_key=key,
                           first_issue_deadline=timing['deadline'])
        except PrematchDeadlineElapsed:
            return None, 'CHECKPOINT_ELAPSED'
        return receipt, None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registration', type=Path, required=True)
    parser.add_argument('--window', required=True)
    parser.add_argument('--capture', type=Path, required=True)
    parser.add_argument('--freeze-root', default=DEFAULT_ROOT)
    args = parser.parse_args()
    plan, universe, slates, _ = display._registered(json.loads(args.registration.read_bytes()))
    fixtures = {mid: universe[mid] for mid in slates[args.window]}
    print(json.dumps(issue(args.capture, plan, fixtures, freeze_root=args.freeze_root), ensure_ascii=False))


if __name__ == '__main__':
    main()
