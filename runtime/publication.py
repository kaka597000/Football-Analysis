"""Recoverable prediction publication; retries reuse one immutable freeze."""
import copy
import json
import os
from pathlib import Path
import tempfile
import uuid
from research.analysis_freeze import save, reject_credentials, verify, digest, aware
from storage_lock import exclusive_lock


def sync_directory(path):
    # fsync(file) alone does not make a newly linked/replaced name durable.
    if os.name != 'nt':
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def request_digest(state):
    return digest({key: state[key] for key in
                   ('id', 'out', 'report', 'fixture', 'output', 'stage', 'freeze_root')})


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
            or record['track'] != 'current-test-pair'
            or record['match_id'] != state['fixture']['match_id']
            or record['record_sha256'] != receipt['sha256']
            or record['freeze_id'] != receipt['freeze_id']
            or record['recorded_at'] != receipt['recorded_at']
            or record['evaluation_slot'] != receipt['evaluation_slot']):
        raise ValueError('publication freeze receipt mismatch')


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


def deliver(path, content):
    if path.exists():
        if path.read_bytes() != content:
            raise FileExistsError(path)
        sync_directory(path.parent)
        return
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.delivery-')
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(content); handle.flush(); os.fsync(handle.fileno())
        os.link(temp, path)  # complete file, no overwrite
        sync_directory(path.parent)
    finally:
        os.unlink(temp)


def publish(output, fixture, *, out, freeze_root, stage='prematch', report=None, resume=False):
    out = Path(out).resolve()
    journal = out.with_name(out.name + '.publication.json')
    out.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(str(journal) + '.lock'):
        if resume:
            state = json.loads(journal.read_text(encoding='utf-8'))
            if state['out'] != str(out): raise ValueError('publication target mismatch')
            if state.get('request_sha256') is not None and state['request_sha256'] != request_digest(state):
                raise ValueError('publication request checksum mismatch')
            verify_receipt(state)
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
            state['request_sha256'] = request_digest(state)
            atomic_json(journal, state)
        fixture, output = state['fixture'], state['output']
        validate_identity(output, fixture)
        # Repeated save after an interrupted process resolves the existing record.
        receipt = save(output, fixture, stage=stage, track='current-test-pair',
                       root=freeze_root, idempotency_key=state['id'])
        state['status'] = 'FROZEN'; state['receipt'] = receipt
        atomic_json(journal, state)
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
