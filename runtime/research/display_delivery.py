"""Exact comprehensive display text and the delivery evidence that binds it."""

from copy import deepcopy
from datetime import timedelta, timezone
import re

from research.analysis_freeze import aware, digest

SCHEMA = 'WCPP-ACTUAL-DISPLAY-1'
DISPLAY_TRACK = 'comprehensive'
ABSTAIN_TEXT = '第一比分待定；两项备选待定'


class DeliveryAfterDeadline(ValueError):
    """A confirmed service send was at or after this fixture's frozen kickoff."""


def valid_score(value):
    return isinstance(value, str) and re.fullmatch(r'(?:0|[1-9][0-9]?)-(?:0|[1-9][0-9]?)', value) is not None


def render(first, alternatives):
    return f'第一比分：{first}；备选一：{alternatives[0]}；备选二：{alternatives[1]}'


def score_text(first, alternatives):
    values = [first, *alternatives]
    if len(values) != 3 or not all(map(valid_score, values)) or len(set(values)) != 3:
        raise ValueError('exactly three distinct ordered scores required')
    return render(first, alternatives)


def attach_display(payload, first=None, alternatives=(), *, abstain=False):
    """Freeze the supplied ranking and its text without choosing any scores."""
    out = deepcopy(payload)
    if 'displayed_scores' in out:
        raise ValueError('display already present; append a new real prematch version instead')
    alternatives = list(alternatives)
    if abstain and (first is not None or alternatives):
        raise ValueError('abstention cannot carry scores')
    text = ABSTAIN_TEXT if abstain else score_text(first, alternatives)
    out['displayed_scores'] = dict(schema=SCHEMA, status='ABSTAIN' if abstain else 'ISSUED',
                                   first=first, alternatives=alternatives, text=text)
    return out, text


def prepare_authored_row(payload, fixture):
    """Convert an explicitly ranked analyst table row for a new future publication."""
    row = payload.get('user_facing_row')
    first, alternatives = payload.get('primary'), payload.get('alternates')
    if (payload.get('selection_scope') != 'active_reference' or not isinstance(row, dict)
            or row.get('match_id') != fixture['match_id'] or not isinstance(alternatives, list)):
        raise ValueError('verified formal analyst row for the same match required')
    scores = [first, *alternatives]
    if (scores != payload['displayed_scores'] or row.get('primary') != first
            or row.get('alternates') != alternatives or row.get('ordered_scores', scores) != scores):
        raise ValueError('authored table row and three-score ranking differ')
    kickoff = aware(fixture['kickoff_at'])
    shown = row.get('kickoff_bjt')
    if shown is not None and shown != kickoff.astimezone(timezone(timedelta(hours=8))).strftime('%m-%d %H:%M'):
        if aware(shown) != kickoff:
            raise ValueError('authored table row kickoff differs')
    body = {key: value for key, value in payload.items() if key != 'displayed_scores'}
    return attach_display(body, first, alternatives)[0]


def approved_scope_for(rows, fixture):
    """Select this cohort's row from the issuer's independently retained scope snapshot."""
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError('approved stages must be an array of scope records')
    matches = [row for row in rows if row.get('cohort') == fixture['cohort']]
    if len(matches) > 1:
        raise ValueError('a unique approved stage scope for the fixture cohort is required')
    return matches[0] if matches else None


def require_authored_stage(fixture, scope):
    """Use the author's independently approved scope row; competition membership alone is insufficient."""
    from automation.collector import PHASED_RESEARCH, REGULAR
    from competition_scope import require

    require(fixture)
    identities = (fixture.get(k) for k in ('match_id', 'competition', 'cohort', 'stage', 'home', 'away'))
    if (fixture.get('identity_verified') is not True
            or fixture.get('stage_verified') is False
            or not all(isinstance(value, str) and value.strip() for value in identities)
            or fixture['home'] == fixture['away'] or fixture.get('score_basis') != 'regulation_90'):
        raise ValueError('verified regulation-time fixture identity required')
    if fixture.get('phase') is not None and str(fixture['phase']).casefold() != 'prematch':
        raise ValueError('authored handoff requires a prematch fixture')
    if (not isinstance(scope, dict) or not isinstance(scope.get('stages'), list)
            or (scope.get('cohort'), scope.get('competition')) != (fixture['cohort'], fixture['competition'])):
        raise ValueError('approved stages for the same competition and cohort required')
    require(scope)
    competition, cohort, stage = fixture['competition'], fixture['cohort'], fixture['stage']
    token = stage
    if competition in REGULAR and (cohort != competition or stage not in ('regular', 'regular-season')):
        raise ValueError('fixture stage outside the approved regular league season')
    if competition == 'uefa.champions':
        token = 'NEW_FORMAT:league_phase'
        if cohort != f'{competition}:{token}' or stage not in ('league_phase', token):
            raise ValueError('fixture stage outside the approved UEFA era')
    elif competition == 'afc.champions':
        if cohort != f'{competition}:ELITE:league_phase' or stage != 'league_phase':
            raise ValueError('fixture stage outside the approved AFC era')
    elif competition in PHASED_RESEARCH:
        from research_program.phase_classification import PHASES
        if stage not in PHASES[competition]:
            raise ValueError('fixture stage outside the recognized competition phases')
    elif competition == 'uefa.nations':
        from research_program.nations_phase import PHASES
        if stage not in PHASES:
            raise ValueError('fixture stage outside the recognized Nations League phases')
    if token in scope['stages']:
        return
    # Specialist scope rows may carry the exact full cohort suffix as their stage token.
    qualified = cohort.removeprefix(competition + ':')
    if (competition not in ('uefa.champions', 'afc.champions') and qualified.endswith(':' + stage)
            and qualified in scope['stages']):
        return
    raise ValueError('fixture stage outside the approved cohort stages')


def bind_authored_row(payload, fixture, scope):
    """Keep the score list consumed by the normal batch packager in the signed payload."""
    require_authored_stage(fixture, scope)
    if not isinstance(payload.get('displayed_scores'), list):
        raise ValueError('an explicit analyst score list is required')
    display = prepare_authored_row(payload, fixture)['displayed_scores']
    if (('authored_display' in payload and payload['authored_display'] != display)
            or ('authored_stage_scope' in payload and payload['authored_stage_scope'] != scope)):
        raise ValueError('previously bound author display differs')
    return dict(payload, authored_display=display, authored_stage_scope=deepcopy(scope))


def validate_display(payload, *, allow_missing=False):
    """Return the canonical display. Unsigned historical rows have no display to score."""
    display = payload.get('displayed_scores')
    authored = 'authored_display' in payload or 'authored_stage_scope' in payload
    if authored and not isinstance(display, list):
        raise ValueError('authored display requires the retained table scores')
    if allow_missing and not authored and (not isinstance(display, dict) or display.get('schema') != SCHEMA):
        return None
    if isinstance(display, list):
        bound = payload.get('authored_display')
        if not isinstance(bound, dict) or payload.get('selection_scope') != 'active_reference':
            raise ValueError('explicit comprehensive display required')
        alternatives = bound.get('alternatives')
        if (not isinstance(alternatives, list) or display != [bound.get('first'), *alternatives]
                or payload.get('alternates') != alternatives):
            raise ValueError('bound display differs from the retained table scores')
        display = bound
    if not isinstance(display, dict) or display.get('schema') != SCHEMA:
        raise ValueError('explicit comprehensive display required')
    if payload.get('selection_scope', 'active_reference') != 'active_reference':
        raise ValueError('formal comprehensive display required')
    first, alternatives = display.get('first'), display.get('alternatives')
    if payload.get('primary') != first or not isinstance(alternatives, list):
        raise ValueError('display does not match comprehensive selection')
    if display.get('status') == 'ISSUED':
        expected = score_text(first, alternatives)
    elif display.get('status') == 'ABSTAIN' and first is None and alternatives == []:
        expected = ABSTAIN_TEXT
    else:
        raise ValueError('invalid display status')
    if display.get('text') != expected:
        raise ValueError('display text differs from ranked scores')
    return display


def display_slot(match_id):
    return digest({'match_id': match_id, 'track': DISPLAY_TRACK, 'stage': 'prematch'})


def delivery_request(record):
    """Build the exact message and binding from the verified frozen record."""
    fixture = record['fixture']
    payload = record['payload']
    display = validate_display(payload)
    if isinstance(payload['displayed_scores'], list):
        if (record['stage'] != 'prematch' or record['track'] != DISPLAY_TRACK
                or record['match_id'] != fixture['match_id']):
            raise ValueError('authored display requires the same comprehensive prematch fixture')
        require_authored_stage(fixture, payload.get('authored_stage_scope'))
        # Recheck the signed row, its kickoff, and its order when reading the request.
        if prepare_authored_row(payload, fixture)['displayed_scores'] != display:
            raise ValueError('bound author row differs from the frozen fixture')
    stamp = aware(record['recorded_at']).astimezone(timezone(timedelta(hours=8)))
    text = (f"赛事 {record['match_id']}\n{display['text']}\n"
            f"冻结 {record['freeze_id']}（北京时间 {stamp:%Y-%m-%d %H:%M:%S}）")
    return {'slot': display_slot(record['match_id']), 'match_id': record['match_id'],
            'track': record['track'], 'revision': record['revision'], 'freeze_id': record['freeze_id'],
            'freeze_sha256': record['record_sha256'], 'deadline': fixture['kickoff_at'], 'text': text}


def verify_delivery(confirmation, request, record, *, at):
    """Check the first visible receipt from the configured delivery authority."""
    if (confirmation.get('status') != 'DELIVERED'
            or confirmation.get('first_visible_verified', True) is not True
            or confirmation.get('request_sha256') != digest(request)
            or confirmation.get('text') != request['text']
            or any(k in confirmation and confirmation[k] != request[k] for k in
                   ('slot', 'match_id', 'track', 'revision', 'freeze_id', 'freeze_sha256'))
            or not isinstance(confirmation.get('message_id'), str)
            or not confirmation['message_id'].strip()):
        raise ValueError('delivery does not match the displayed freeze')
    if 'ordered_scores' in confirmation:
        display = validate_display(record['payload'])
        expected = [display['first'], *display['alternatives']] if display['status'] == 'ISSUED' else []
        if confirmation['ordered_scores'] != expected:
            raise ValueError('delivery order differs from the displayed freeze')
    delivered_at = aware(confirmation['delivered_at'])
    recorded_at = aware(record['recorded_at'])
    if not recorded_at <= delivered_at <= at:
        raise ValueError('delivery was not confirmed before the frozen kickoff')
    if delivered_at >= aware(request['deadline']):
        raise DeliveryAfterDeadline('delivery was not confirmed before the frozen kickoff')
    return delivered_at
