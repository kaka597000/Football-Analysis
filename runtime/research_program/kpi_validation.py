"""Register, capture and score a fixed future cohort against a market-only baseline."""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import re
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from competition_scope import require
from optimization.evaluate import settle_ou
from research.analysis_freeze import DEFAULT_ROOT, aware, digest, save, verify
from research_program.future_identity import fixture_identity
from research_program.future_validation import quote_audit, read, sha, write_new
from research_program.kpi_targets import CURRENT_SCHEMA, ORIGINAL_SCHEMA, PROTOCOLS, TARGETS
from research_program.replay import _finished_espn_label

REGISTRATION = 'kpi-preregistration'
FORECAST = 'kpi-first-forecast'
IDENTITY_FIELDS = ('match_id', 'cohort', 'stage', 'kickoff_at', 'home', 'away')
ROLES = ('candidate', 'market_baseline')
OUTCOMES = ('FULL_WIN', 'HALF_WIN', 'PUSH', 'HALF_LOSS', 'FULL_LOSS')
LABELS = ('home_goals', 'away_goals', 'actual', 'result')


def _utcnow():
    return datetime.now(timezone.utc)


def _unlabeled(fixture):
    require(fixture)
    if (fixture.get('identity_verified') is not True
            or any(not fixture[key] for key in IDENTITY_FIELDS)
            or fixture['home'] == fixture['away']
            or any(key in fixture for key in LABELS)):
        raise ValueError('verified, unlabeled fixture with distinct teams required')


def _artifact(spec):
    version, sha256 = spec['version'], spec['artifact_sha256']
    if not version or not re.fullmatch('[0-9a-f]{64}', sha256):
        raise ValueError('locked candidate or market specification identity required')
    return {'version': version, 'artifact_sha256': sha256}


def _registered_receipt(payload, root):
    mid = 'kpi-plan:' + digest(payload)
    scope = {'match_id': mid, 'stage': 'prematch', 'track': REGISTRATION}
    path = Path(root) / digest(scope) / '000001.json'
    if not path.is_file():
        return None
    record = read(path)
    if _plan(record) != payload:
        raise ValueError('conflicting saved KPI registration')
    return {'path': str(path), 'freeze_id': record['freeze_id'], 'recorded_at': record['recorded_at'],
            'sha256': record['record_sha256'], 'evaluation_slot': record['evaluation_slot']}


def register(fixtures, candidate, market_baseline, *, freeze_root=DEFAULT_ROOT):
    """Lock the complete stated fixture universe and both model identities before kickoff."""
    now = _utcnow()
    identities = {}
    natural_keys = set()
    for fixture in fixtures:
        _unlabeled(fixture)
        mid, *key = fixture_identity(fixture)
        natural_key = tuple(key)
        if mid in identities or natural_key in natural_keys:
            raise ValueError('duplicate registered fixture')
        identities[mid] = {key: fixture[key] for key in IDENTITY_FIELDS}
        natural_keys.add(natural_key)
    if not identities:
        raise ValueError('explicit future population required')
    payload = {
        'schema': CURRENT_SCHEMA,
        'targets': TARGETS,
        'fixtures': identities,
        'candidate': _artifact(candidate),
        'market_baseline': _artifact(market_baseline),
        'population_rule': 'ALL_REGISTERED_FIXTURES_FIRST_PREMATCH',
        'total_rule': 'STRICT_FULL_WIN_EXCLUDING_PUSH; COVERAGE_ACCOUNTED_SEPARATELY',
    }
    # An upgraded runner retrying the same cohort must resume the original contract.
    legacy = dict(payload, schema=ORIGINAL_SCHEMA, targets=PROTOCOLS[ORIGINAL_SCHEMA])
    for prior in (legacy, payload):
        receipt = _registered_receipt(prior, freeze_root)
        if receipt is not None:
            return receipt
    kickoff = min(aware(f['kickoff_at']) for f in identities.values())
    if kickoff <= now:
        raise ValueError('only future fixtures may register')
    plan_id = 'kpi-plan:' + digest(payload)
    shell = {'match_id': plan_id, 'cutoff_at': now.isoformat(), 'kickoff_at': kickoff.isoformat()}
    try:
        return save(payload, shell, stage='prematch', track=REGISTRATION, root=freeze_root,
                    idempotency_key=plan_id)
    except ValueError as error:
        if str(error) == 'publication idempotency conflict':
            receipt = _registered_receipt(payload, freeze_root)
            if receipt is not None:
                return receipt
        raise


def _plan(record):
    record = _first(record, REGISTRATION)
    payload = record['payload']
    schema = payload.get('schema')
    if (schema not in PROTOCOLS or payload.get('targets') != PROTOCOLS[schema] or not payload.get('fixtures')
            or record['match_id'] != 'kpi-plan:' + digest(payload)
            or record['match_id'] != record['fixture']['match_id']):
        raise ValueError('registered KPI protocol required')
    keys = set()
    for mid, fixture in payload['fixtures'].items():
        match, *natural = fixture_identity(fixture)
        if mid != match or tuple(natural) in keys or aware(record['recorded_at']) >= aware(fixture['kickoff_at']):
            raise ValueError('conflicting registration population')
        keys.add(tuple(natural))
    for role in ROLES:
        _artifact(payload[role])
    return payload


def _first(record, track):
    verify(record)
    fixture = record['fixture']
    if (record.get('track') != track or record.get('stage') != 'prematch' or record.get('revision') != 1
            or record.get('evaluation_slot') != 'FIRST_PREMATCH' or record.get('previous_sha256') is not None
            or not aware(fixture['cutoff_at']) <= aware(record['recorded_at']) < aware(fixture['kickoff_at'])):
        raise ValueError('original prematch freeze required')
    return record


def _selection(prediction, identity, cutoff, issued_at, actual_line):
    if _artifact(prediction) != identity:
        raise ValueError('forecast differs from locked identity')
    if not cutoff <= aware(prediction['generated_at']) <= issued_at:
        raise ValueError('forecast generation outside the captured cutoff')
    primary, three = prediction.get('primary'), prediction.get('top3')
    score = r'(0|[1-9][0-9]?)-(0|[1-9][0-9]?)'
    if primary is not None and not (isinstance(primary, str) and re.fullmatch(score, primary)):
        raise ValueError('invalid original first score')
    if three is not None and (
        not isinstance(three, list) or len(three) != 3 or primary != three[0]
        or any(not (isinstance(v, str) and re.fullmatch(score, v)) for v in three) or len(set(three)) != 3
    ):
        raise ValueError('Top3 must contain three unique exact scores with Primary first')
    if prediction.get('wdl') not in (None, 'home', 'draw', 'away'):
        raise ValueError('invalid WDL direction')
    if prediction.get('total_side') is not None:
        if prediction['total_side'] not in ('over', 'under') or actual_line is None:
            raise ValueError('total choice requires a verified pre-kickoff line')
        if prediction.get('total_line') != actual_line:
            raise ValueError('total choice differs from common actual line')
    selected = {**identity, **{key: prediction.get(key) for key in (
        'generated_at', 'primary', 'top3', 'wdl', 'total_side', 'total_line'
    )}}
    evidence_hash = prediction.get('forecast_evidence_sha256')
    if evidence_hash is not None:
        if not isinstance(evidence_hash, str) or not re.fullmatch('[0-9a-f]{64}', evidence_hash):
            raise ValueError('forecast evidence digest must be SHA-256')
        selected['forecast_evidence_sha256'] = evidence_hash
    return selected


def _validate_fixture(plan_record, plan, fixture, captured_at):
    _unlabeled(fixture)
    mid = fixture['match_id']
    if mid not in plan['fixtures'] or fixture_identity(fixture) != fixture_identity(plan['fixtures'][mid]):
        raise ValueError('fixture outside registered identity')
    cutoff = aware(fixture['cutoff_at'])
    if not aware(plan_record['recorded_at']) <= cutoff <= captured_at < aware(fixture['kickoff_at']):
        raise ValueError('invalid preregistration or prematch chronology')
    audit = quote_audit(fixture)
    return cutoff, audit['quote']['line'] if audit['eligible'] else None


def capture(plan_record, fixture, candidate, market_baseline, *, freeze_root=DEFAULT_ROOT):
    """Freeze the two original selections together with the same actual-market input."""
    plan = _plan(plan_record)
    now = _utcnow()
    cutoff, line = _validate_fixture(plan_record, plan, fixture, now)
    payload = {'plan_sha256': plan_record['record_sha256'],
               'candidate': _selection(candidate, plan['candidate'], cutoff, now, line),
               'market_baseline': _selection(market_baseline, plan['market_baseline'], cutoff, now, line)}
    # Use the globally first record for this fixture and track. Different payloads
    # cannot append or replace it even when multiple captures run concurrently.
    receipt = save(payload, fixture, stage='prematch', track=FORECAST, root=freeze_root,
                   idempotency_key=FORECAST)
    if receipt['evaluation_slot'] != 'FIRST_PREMATCH':
        raise ValueError('KPI first capture has already been issued')
    return receipt


def _actual(result, fixture, evidence_root, now):
    if result.get('event_status') != 'FT':
        return None
    if (result.get('score_basis') != 'regulation_90' or fixture_identity(result) != fixture_identity(fixture)
            or any(type(result.get(key)) is not int or result[key] < 0 for key in LABELS[:2])):
        raise ValueError('finished label must match the registered regulation-90 fixture')
    observed = aware(result['observed_at'])
    if not aware(fixture['kickoff_at']) + timedelta(minutes=90) <= observed <= now:
        raise ValueError('invalid full-time observation time')
    evidence = result.get('evidence')
    if not evidence or evidence_root is None:
        raise ValueError('retained full-time evidence required')
    root = Path(evidence_root).resolve()
    path = (root / evidence['file']).resolve()
    if (not path.is_relative_to(root) or evidence.get('identity_verified') is not True
            or aware(evidence['query_time']) != observed or sha(path) != evidence['sha256']):
        raise ValueError('retained result evidence checksum, identity or timestamp mismatch')
    if fixture['match_id'].startswith('kleague:'):
        from research_program.kleague_result import regulation
        regulation(result, evidence, path)
    elif evidence.get('score_extraction')=='first_two_halves':
        from research_program.regulation_halves import verify
        verify(result, evidence, path)
    else:
        _finished_espn_label(result, evidence, path)
    return result['home_goals'], result['away_goals']


def _outcomes(prediction, goals, line):
    settlement = None
    values = dict.fromkeys(TARGETS)
    if prediction is None or goals is None:
        return values, settlement
    home, away = goals
    score = f'{home}-{away}'
    wdl = 'home' if home > away else 'away' if home < away else 'draw'
    values.update(
        score_top1=score == prediction['primary'] if prediction['primary'] else None,
        score_top3=score in prediction['top3'] if prediction['top3'] else None,
        wdl=wdl == prediction['wdl'] if prediction['wdl'] else None,
    )
    if prediction['total_side'] is not None:
        settlement = settle_ou(home + away, line, prediction['total_side'])
        if settlement != 'PUSH':
            values['actual_ou'] = settlement == 'FULL_WIN'
    return values, settlement


def _rate(wins, n):
    return wins / n if n else None


def _wilson(wins, n):
    if not n:
        return None
    z = 1.959963984540054
    p = wins / n
    a = 1 + z * z / n
    center = (p + z * z / (2 * n)) / a
    half = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / a
    return [max(0., center - half), min(1., center + half)]


def _summarize(rows, role, settled, targets):
    settled_rows = [r for r in rows if r['result'] is not None]
    outcomes = Counter(r['settlements'][role] for r in settled_rows)
    result = {'total_settlements': {key: outcomes[key] for key in OUTCOMES}, 'metrics': {}}
    for metric, target in targets.items():
        scored = [r['outcomes'][role][metric] for r in settled_rows]
        wins = sum(v is True for v in scored)
        n = sum(v is not None for v in scored)
        # Unissued and abstained picks stay in the fixed population. A verified
        # push is independent of pick direction and never enters the rate.
        denominator = settled - (sum(r['common_line_push'] for r in settled_rows) if metric == 'actual_ou' else 0)
        result['metrics'][metric] = {
            'target': target, 'hits': wins, 'eligible': n,
            'eligible_rate': _rate(wins, n), 'population_coverage': _rate(n, len(rows)),
            'settled_coverage': _rate(n, settled),
            'resolved_gate_denominator': denominator, 'resolved_gate_rate': _rate(wins, denominator),
            'resolved_gate_wilson_95': _wilson(wins, denominator),
        }
    return result


def _paired(rows, metric):
    difference = Counter()
    for r in rows:
        candidate, baseline = (r['outcomes'][role][metric] for role in ROLES)
        if candidate is not None and baseline is not None:
            difference[int(candidate) - int(baseline)] += 1
    n = sum(difference.values())
    net = difference[1] - difference[-1]
    return {'eligible': n, 'gained': difference[1], 'lost': difference[-1], 'net_hits': net,
            'net_rate': _rate(net, n)}


def review(plan_record, captures, results, *, evidence_root=None):
    """Score the fixed cohort without substituting late picks or removing missing games."""
    plan = _plan(plan_record)
    now = _utcnow()
    registered = plan['fixtures']
    labels, predictions = {}, {}
    for capture_record in captures:
        capture_record = _first(capture_record, FORECAST)
        fixture, payload = capture_record['fixture'], capture_record['payload']
        mid = fixture['match_id']
        if (mid in predictions or capture_record['match_id'] != mid
                or payload['plan_sha256'] != plan_record['record_sha256']):
            raise ValueError('duplicate or unbound capture')
        captured_at = aware(capture_record['recorded_at'])
        if captured_at > now:
            raise ValueError('forecast capture timestamp is in the future')
        cutoff, line = _validate_fixture(plan_record, plan, fixture, captured_at)
        predictions[mid] = (line, {role: _selection(payload[role], plan[role], cutoff, captured_at, line)
                                   for role in ROLES})
    for result in results:
        mid = result['match_id']
        if mid not in registered or mid in labels:
            raise ValueError('duplicate or unregistered actual')
        labels[mid] = _actual(result, registered[mid], evidence_root, now)
    rows = []
    for mid in registered:
        line, picks = predictions.get(mid, (None, {}))
        goals = labels.get(mid)
        common_push = line is not None and goals is not None and settle_ou(sum(goals), line, 'over') == 'PUSH'
        outcomes, settlements = {}, {}
        for role in ROLES:
            outcomes[role], settlements[role] = _outcomes(picks.get(role), goals, line)
        rows.append({'match_id': mid, 'captured': mid in predictions, 'result': goals, 'actual_line': line,
                     'common_line_push': common_push, 'outcomes': outcomes, 'settlements': settlements})
    settled = sum(r['result'] is not None for r in rows)
    tracks = {role: _summarize(rows, role, settled, plan['targets']) for role in ROLES}
    metrics = tracks['candidate']['metrics']
    complete = settled == len(rows)
    meets_targets = complete and all(
        (r['resolved_gate_rate'] or 0.) >= r['target'] for r in metrics.values())
    statistically_supported = meets_targets and all(
        r['resolved_gate_wilson_95'][0] >= r['target'] for r in metrics.values())
    return {
        'schema': 'WCPP-KPI-REVIEW-1', 'plan_sha256': plan_record['record_sha256'],
        'population': len(rows), 'settled_population': settled,
        'pending_matches': [r['match_id'] for r in rows if r['result'] is None],
        'missing_first_captures': [r['match_id'] for r in rows if not r['captured']],
        'tracks': tracks, 'paired_vs_market_baseline': {metric: _paired(rows, metric) for metric in TARGETS},
        'targets_met_on_complete_population': meets_targets,
        'targets_supported_by_per_metric_wilson_95': statistically_supported,
        'confidence_interval_scope': 'NOMINAL_PER_METRIC_PER_MATCH; NO JOINT_OR_CLUSTER_CLAIM',
        'automatic_promotion': False, 'rows': rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    registering = sub.add_parser('register')
    registering.add_argument('--fixtures', type=Path, required=True)
    registering.add_argument('--candidate', type=Path, required=True)
    registering.add_argument('--market-baseline', type=Path, required=True)
    capturing = sub.add_parser('capture')
    capturing.add_argument('--plan', type=Path, required=True)
    capturing.add_argument('--fixture', type=Path, required=True)
    capturing.add_argument('--candidate', type=Path, required=True)
    capturing.add_argument('--market-baseline', type=Path, required=True)
    reviewing = sub.add_parser('review')
    reviewing.add_argument('--plan', type=Path, required=True)
    reviewing.add_argument('--captures', type=Path, nargs='*', default=[])
    reviewing.add_argument('--results', type=Path, required=True)
    reviewing.add_argument('--evidence-root', type=Path)
    for command in (registering, capturing, reviewing):
        command.add_argument('--out', type=Path, required=True)
    for command in (registering, capturing):
        command.add_argument('--freeze-root', default=DEFAULT_ROOT)
    args = parser.parse_args()
    if args.command == 'register':
        result = register(read(args.fixtures), read(args.candidate), read(args.market_baseline),
                          freeze_root=args.freeze_root)
    elif args.command == 'capture':
        result = capture(read(args.plan), read(args.fixture), read(args.candidate), read(args.market_baseline),
                         freeze_root=args.freeze_root)
    else:
        result = review(read(args.plan), [read(p) for p in args.captures], read(args.results),
                        evidence_root=args.evidence_root)
    write_new(args.out, result)


if __name__ == '__main__':
    main()
