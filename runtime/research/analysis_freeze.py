"""Append-only issued analyses; freezing is independent of model promotion."""
import argparse
from datetime import datetime
import sys
from pathlib import Path
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from storage_lock import exclusive_lock
import hashlib
import json
import os
from pathlib import Path
import tempfile
from zoneinfo import ZoneInfo

DEFAULT_ROOT = '/workspace/WCPP/forecasts/issued-analyses'


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def aware(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('timezone required')
    return result


def reject_credentials(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower().replace('-', '_') in {
                'api_key', 'apikey', 'authorization', 'password', 'access_token'}:
                raise ValueError('credential field prohibited')
            reject_credentials(item)
    elif isinstance(value, list):
        for item in value:
            reject_credentials(item)
    elif isinstance(value, str):
        if any(marker in value.lower() for marker in
               ('apikey=', 'api_key=', 'access_token=', 'bearer ')):
            raise ValueError('credential-bearing text prohibited')


def verify(record):
    body = {key: value for key, value in record.items() if key != 'record_sha256'}
    if digest(body) != record.get('record_sha256'):
        raise ValueError('freeze checksum mismatch')
    return record


def save(payload, fixture, *, stage, track, root=DEFAULT_ROOT, idempotency_key=None):
    """Save actual issued content, including ABSTAIN. Never backdate or overwrite."""
    if stage not in ('prematch', 'live', 'review') or not track:
        raise ValueError('explicit stage and track required')
    reject_credentials({'fixture': fixture, 'payload': payload})
    now = datetime.now(ZoneInfo('Asia/Shanghai'))
    kickoff = aware(fixture['kickoff_at'])
    cutoff = aware(fixture['cutoff_at'])
    if cutoff > now:
        raise ValueError('invalid cutoff or late prematch freeze')
    if stage == 'live' and now < kickoff:
        raise ValueError('live analysis before kickoff')
    try:
        from .score_distribution_audit import audit_issued
    except ImportError:
        try:
            from research.score_distribution_audit import audit_issued
        except ImportError:
            from score_distribution_audit import audit_issued
    selection_audit = audit_issued(payload, fixture, stage=stage, track=track,
                                   verify_record=verify)
    scope = {'match_id': fixture['match_id'], 'stage': stage, 'track': track}
    folder = Path(root) / digest(scope)
    folder.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(folder / '.lock'):
        records = sorted(folder.glob('[0-9]*.json'))
        previous_hash = None
        for path in records:
            previous = verify(json.loads(path.read_text(encoding='utf-8')))
            if previous['previous_sha256'] != previous_hash:
                raise ValueError('freeze chain mismatch')
            previous_hash = previous['record_sha256']
        if idempotency_key is not None:
            for path in records:
                prior = verify(json.loads(path.read_text(encoding='utf-8')))
                if prior.get('idempotency_key') == idempotency_key:
                    if prior['fixture'] != fixture or prior['payload'] != payload:
                        raise ValueError('publication idempotency conflict')
                    return {'freeze_id': prior['freeze_id'], 'recorded_at': prior['recorded_at'],
                            'path': str(path), 'sha256': prior['record_sha256'],
                            'evaluation_slot': prior['evaluation_slot']}
        # Lock contention and chain verification can cross kickoff. Do not use
        # the time captured before waiting as the issued record's timestamp.
        now = datetime.now(ZoneInfo('Asia/Shanghai'))
        if cutoff > now or (stage == 'prematch' and now >= kickoff):
            raise ValueError('invalid cutoff or late prematch freeze')
        version = len(records) + 1
        record = dict(scope, schema='WCPP-ISSUED-FREEZE-1',
                      freeze_id=f'{digest(scope)[:16]}-v{version:06d}',
                      recorded_at=now.isoformat(), revision=version,
                      previous_sha256=previous_hash, fixture=fixture, payload=payload,
                      evaluation_slot=('FIRST_PREMATCH' if stage == 'prematch'
                                       and version == 1 else 'SEPARATE_REVISION_OR_STAGE'),
                      model_promotion=False)
        if selection_audit is not None:
            record['selection_audit'] = selection_audit
        if idempotency_key is not None:
            record['idempotency_key'] = idempotency_key
        record['record_sha256'] = digest(record)
        target = folder / f'{version:06d}.json'
        # Publish a complete fsynced record atomically, with no replacement.
        fd, temporary = tempfile.mkstemp(prefix='.pending-', dir=folder)
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(encoded(record)); handle.flush(); os.fsync(handle.fileno())
            # Large payload serialization/fsync may also cross the boundary.
            commit_time = datetime.now(ZoneInfo('Asia/Shanghai'))
            if cutoff > commit_time or (stage == 'prematch' and commit_time >= kickoff):
                raise ValueError('invalid cutoff or late prematch freeze')
            os.link(temporary, target)
            if os.name != 'nt':
                directory_fd = os.open(folder, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            os.unlink(temporary)
    return {'freeze_id': record['freeze_id'], 'recorded_at': record['recorded_at'],
            'path': str(target), 'sha256': record['record_sha256'],
            'evaluation_slot': record['evaluation_slot']}


def save_authored_row(payload, fixture, *, scope, idempotency_key, root=DEFAULT_ROOT, out=None):
    """Freeze a signed-off analyst table once and return the normal batch envelope."""
    from research.display_delivery import bind_authored_row, delivery_request

    if not isinstance(idempotency_key, str) or not idempotency_key.strip():
        raise ValueError('stable authored issuance key required')
    deadline = aware(fixture['kickoff_at'])

    def before_handoff():
        if datetime.now(deadline.tzinfo) >= deadline:
            raise ValueError('authored handoff deadline passed')

    before_handoff()
    prepared = bind_authored_row(payload, fixture, scope)
    receipt = save(prepared, fixture, stage='prematch', track='comprehensive',
                   root=root, idempotency_key=idempotency_key)
    record = verify(json.loads(Path(receipt['path']).read_bytes()))
    if record['record_sha256'] != receipt['sha256']:
        raise ValueError('authored freeze receipt mismatch')
    issued = {'fixture': record['fixture'], 'payload': record['payload'], 'receipt': receipt,
              'display_block': delivery_request(record)['text']}
    before_handoff()
    if out is not None:
        from publication import deliver
        path = Path(out)
        path.parent.mkdir(parents=True, exist_ok=True)
        with exclusive_lock(str(path) + '.lock'):
            before_handoff()
            deliver(path, encoded(issued), before_commit=before_handoff)
    return issued


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', help='JSON containing fixture and payload')
    parser.add_argument('--stage', required=True, choices=['prematch', 'live', 'review'])
    parser.add_argument('--track', required=True)
    parser.add_argument('--root', default=DEFAULT_ROOT)
    parser.add_argument('--authored-table-key', help='freeze an explicitly signed-off analyst row '
                        'under its stable issue key and print the batch envelope')
    parser.add_argument('--authored-scopes', type=Path, help='existing approved cohort/stages rows for the batch')
    parser.add_argument('--authored-out', type=Path, help='atomically save the normal batch envelope locally')
    args = parser.parse_args()
    data = json.loads(Path(args.input).read_text(encoding='utf-8'))
    if (args.authored_table_key is None) != (args.authored_scopes is None):
        parser.error('--authored-table-key and --authored-scopes must be supplied together')
    if args.authored_table_key is None:
        if args.authored_out is not None:
            parser.error('--authored-out requires the authored-table options')
        result = save(data['payload'], data['fixture'], stage=args.stage,
                      track=args.track, root=args.root)
    else:
        from research.display_delivery import approved_scope_for

        if (args.stage, args.track) != ('prematch', 'comprehensive'):
            parser.error('--authored-table-key requires --stage prematch --track comprehensive')
        scopes = json.loads(args.authored_scopes.read_bytes())
        try:
            scope = approved_scope_for(scopes, data['fixture'])
        except ValueError as exc:
            parser.error(str(exc))
        if scope is None:
            parser.error('a unique approved stage scope for the fixture cohort is required')
        result = save_authored_row(data['payload'], data['fixture'], root=args.root, scope=scope,
                                   idempotency_key=args.authored_table_key, out=args.authored_out)
        if args.authored_out is not None:
            result = result['receipt']
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
