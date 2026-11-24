"""Recoverable prediction publication; retries reuse one immutable freeze."""
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import uuid
from research.analysis_freeze import save, reject_credentials, verify, digest, aware, encoded
from research.display_delivery import (
    DISPLAY_TRACK, delivery_request, display_slot, prepare_authored_row, validate_display, verify_delivery,
)
from storage_lock import exclusive_lock

MODEL_TRACK = 'current-test-pair'


def sync_directory(path):
    # fsync(file) alone does not make a newly linked/replaced name durable.
    if os.name != 'nt':
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def request_digest(state):
    keys = ('id', 'out', 'report', 'fixture', 'output', 'stage', 'freeze_root')
    if 'track' in state:
        keys += ('track',)
    return digest({key: state[key] for key in keys})


def validate_identity(output, fixture):
    expected = fixture['match_id']
    if 'match_id' in output and output['match_id'] != expected:
        raise ValueError('publication fixture/output identity mismatch')
    for key in ('current_model', 'test_model'):
        model = output.get(key)
        if isinstance(model, dict):
            source = model.get('input')
            if isinstance(source, dict) and 'match_id' in source and source['match_id'] != expected:
                raise ValueError('publication model input identity mismatch')
            if isinstance(source, dict):
                for field in ('kickoff_at', 'cutoff_at'):
                    if field in source and aware(source[field]) != aware(fixture[field]):
                        raise ValueError('publication model input time mismatch: ' + field)
                # Actual predictors preserve the complete fixture. Compare all
                # context (including venue and real quotes), allowing equivalent
                # timezone representations without inventing missing fields.
                times = {'kickoff_at', 'cutoff_at'}
                if (set(source) != set(fixture)
                        or {k: v for k, v in source.items() if k not in times}
                        != {k: v for k, v in fixture.items() if k not in times}):
                    raise ValueError('publication model input context mismatch')
            elif source is not None:
                raise ValueError('publication model input context mismatch')


def verify_receipt(state):
    receipt = state.get('receipt')
    if receipt is None:
        if state.get('status') in ('FROZEN', 'DELIVERED'):
            raise ValueError('frozen publication receipt missing')
        return
    path = Path(receipt['path']).resolve()
    if path.parent.parent != Path(state['freeze_root']).resolve():
        raise ValueError('publication freeze root mismatch')
    record = verify(json.loads(path.read_text(encoding='utf-8')))
    if (record.get('idempotency_key') != state['id']
            or record['fixture'] != state['fixture']
            or record['payload'] != state['output']
            or record['stage'] != state['stage']
            or record['track'] != state.get('track', MODEL_TRACK)
            or record['match_id'] != state['fixture']['match_id']
            or record['record_sha256'] != receipt['sha256']
            or record['freeze_id'] != receipt['freeze_id']
            or record['recorded_at'] != receipt['recorded_at']
            or record['evaluation_slot'] != receipt['evaluation_slot']):
        raise ValueError('publication freeze receipt mismatch')
    return record


def atomic_json(path, value):
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.publication-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush(); os.fsync(handle.fileno())
        os.replace(temp, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(temp): os.unlink(temp)


def deliver(path, content, *, before_commit=None):
    if path.exists():
        if path.read_bytes() != content:
            raise FileExistsError(path)
        sync_directory(path.parent)
        return
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.delivery-')
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(content); handle.flush(); os.fsync(handle.fileno())
        if before_commit is not None:
            before_commit()
        os.link(temp, path)  # complete file, no overwrite
        sync_directory(path.parent)
    finally:
        os.unlink(temp)


def _deliver_comprehensive(state, record, journal, receipt_path, publisher):
    request = delivery_request(record)
    deadline = aware(request['deadline'])
    pending = {'freeze_receipt': state['receipt'], 'publication_status': 'UNCONFIRMED'}
    if publisher is None:
        return pending

    confirmation = publisher.lookup_first(request['slot'])
    attempted_send = False
    if (confirmation is None and state['status'] == 'FROZEN' and not receipt_path.exists()
            and datetime.now(timezone.utc) < deadline):
        publisher.send_first(copy.deepcopy(request))
        attempted_send = True
        confirmation = publisher.lookup_first(request['slot'])
    if confirmation is None:
        return pending

    verify_delivery(confirmation, request, record, at=datetime.now(timezone.utc))
    event = {'request': request, 'confirmation': confirmation}
    deliver(receipt_path, encoded(event))
    if state['status'] != 'DELIVERED':
        state['status'] = 'DELIVERED'
        atomic_json(journal, state)

    published = dict(state['output'], freeze_receipt=state['receipt'],
                     publication_status='DELIVERED', display_delivery=event)

    def before_mirror():
        if datetime.now(timezone.utc) >= deadline:
            raise ValueError('prematch publication mirror deadline passed')

    # A lookup-only recovery returns the original confirmation. Recreating a
    # lost presentation file could look like a new release, even if the clock
    # still precedes the frozen schedule.
    if attempted_send and datetime.now(timezone.utc) < deadline:
        deliver(Path(state['out']), encoded(published), before_commit=before_mirror)
        if state['report']:
            target = Path(state['report'])
            target.parent.mkdir(parents=True, exist_ok=True)
            deliver(target, request['text'].encode('utf-8'), before_commit=before_mirror)
    return published


def publish(output, fixture, *, out, freeze_root, stage='prematch', report=None, resume=False,
            track=MODEL_TRACK, publisher=None):
    """Freeze and deliver. The optional comprehensive route needs a trusted publisher.

    lookup_first(slot) reads the immutable first visible receipt. send_first(request)
    honors the deadline and atomically deduplicates first-display attempts by slot.
    Resume uses the same authority, including after a lost response or restart.
    """
    if track not in (MODEL_TRACK, DISPLAY_TRACK) or (publisher is not None and track != DISPLAY_TRACK):
        raise ValueError('unsupported publication track or publisher')
    if track == DISPLAY_TRACK and stage != 'prematch':
        raise ValueError('first comprehensive display is prematch only')
    out = Path(out).resolve()
    journal = out.with_name(out.name + '.publication.json')
    delivery_path = out.with_name(out.name + '.display-receipt.json')
    out.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(str(journal) + '.lock'):
        record = None
        if resume:
            state = json.loads(journal.read_text(encoding='utf-8'))
            if state['out'] != str(out): raise ValueError('publication target mismatch')
            if state.get('track', MODEL_TRACK) != track:
                raise ValueError('publication track mismatch')
            checksum = state.get('request_sha256')
            if track == DISPLAY_TRACK or checksum is not None:
                if checksum != request_digest(state):
                    raise ValueError('publication request checksum mismatch')
            record = verify_receipt(state)
            fixture, output = state['fixture'], state['output']
            freeze_root, stage, report = state['freeze_root'], state['stage'], state['report']
        else:
            if out.exists() or journal.exists(): raise FileExistsError(out)
            if report and (Path(report).exists() or Path(report).resolve() in (out, journal)):
                raise FileExistsError(report)
            reject_credentials({'fixture':fixture,'output':output})
            validate_identity(output, fixture)
            state = {'id':uuid.uuid4().hex, 'status':'PREPARED', 'out':str(out),
                     'report':str(Path(report).resolve()) if report else None,
                     'fixture':copy.deepcopy(fixture), 'output':copy.deepcopy(output),
                     'stage':stage, 'freeze_root':str(Path(freeze_root).resolve())}
            if track == DISPLAY_TRACK:
                from competition_scope import require
                require(fixture)
                if not all(fixture.get(k) for k in ('match_id', 'cohort', 'stage', 'home', 'away')):
                    raise ValueError('complete fixture identity required for display')
                if delivery_path.exists() or state['report'] == str(delivery_path):
                    raise FileExistsError(delivery_path)
                if datetime.now(timezone.utc) >= aware(fixture['kickoff_at']):
                    raise ValueError('prematch display deadline passed')
                if isinstance(output.get('displayed_scores'), list):
                    output = prepare_authored_row(output, fixture)
                    state['output'] = output
                validate_display(output)
                if publisher is not None and publisher.lookup_first(display_slot(fixture['match_id'])) is not None:
                    raise FileExistsError('first comprehensive display is already confirmed')
                state['track'] = track
            state['request_sha256'] = request_digest(state)
            atomic_json(journal, state)
        fixture, output = state['fixture'], state['output']
        validate_identity(output, fixture)
        if track == DISPLAY_TRACK and record is not None:
            return _deliver_comprehensive(state, record, journal, delivery_path, publisher)
        # Repeated save after an interrupted process resolves the existing record.
        receipt = save(output, fixture, stage=stage, track=track,
                       root=freeze_root, idempotency_key=state['id'])
        state['status'] = 'FROZEN'; state['receipt'] = receipt
        atomic_json(journal, state)
        if track == DISPLAY_TRACK:
            return _deliver_comprehensive(state, verify_receipt(state), journal, delivery_path, publisher)
        published = dict(output, freeze_receipt=receipt)
        content = json.dumps(published, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')
        deliver(out, content)
        if report:
            from research.match_analysis import render
            target = Path(report); target.parent.mkdir(parents=True, exist_ok=True)
            deliver(target, render(output['match_analysis']).encode('utf-8'))
        state['status'] = 'DELIVERED'
        atomic_json(journal, state)
        return published
