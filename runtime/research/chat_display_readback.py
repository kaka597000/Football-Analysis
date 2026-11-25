"""Prepare a normal batch locally and read independent original-channel evidence."""

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

from publication import deliver

from research.analysis_freeze import aware, digest, encoded
from research.display_delivery import delivery_request
from storage_lock import exclusive_lock


def _text_hash(text):
    return sha256(text.encode('utf-8')).hexdigest()


def prepare(records, text, *, room_id, author_id, at):
    """Bind the exact final text, ordered blocks and intended room before kickoff."""
    if not records or not room_id or not author_id:
        raise ValueError('freezes and the expected room and author are required')
    matches, slots = [], set()
    consumed = 0
    lines = text.split('\n')
    for record in records:
        request = delivery_request(record)
        if not aware(record['recorded_at']) <= at < aware(request['deadline']):
            raise ValueError('stage the original freeze before its planned kickoff')
        block = request['text'].split('\n')
        positions = [i for i, line in enumerate(lines) if line == block[0]]
        if (request['slot'] in slots or len(positions) != 1 or positions[0] < consumed
                or lines[positions[0]:positions[0] + len(block)] != block):
            raise ValueError('each frozen display block must occur once and in batch order')
        consumed = positions[0] + len(block)
        slots.add(request['slot'])
        display = record['payload']['authored_display']
        matches.append(dict(match_id=request['match_id'], freeze_id=request['freeze_id'],
                            freeze_sha256=request['freeze_sha256'], request_sha256=digest(request),
                            planned_kickoff_at=request['deadline'], display_status=display['status'],
                            ordered_scores=list(record['payload']['displayed_scores'])))
    plan = dict(channel='chatgpt', room_id=room_id, author_id=author_id, prepared_at=at.isoformat(),
                body_sha256=_text_hash(text), matches=matches)
    return dict(plan=plan, plan_sha256=digest(plan))


def stage_message(records, text, *, room_id, author_id, path):
    """Record the final body commitment locally under the current clock, once."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(str(path) + '.lock'):
        if path.exists():
            prior = json.loads(path.read_bytes())
            original_at = aware(prior['plan']['prepared_at'])
            if prior != prepare(records, text, room_id=room_id, author_id=author_id, at=original_at):
                raise ValueError('existing staged message differs from the requested body')
            return prior
        staged = prepare(records, text, room_id=room_id, author_id=author_id, at=datetime.now(timezone.utc))
        deadline = min(aware(match['planned_kickoff_at']) for match in staged['plan']['matches'])

        def before_commit():
            if datetime.now(timezone.utc) >= deadline:
                raise ValueError('staging deadline passed before local commit')

        deliver(path, encoded(staged), before_commit=before_commit)
        return staged


def first_delivery_lookup(records, staged, *, room_id, author_id, message_id, read_message, lookup_original, at):
    """Readers are configured by the verifier for the authorized destination.

    read_message(id) reads the named visible message. lookup_original(slot) reads
    its independent, immutable *first* delivery for that fixture in that room.
    A missing reader, a lost message or a partial read cannot confirm a delivery.
    """
    if staged is None or not message_id or read_message is None or lookup_original is None:
        return lambda slot: None
    plan = staged['plan']
    if (staged['plan_sha256'] != digest(plan) or not room_id or not author_id
            or (plan['room_id'], plan['author_id']) != (room_id, author_id)):
        raise ValueError('staged batch checksum or authorized destination differs')
    response = read_message(message_id)
    if not response or response.get('partial') is not False or not response.get('message'):
        return lambda slot: None
    message = response['message']
    if (response.get('room_id') != room_id or message.get('channel') != 'chatgpt'
            or message.get('author') != 'aeon' or message.get('author_id') != author_id
            or message.get('message_id') != message_id or message.get('deleted_at') is not None):
        raise ValueError('named readback differs from the authorized message')
    body = message['content']['text']
    prepared = aware(plan['prepared_at'])
    sent = aware(message['sent_at'])
    if not prepared <= sent <= at:
        raise ValueError('service send time outside staged window')
    if prepare(records, body, room_id=room_id, author_id=author_id, at=prepared) != staged:
        raise ValueError('readback differs from the original staged body or frozen records')
    requests = {}
    for record in records:
        request = delivery_request(record)
        requests[request['slot']] = (request, record['payload']['displayed_scores'])

    def lookup(slot):
        if slot not in requests:
            return None
        request, scores = requests[slot]
        first = lookup_original(slot)
        if first is None:
            return None
        expected = dict(status='DELIVERED', slot=slot, channel='chatgpt', room_id=room_id,
                        author_id=author_id, message_id=message_id, original_body_sha256=plan['body_sha256'])
        if (not isinstance(first, dict) or any(first.get(k) != v for k, v in expected.items())
                or not isinstance(first.get('delivered_at'), str) or aware(first['delivered_at']) != sent):
            raise ValueError('first channel delivery conflicts with the named original message')
        details = dict(match_id=request['match_id'], track=request['track'], revision=request['revision'],
                       freeze_id=request['freeze_id'], freeze_sha256=request['freeze_sha256'],
                       request_sha256=digest(request), body_sha256=plan['body_sha256'], original_text=body,
                       ordered_scores=scores, first_visible_verified=True)
        if (any(k in first and first[k] != v for k, v in details.items())
                or ('first_visible_verified' in first and first['first_visible_verified'] is not True)
                or first.get('text', body) not in (body, request['text'])):
            raise ValueError('first delivery assertions conflict with the original body or ordered freeze')
        return first

    return lookup
