"""Append-only issued analyses; freezing is independent of model promotion."""
import argparse
from datetime import datetime
import fcntl
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


def save(payload, fixture, *, stage, track, root=DEFAULT_ROOT):
    """Save actual issued content, including ABSTAIN. Never backdate or overwrite."""
    if stage not in ('prematch', 'live', 'review') or not track:
        raise ValueError('explicit stage and track required')
    reject_credentials({'fixture': fixture, 'payload': payload})
    now = datetime.now(ZoneInfo('Asia/Shanghai'))
    kickoff = aware(fixture['kickoff_at'])
    cutoff = aware(fixture['cutoff_at'])
    if cutoff > now or (stage == 'prematch' and now >= kickoff):
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
    with (folder / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        records = sorted(folder.glob('[0-9]*.json'))
        previous_hash = None
        for path in records:
            previous = verify(json.loads(path.read_text()))
            if previous['previous_sha256'] != previous_hash:
                raise ValueError('freeze chain mismatch')
            previous_hash = previous['record_sha256']
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
        record['record_sha256'] = digest(record)
        target = folder / f'{version:06d}.json'
        # Publish a complete fsynced record atomically, with no replacement.
        fd, temporary = tempfile.mkstemp(prefix='.pending-', dir=folder)
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(encoded(record)); handle.flush(); os.fsync(handle.fileno())
            os.link(temporary, target)
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', help='JSON containing fixture and payload')
    parser.add_argument('--stage', required=True, choices=['prematch', 'live', 'review'])
    parser.add_argument('--track', required=True)
    parser.add_argument('--root', default=DEFAULT_ROOT)
    args = parser.parse_args()
    data = json.loads(Path(args.input).read_text())
    print(json.dumps(save(data['payload'], data['fixture'], stage=args.stage,
                          track=args.track, root=args.root), ensure_ascii=False))


if __name__ == '__main__':
    main()
