"""Stage and compare a normal ChatGPT batch without sending or certifying delivery."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from publication import deliver
from research.analysis_freeze import aware, digest, encoded, verify
from research.display_delivery import DISPLAY_TRACK, delivery_request, validate_display


def _text_hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def prepare(records, text, *, room_id, author_id, at):
    """Bind the exact intended message, room, and ordered fixture blocks before release."""
    if not records or not room_id or not author_id:
        raise ValueError('verified freezes and the expected room and author are required')

    matches = []
    slots = set()
    consumed_lines = 0
    lines = text.split('\n')
    for record in records:
        record = verify(record)
        if record['stage'] != 'prematch' or record['track'] != DISPLAY_TRACK:
            raise ValueError('only comprehensive prematch freezes can be staged')
        request = delivery_request(record)
        frozen_at, deadline = aware(record['recorded_at']), aware(request['deadline'])
        if not frozen_at <= at < deadline:
            raise ValueError('stage the original freeze before its planned kickoff')
        block = request['text'].split('\n')
        # The original block must occupy whole consecutive lines in the final message.
        positions = [index for index, line in enumerate(lines)
                     if line == block[0] and lines[index:index + len(block)] == block]
        if (request['slot'] in slots or len(positions) != 1 or positions[0] < consumed_lines):
            raise ValueError('each frozen display block must occur once and in batch order')
        consumed_lines = positions[0] + len(block)
        slots.add(request['slot'])
        display = validate_display(record['payload'])
        matches.append({'match_id': request['match_id'], 'freeze_id': request['freeze_id'],
                        'freeze_sha256': request['freeze_sha256'], 'request_sha256': digest(request),
                        'planned_kickoff_at': request['deadline'], 'display_status': display['status'],
                        'ordered_scores': [display['first'], *display['alternatives']]
                        if display['status'] == 'ISSUED' else []})

    plan = {'channel': 'chatgpt', 'room_id': room_id, 'author_id': author_id,
            'prepared_at': at.isoformat(), 'body_sha256': _text_hash(text), 'matches': matches}
    return {'plan': plan, 'plan_sha256': digest(plan)}


def match_readback(staged, response, *, message_id, at):
    """Verify the named provider readback, leaving first-visible and kickoff proof pending."""
    plan = staged['plan']
    if staged['plan_sha256'] != digest(plan):
        raise ValueError('staged batch checksum mismatch')
    message = response.get('message')
    if not message or response.get('partial') is not False:
        raise ValueError('named channel message unavailable or incomplete')
    if (not message_id or message.get('message_id') != message_id
            or response.get('room_id') != plan['room_id'] or message.get('channel') != plan['channel']
            or message.get('author_id') != plan['author_id'] or message.get('author') != 'aeon'
            or message.get('deleted_at') is not None):
        raise ValueError('readback does not match the expected visible message, room, or author')

    text = message['content']['text']
    if _text_hash(text) != plan['body_sha256']:
        raise ValueError('visible body does not match the staged message')
    sent = aware(message['sent_at'])
    if (not aware(plan['prepared_at']) <= sent <= at
            or any(sent >= aware(match['planned_kickoff_at']) for match in plan['matches'])):
        raise ValueError('service send time is outside the staged prematch window')

    return {'status': 'READBACK_MATCHED', 'first_visible_verified': False,
            'actual_kickoff_verified': False, 'message_id': message_id, 'sent_at': message['sent_at'],
            'room_id': plan['room_id'], 'channel': plan['channel'], 'author_id': plan['author_id'],
            'body_sha256': plan['body_sha256'], 'plan_sha256': staged['plan_sha256'],
            'matches': plan['matches']}


def _read_json(path):
    return json.loads(path.read_bytes())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    blocks = commands.add_parser('blocks', help='render the original fixture blocks for the regular sender')
    blocks.add_argument('freezes', type=Path, nargs='+')
    stage = commands.add_parser('stage', help='bind the final, unsent batch body to its freezes')
    stage.add_argument('freezes', type=Path, nargs='+')
    stage.add_argument('--body', required=True, type=Path)
    stage.add_argument('--room-id', required=True)
    stage.add_argument('--author-id', required=True)
    stage.add_argument('--out', required=True, type=Path)
    check = commands.add_parser('check', help='compare a direct, read-only channel response')
    check.add_argument('--plan', required=True, type=Path)
    check.add_argument('--readback', required=True, type=Path)
    check.add_argument('--message-id', required=True)
    check.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    if args.command == 'blocks':
        print('\n\n'.join(delivery_request(verify(_read_json(path)))['text'] for path in args.freezes))
        return

    now = datetime.now(timezone.utc)
    if args.command == 'stage':
        evidence = prepare([_read_json(path) for path in args.freezes], args.body.read_bytes().decode('utf-8'),
                           room_id=args.room_id, author_id=args.author_id, at=now)
    else:
        evidence = match_readback(_read_json(args.plan), _read_json(args.readback), message_id=args.message_id, at=now)
    deliver(args.out, encoded(evidence))


if __name__ == '__main__':
    main()
