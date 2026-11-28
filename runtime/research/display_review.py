"""Read-only review of the first actually displayed scores in a registered batch."""

from datetime import timedelta
from pathlib import Path

from research.analysis_freeze import aware, digest, verify
from research.chat_display_readback import first_delivery_lookup
from research.display_delivery import DISPLAY_TRACK, check_issue_scope, delivery_request
from research_program.three_arm_review import _results


def _check_fixture(record, fixture):
    frozen = record['fixture']
    keys = ('match_id', 'competition', 'cohort', 'stage', 'home', 'away', 'score_basis',
            'provider_competition', 'provider_stage')
    if (record['stage'] != 'prematch' or record['track'] != DISPLAY_TRACK
            or any(frozen.get(k) != fixture.get(k) for k in keys)
            or aware(frozen['kickoff_at']) != aware(fixture['kickoff_at'])
            or aware(frozen['cutoff_at']) != aware(fixture['cutoff_at'])):
        raise ValueError('frozen fixture, track or stage differs from the registered entry')


def _settle(fixture, record, result, issues, first, actual_start, approved_scope, at):
    row = dict(match_id=fixture['match_id'], track=DISPLAY_TRACK, verified_completed=result is not None,
               display_status='MISSING', result=result, top1_issued=False, top3_issued=False,
               top1_hit=None, top3_hit=None)
    if issues:
        row['result_issues'] = issues
    if record is None:
        return row
    payload = record['payload']
    if 'authored_display' not in payload and 'authored_stage_scope' not in payload:
        return row
    request = delivery_request(record)
    confirmation = first(request['slot'])
    if confirmation is None:
        return dict(row, display_issue='UNCONFIRMED_DELIVERY')
    scope = approved_scope(fixture) if approved_scope is not None else None
    if scope is None:
        return dict(row, display_issue='UNVERIFIED_ISSUE_SCOPE')
    check_issue_scope(fixture, scope)
    if scope != payload['authored_stage_scope']:
        raise ValueError('approved issue scope differs from the frozen scope')
    sent = aware(confirmation['delivered_at'])
    if sent >= aware(request['deadline']):
        return dict(row, display_issue='DELIVERED_AT_OR_AFTER_PLANNED_KICKOFF')
    start = actual_start(fixture) if actual_start is not None else None
    if start is None:
        return dict(row, display_issue='UNVERIFIED_ACTUAL_KICKOFF')
    if any(start[k] != fixture[k] for k in ('match_id', 'competition', 'home', 'away')):
        raise ValueError('actual kickoff report belongs to a different fixture')
    kickoff = start.get('actual_kickoff_at')
    interval = start.get('actual_kickoff_interval')
    if (kickoff is None) == (interval is None):
        raise ValueError('expected one exact kickoff or one verified kickoff minute')
    if interval is None:
        earliest = latest = aware(kickoff)
        proof = dict(actual_kickoff_at=kickoff)
    else:
        earliest = aware(interval['start_inclusive'])
        latest = aware(interval['end_exclusive'])
        if latest - earliest != timedelta(minutes=1) or earliest.second or earliest.microsecond:
            raise ValueError('expected one complete minute for official kickoff evidence')
        proof = dict(actual_kickoff_interval=interval)
    if latest > at:
        return dict(row, display_issue='UNVERIFIED_ACTUAL_KICKOFF')
    if sent >= latest:
        return dict(row, display_issue='DELIVERED_AT_OR_AFTER_KICKOFF')
    if sent >= earliest:
        return dict(row, display_issue='DELIVERY_INSIDE_KICKOFF_MINUTE_UNPROVEN')
    if result is not None and aware(result['source']['captured_at']) < latest + timedelta(minutes=90):
        raise ValueError('result observed before actual regulation could finish')

    display = payload['authored_display']
    row.update(display_status=display['status'], freeze_sha256=record['record_sha256'],
               message_id=confirmation['message_id'], delivered_at=confirmation['delivered_at'],
               original_body_sha256=confirmation['original_body_sha256'], **proof)
    if display['status'] == 'ISSUED':
        row.update(top1_issued=True, top3_issued=True)
        if result is not None:
            score = f"{result['home']}-{result['away']}"
            row.update(top1_hit=score == display['first'], top3_hit=score in payload['displayed_scores'])
    return row


def review_batch(fixtures, records, *, state, at, staged=None, room_id=None, author_id=None,
                 message_id=None, read_message=None, lookup_original=None, lookup_actual_kickoff=None,
                 lookup_approved_scope=None):
    """Grade the original population from retained freeze versions and already admitted regulation labels.

    The staged body establishes the order. Unsigned legacy records and
    missing fixtures remain in coverage. The two channel reads, actual kickoff
    and approved scope come from independently configured authorities.
    """
    registered = {f['match_id']: f for f in fixtures}
    if len(registered) != len(fixtures):
        raise ValueError('duplicate registered fixture')
    versions = {}
    for record in records:
        record = verify(record)
        mid = record['match_id']
        if (mid not in registered or record['fixture']['match_id'] != mid
                or record['stage'] != 'prematch' or record['track'] != DISPLAY_TRACK):
            raise ValueError('unexpected fixture, stage or track in the comprehensive archive')
        key = (mid, record['freeze_id'])
        if key in versions and versions[key]['record_sha256'] != record['record_sha256']:
            raise ValueError('conflicting content for the same freeze version')
        versions[key] = record
    selected = {}
    if staged is not None:
        plan = staged['plan']
        if staged['plan_sha256'] != digest(plan):
            raise ValueError('staged batch checksum differs')
        planned_ids = set()
        for ref in plan['matches']:
            mid = ref['match_id']
            if mid not in registered or mid in planned_ids:
                raise ValueError('unexpected or duplicate staged fixture')
            planned_ids.add(mid)
            record = versions.get((mid, ref['freeze_id']))
            if record is not None:
                if record['record_sha256'] != ref['freeze_sha256']:
                    raise ValueError('staged freeze hash differs from the retained version')
                _check_fixture(record, registered[mid])
                selected[mid] = record
        if len(selected) < len(planned_ids):
            # A lost original block makes the whole body unverifiable, even when a later revision exists.
            staged = None
    state = Path(state).resolve()
    if (state / 'normalized-results.jsonl').is_file():
        results, issues = _results(state, registered, at)
    else:
        results, issues = {}, {mid: ['normalized result archive missing'] for mid in registered}
    first = first_delivery_lookup(list(selected.values()), staged, room_id=room_id, author_id=author_id,
                                  message_id=message_id, read_message=read_message,
                                  lookup_original=lookup_original, at=at)
    return [_settle(f, selected.get(mid), results.get(mid), issues.get(mid), first,
                    lookup_actual_kickoff, lookup_approved_scope, at) for mid, f in registered.items()]


def summarize(rows):
    if any(r['track'] != DISPLAY_TRACK for r in rows) or len({r['match_id'] for r in rows}) != len(rows):
        raise ValueError('one comprehensive evaluation per registered match required')
    n = len(rows)
    complete = sum(r['verified_completed'] for r in rows)
    out = dict(registered_n=n, verified_completed_n=complete, pending_n=n - complete,
               missing_display_n=sum(r['display_status'] == 'MISSING' for r in rows),
               abstain_n=sum(r['display_status'] == 'ABSTAIN' for r in rows))
    for key in ('top1', 'top3'):
        issued = sum(r[key + '_issued'] for r in rows)
        settled = [r[key + '_hit'] for r in rows if r[key + '_hit'] is not None]
        out[key] = dict(issued_n=issued, coverage=issued / n if n else None, settled_n=len(settled),
                        hits=sum(settled) if settled else None, accuracy=sum(settled) / len(settled) if settled else None)
    return out
