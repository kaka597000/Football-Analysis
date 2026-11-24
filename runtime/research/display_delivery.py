"""Opt-in binding of an already ordered analyst row to an immutable prematch freeze."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re

from competition_scope import require
from research.analysis_freeze import aware, digest, save, verify

DISPLAY_TRACK = 'comprehensive'
SCHEMA = 'WCPP-ACTUAL-DISPLAY-1'
ABSTAIN_TEXT = '第一比分待定；两项备选待定'


def check_issue_scope(fixture, scope):
    """Use the issuer's retained literal stage, with the existing main scope checks."""
    require(fixture)
    if (fixture.get('identity_verified') is not True or fixture.get('stage_verified') is False
            or fixture.get('score_basis') != 'regulation_90'
            or str(fixture.get('phase', 'prematch')).casefold() != 'prematch'
            or not all(isinstance(fixture.get(k), str) and fixture[k].strip()
                       for k in ('match_id', 'competition', 'cohort', 'stage', 'home', 'away'))
            or fixture['home'] == fixture['away']):
        raise ValueError('verified prematch regulation fixture required')
    if (not isinstance(scope, dict) or not isinstance(scope.get('stages'), list)
            or (scope.get('cohort'), scope.get('competition')) != (fixture['cohort'], fixture['competition'])
            or fixture['stage'] not in scope['stages']):
        raise ValueError('fixture differs from the original approved cohort and stages')
    require(scope)


def _author_display(payload, fixture):
    row = payload.get('user_facing_row')
    if (payload.get('selection_scope') != 'active_reference' or not isinstance(row, dict)
            or row.get('match_id') != fixture['match_id']):
        raise ValueError('formal author row for this match required')
    first, alternatives = payload['primary'], payload['alternates']
    if (not isinstance(alternatives, list) or first != row['primary']
            or alternatives != row['alternates']):
        raise ValueError('author row and payload ranking differ')
    scores = [] if first is None else [first, *alternatives]
    if payload.get('displayed_scores') != scores or row.get('ordered_scores', scores) != scores:
        raise ValueError('author row and legacy score list differ')
    shown = row.get('kickoff_bjt')
    kickoff = aware(fixture['kickoff_at'])
    if shown is not None and shown != kickoff.astimezone(timezone(timedelta(hours=8))).strftime('%m-%d %H:%M'):
        if aware(shown) != kickoff:
            raise ValueError('author row kickoff differs')
    if first is None and not alternatives and payload.get('display_abstain') is True:
        return dict(schema=SCHEMA, status='ABSTAIN', first=None, alternatives=[], text=ABSTAIN_TEXT)
    if (len(scores) != 3 or payload.get('display_abstain')
            or not all(isinstance(s, str) and re.fullmatch(r'(?:0|[1-9][0-9]?)-(?:0|[1-9][0-9]?)', s)
                       for s in scores) or len(set(scores)) != 3):
        raise ValueError('three distinct ordered scores or an explicit abstention required')
    text = f'第一比分：{first}；备选一：{alternatives[0]}；备选二：{alternatives[1]}'
    return dict(schema=SCHEMA, status='ISSUED', first=first, alternatives=alternatives, text=text)


def save_authored_row(payload, fixture, *, scope, idempotency_key, root):
    """Keep the normal list and freeze envelope. An issuance key can only replay its original inputs."""
    if not isinstance(idempotency_key, str) or not idempotency_key.strip():
        raise ValueError('stable authored issuance key required')
    deadline = aware(fixture['kickoff_at'])
    if datetime.now(deadline.tzinfo) >= deadline:
        raise ValueError('authored handoff deadline passed')
    check_issue_scope(fixture, scope)
    display = _author_display(payload, fixture)
    if (payload.get('authored_display', display) != display
            or payload.get('authored_stage_scope', scope) != scope):
        raise ValueError('previous author binding differs')
    body = dict(payload, authored_display=display, authored_stage_scope=scope)
    receipt = save(body, fixture, stage='prematch', track=DISPLAY_TRACK, root=root,
                   idempotency_key=idempotency_key)
    record = verify(json.loads(Path(receipt['path']).read_bytes()))
    if receipt['sha256'] != record['record_sha256']:
        raise ValueError('authored freeze receipt mismatch')
    block = delivery_request(record)['text']
    if datetime.now(deadline.tzinfo) >= deadline:
        raise ValueError('authored handoff deadline passed')
    return dict(fixture=record['fixture'], payload=record['payload'], receipt=receipt, display_block=block)


def display_slot(match_id):
    return digest({'match_id': match_id, 'track': DISPLAY_TRACK, 'stage': 'prematch'})


def delivery_request(record):
    """Reproduce the exact fixture block from the verified author row and freeze."""
    record = verify(record)
    fixture, payload = record['fixture'], record['payload']
    if (record['stage'] != 'prematch' or record['track'] != DISPLAY_TRACK
            or record['match_id'] != fixture['match_id']
            or not aware(fixture['cutoff_at']) <= aware(record['recorded_at']) < aware(fixture['kickoff_at'])):
        raise ValueError('original comprehensive prematch fixture required')
    check_issue_scope(fixture, payload.get('authored_stage_scope'))
    display = _author_display(payload, fixture)
    if display != payload.get('authored_display'):
        raise ValueError('frozen author binding differs')
    stamp = aware(record['recorded_at']).astimezone(timezone(timedelta(hours=8)))
    text = (f"赛事 {record['match_id']}\n{display['text']}\n"
            f"冻结 {record['freeze_id']}（北京时间 {stamp:%Y-%m-%d %H:%M:%S}）")
    return dict(slot=display_slot(record['match_id']), match_id=record['match_id'], track=record['track'],
                revision=record['revision'], freeze_id=record['freeze_id'], freeze_sha256=record['record_sha256'],
                deadline=fixture['kickoff_at'], text=text)
