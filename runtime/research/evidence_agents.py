"""Bounded evidence-only research roles; no LLM, network or numeric overrides implied.

The host supplies caller(request)->record. Each analyst's first turn is isolated.
The auditor may request one evidence-grounded correction turn, within budget.
"""
import copy
from datetime import datetime, timezone
import hashlib
import json
import re
from urllib.parse import parse_qsl, urlsplit

VERSION = 'WCPP-EVIDENCE-AGENTS-1'
ROLES = ('identity_data', 'form_xg', 'lineup_schedule_motivation', 'market', 'auditor')
LIMIT = 10
RECORD_FIELDS = {'role', 'match_id', 'inputs_hash', 'as_of', 'status',
                 'observations', 'objections', 'corrections'}
STATUSES = {'EVIDENCE_REVIEWED', 'MISSING_EVIDENCE', 'OBJECTION', 'CORRECTION'}
NUMERIC_ASSERTION = re.compile(
    r'\d+(?:\.\d+)?\s*[%％]|\b\d+\s*[-:]\s*\d+\b|'
    r'(?:probability|lambda|概率|胜率|权重|预期进球)[^。\n]{0,16}\d', re.I)


def digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(',', ':'), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def aware(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('timezone required')
    return parsed


def validate_packet(packet):
    if set(packet) != {'match_id', 'as_of', 'kickoff_at', 'evidence'}:
        raise ValueError('unexpected evidence packet fields')
    if not isinstance(packet['match_id'], str) or not packet['match_id']:
        raise ValueError('match identity required')
    cutoff = aware(packet['as_of'])
    if cutoff > datetime.now(timezone.utc) or cutoff >= aware(packet['kickoff_at']):
        raise ValueError('future cutoff or invalid prematch cutoff')
    if not isinstance(packet['evidence'], list):
        raise ValueError('evidence list required')
    ids = {}
    for evidence in packet['evidence']:
        if set(evidence) != {'id', 'source_url', 'known_at', 'captured_at', 'fields'}:
            raise ValueError('unexpected evidence fields')
        eid = evidence['id']
        if not isinstance(eid, str) or not eid or eid in ids:
            raise ValueError('unique evidence ID required')
        if not isinstance(evidence['source_url'], str) or not evidence['source_url'].startswith('https://'):
            raise ValueError('HTTPS evidence source required')
        url = urlsplit(evidence['source_url'])
        if url.username or url.password or any(k.lower() in {'apikey', 'api_key', 'token', 'access_token'}
                                               for k, _ in parse_qsl(url.query)):
            raise ValueError('credential-bearing source URL forbidden')
        if not aware(evidence['known_at']) <= aware(evidence['captured_at']) <= cutoff:
            raise ValueError('future evidence or invalid capture order')
        if not isinstance(evidence['fields'], dict):
            raise ValueError('explicit evidence fields required')
        def no_secrets(value):
            if isinstance(value, dict):
                if any(str(k).lower().replace('-', '_') in {'apikey', 'api_key', 'authorization', 'password', 'token', 'access_token'} for k in value):
                    raise ValueError('secret fields forbidden in evidence packet')
                for child in value.values(): no_secrets(child)
            elif isinstance(value, list):
                for child in value: no_secrets(child)
        no_secrets(evidence['fields'])
        ids[eid] = evidence['fields']
    digest(packet)  # Reject non-JSON values/NaN before caller execution.
    return ids


def _text(value):
    if not isinstance(value, str) or not value.strip() or NUMERIC_ASSERTION.search(value):
        raise ValueError('text required; numeric probability/score assertions forbidden')


def validate_record(record, request, evidence, pending):
    if not isinstance(record, dict) or set(record) != RECORD_FIELDS:
        raise ValueError('unknown or missing role record fields')
    for key in ('role', 'match_id', 'inputs_hash', 'as_of'):
        if record[key] != request[key]:
            raise ValueError('record identity or evidence hash mismatch')
    if record['status'] not in STATUSES:
        raise ValueError('invalid role status')
    for key in ('observations', 'objections', 'corrections'):
        if not isinstance(record[key], list):
            raise ValueError('structured role arrays required')
    for item in record['observations']:
        if not isinstance(item, dict) or set(item) != {'evidence_id', 'field', 'interpretation'}:
            raise ValueError('invalid observation schema')
        if item['evidence_id'] not in evidence or item['field'] not in evidence[item['evidence_id']]:
            raise ValueError('unknown evidence or field')
        if request['round'] == 2 and record['role'] != 'auditor' and not any(
                o['target_role'] == record['role'] and o['field'] == item['field']
                and item['evidence_id'] in o['evidence_ids'] for o in pending.values()):
            raise ValueError('second turn observation outside objection scope')
        _text(item['interpretation'])
    objections = set()
    for item in record['objections']:
        if (not isinstance(item, dict) or set(item) != {'id', 'target_role', 'evidence_ids', 'field', 'reason', 'needs_correction'}
                or record['role'] != 'auditor' or item['target_role'] not in ROLES[:-1]
                or type(item['needs_correction']) is not bool):
            raise ValueError('invalid auditor objection')
        _text(item['id']); _text(item['reason'])
        if item['id'] in objections:
            raise ValueError('duplicate objection ID')
        objections.add(item['id'])
        if request['round'] == 2 and item['id'] not in pending:
            raise ValueError('second turn cannot introduce unrelated objections')
        if (not isinstance(item['evidence_ids'], list) or not item['evidence_ids']
                or any(eid not in evidence or item['field'] not in evidence[eid]
                       for eid in item['evidence_ids'])):
            raise ValueError('objection must cite captured evidence fields')
    for item in record['corrections']:
        if (not isinstance(item, dict) or set(item) != {'objection_id', 'evidence_id', 'field', 'interpretation'}
                or request['round'] != 2 or item['objection_id'] not in pending):
            raise ValueError('unsolicited or ungrounded correction')
        original = pending[item['objection_id']]
        if (original['target_role'] != record['role'] or item['evidence_id'] not in original['evidence_ids']
                or item['field'] != original['field']):
            raise ValueError('correction outside cited objection scope')
        _text(item['interpretation'])
    if request['round'] == 2 and record['role'] != 'auditor' and not record['corrections']:
        raise ValueError('second analyst turn requires grounded correction')
    if request['round'] == 1 and record['status'] == 'CORRECTION':
        raise ValueError('first turn cannot be a correction')
    digest(record)
    return record


def run(packet, caller=None, *, max_rounds=2, call_budget=LIMIT):
    """Return an evidence sidecar only; never accepts/returns a prediction override.

    A fresh deep copy goes to every caller. Calls are serial and bounded (no
    hidden retries). Exceptions and rejected records cannot approve a forecast.
    """
    if type(max_rounds) is not int or max_rounds not in (1, 2):
        raise ValueError('at most two rounds')
    if type(call_budget) is not int or not 0 <= call_budget <= LIMIT:
        raise ValueError('call budget must be between zero and ten')
    evidence = validate_packet(packet)
    frozen = copy.deepcopy(packet)
    fingerprint = digest(frozen)
    result = {'version': VERSION, 'match_id': packet['match_id'], 'as_of': packet['as_of'],
              'inputs_hash': fingerprint, 'status': 'NOT_RUN_NO_CALLER', 'calls': [],
              'calls_used': 0, 'max_rounds': max_rounds, 'call_budget': call_budget,
              'records': [], 'unresolved_objections': [],
              'numeric_prediction_modified': False, 'promotion_approved': False}
    if caller is None:
        return result
    pending = {}

    def invoke(role, round_number):
        if result['calls_used'] >= call_budget:
            return None
        request = {'role': role, 'round': round_number, 'match_id': frozen['match_id'],
                   'as_of': frozen['as_of'], 'inputs_hash': fingerprint,
                   'packet': copy.deepcopy(frozen),
                   'reports': copy.deepcopy(result['records']) if role == 'auditor' or round_number == 2 else [],
                   'pending_objections': copy.deepcopy(pending) if round_number == 2 else {},
                   'constraints': 'Cite existing evidence fields; no probability, lambda, weight or score outputs.'}
        log = {'role': role, 'round': round_number, 'status': 'CALL_FAILED'}
        result['calls_used'] += 1
        try:
            response = caller(copy.deepcopy(request))
            # Rejected originals are identified by hash, never echoed to logs.
            log['response_sha256'] = digest(response)
            accepted = validate_record(response, request, evidence, pending)
            result['records'].append(copy.deepcopy(accepted))
            log['status'] = 'ACCEPTED'
        except Exception as exc:
            # Callers can throw URLs containing credentials: store type only.
            log['error_type'] = type(exc).__name__
            accepted = None
        result['calls'].append(log)
        return accepted

    for role in ROLES:
        record = invoke(role, 1)
        if role == 'auditor' and record:
            pending = {o['id']: o for o in record['objections'] if o['needs_correction']}
    if pending and max_rounds == 2:
        targets = {o['target_role'] for o in pending.values()}
        for role in ROLES[:-1]:
            if role in targets:
                invoke(role, 2)
        # Re-audit only within the original immutable packet and call budget.
        audit = invoke('auditor', 2)
        if audit:
            corrected = {item['objection_id'] for record in result['records']
                         for item in record['corrections']}
            missing = {key: item for key, item in pending.items() if key not in corrected}
            pending = missing | {o['id']: o for o in audit['objections'] if o['needs_correction']}
    result['unresolved_objections'] = list(pending.values())
    failed = any(c['status'] != 'ACCEPTED' for c in result['calls'])
    all_first = {c['role'] for c in result['calls'] if c['round'] == 1 and c['status'] == 'ACCEPTED'} == set(ROLES)
    budget_hit = result['calls_used'] >= call_budget and (not all_first or bool(pending))
    result['status'] = ('INCOMPLETE_BUDGET' if budget_hit else 'INCOMPLETE_CALL_OR_SCHEMA'
                        if failed or not all_first else 'OPEN_OBJECTIONS'
                        if pending else 'EVIDENCE_REVIEW_COMPLETE_RESEARCH_ONLY')
    assert digest(frozen) == fingerprint
    return result
