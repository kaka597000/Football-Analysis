"""Recoverable prediction publication; retries reuse one immutable freeze."""
import copy
import json
import os
from pathlib import Path
import tempfile
import uuid
from research.analysis_freeze import save, reject_credentials
from storage_lock import exclusive_lock


def atomic_json(path, value):
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.publication-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush(); os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp): os.unlink(temp)


def deliver(path, content):
    if path.exists():
        if path.read_bytes() != content:
            raise FileExistsError(path)
        return
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.delivery-')
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(content); handle.flush(); os.fsync(handle.fileno())
        os.link(temp, path)  # complete file, no overwrite
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
            fixture, output = state['fixture'], state['output']
            freeze_root, stage, report = state['freeze_root'], state['stage'], state['report']
        else:
            if out.exists() or journal.exists(): raise FileExistsError(out)
            if report and (Path(report).exists() or Path(report).resolve() == out):
                raise FileExistsError(report)
            reject_credentials({'fixture':fixture,'output':output})
            state = {'id':uuid.uuid4().hex, 'status':'PREPARED', 'out':str(out),
                     'report':str(Path(report).resolve()) if report else None,
                     'fixture':fixture, 'output':copy.deepcopy(output),
                     'stage':stage, 'freeze_root':str(Path(freeze_root).resolve())}
            atomic_json(journal, state)
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
