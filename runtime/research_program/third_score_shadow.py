"""Seal and grade a local third-score comparison from normal authored handoffs."""

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tarfile

import numpy as np

from research.analysis_freeze import aware, digest, save, verify
from research.display_delivery import DISPLAY_TRACK, delivery_request
from research.team_goal_analysis import summarize_prediction
from storage_lock import exclusive_lock
from .three_arm_model import top_three_scores
from .three_arm_review import _first_freeze, _results


PROTOCOL = Path(__file__).resolve().parents[2] / 'research/20261125-third-score-shadow/protocol.json'
REGISTRATION = 'third-score-shadow-registration'
BATCH = 'third-score-shadow-batch'
IDENTITY = ('match_id', 'competition', 'cohort', 'stage', 'home', 'away')
OUTSIDE = {'OUTSIDE_WINDOW', 'R7', 'ALREADY_REGISTERED'}


def _now():
    return datetime.now(timezone.utc)


def _read(path):
    return json.loads(path.read_bytes())


def _path(root, mid, track):
    return root / digest(dict(match_id=mid, stage='prematch', track=track)) / '000001.json'


def _recipe():
    return dict(protocol=_read(PROTOCOL), code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())


def _r7_identity(fixture):
    return digest([fixture.get('competition') or fixture['cohort'].split(':')[0],
                   fixture['home'], fixture['away'],
                   aware(fixture['kickoff_at']).astimezone(timezone.utc).isoformat()])


def _r7_population(path, spec):
    with path.open('rb') as file:
        if hashlib.file_digest(file, 'sha256').hexdigest() != spec['archive_sha256']:
            raise ValueError('R7 archive differs from the pinned original')
        file.seek(0)
        with tarfile.open(fileobj=file, mode='r:gz') as archive:
            matches = json.load(archive.extractfile('receipts.json'))['matches']
            if len(matches) != spec['fixture_count']:
                raise ValueError('unexpected R7 population')
            identities = []
            for mid, ref in matches.items():
                issue = verify(json.load(archive.extractfile(ref['file'])))
                if issue['match_id'] != mid or issue['record_sha256'] != ref['sha256']:
                    raise ValueError('R7 receipt differs from its original freeze')
                identities.append(_r7_identity(issue['fixture']))
    return dict(r7_ids=sorted(matches), r7_fixture_identities=sorted(set(identities)))


def _registration(state):
    recipe = _recipe()
    path = _path(state / 'registration', recipe['protocol']['id'], REGISTRATION)
    record = _first_freeze(path, REGISTRATION)
    if {key: record['payload'][key] for key in recipe} != recipe:
        raise ValueError('implementation or protocol differs from the original registration')
    return record


def register(state, r7_archive):
    recipe = _recipe()
    protocol = recipe['protocol']
    first = _path(state / 'registration', protocol['id'], REGISTRATION)
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    with exclusive_lock(state / '.registration.lock'):
        payload = dict(recipe, **_r7_population(r7_archive, protocol['r7']))
        if first.exists():
            record = _registration(state)
            if record['payload'] != payload:
                raise ValueError('registered exclusions differ')
            return record
        fixture = dict(match_id=protocol['id'], cutoff_at=_now().isoformat(),
                       kickoff_at=protocol['window_start'])
        save(payload, fixture, stage='prematch', track=REGISTRATION, root=state / 'registration',
             idempotency_key=protocol['id'])
        return _registration(state)


def _original(ref, track, final_sha=None):
    # A normal original handoff can refer to a later freeze if its finalized
    # roster already identifies that exact version, e.g. after a hidden draft.
    path = Path(ref['path'])
    record = verify(_read(path)) if final_sha else _first_freeze(path, track)
    if (record['record_sha256'] != ref['sha256'] or record['freeze_id'] != ref['freeze_id']
            or (record['track'], record['stage']) != (track, 'prematch')
            or record['match_id'] != record['fixture']['match_id']
            or (record['record_sha256'] != final_sha if final_sha else record['previous_sha256'] is not None)):
        raise ValueError('original freeze receipt differs')
    return record


def _match(left, right):
    return (all(left[key] == right[key] for key in IDENTITY)
            and aware(left['kickoff_at']) == aware(right['kickoff_at']))


def _pair(handoff, fixture, registered, at, rule):
    author = _original(handoff['receipt'], DISPLAY_TRACK, fixture.get('author_freeze_sha256'))
    delivery_request(author)
    af, ap = author['fixture'], author['payload']
    if (not _match(fixture, af) or handoff['fixture'] != af or handoff['payload'] != ap
            or not aware(registered['recorded_at']) <= aware(author['recorded_at']) <= at):
        raise ValueError('author handoff does not match the roster or registration')
    if ap['authored_display']['status'] == 'ABSTAIN':
        return dict(status='AUTHOR_ABSTAIN')
    ref = ap.get('current_test_reference')
    if ref is None or not Path(ref['path']).is_file():
        return dict(status='PAIR_MISSING')
    pair = _original(ref, 'current-test-pair')
    pf = pair['fixture']
    if (not _match(af, pf) or pf.get('score_basis', 'regulation_90') != 'regulation_90'
            or pf.get('neutral') != af.get('neutral')
            or not aware(pf['cutoff_at']) <= aware(pair['recorded_at']) <= aware(af['cutoff_at'])):
        raise ValueError('paired model identity or decision cutoff differs')
    models = {key: pair['payload'][key] for key in ('current_model', 'test_model')}
    summaries = {key: summarize_prediction(value) for key, value in models.items()}
    if any(s['status'] != 'AVAILABLE' for s in summaries.values()):
        return dict(status='PAIR_UNAVAILABLE')
    if any(not value.get('version') or not value.get('matrix_hash') for value in models.values()):
        raise ValueError('frozen model version and matrix identity required')
    if len({tuple(s['matrix_shape']) for s in summaries.values()}) != 1:
        raise ValueError('paired score matrix supports differ')

    baseline = ap['displayed_scores']
    ranked = top_three_scores(np.asarray(models['test_model']['matrix'], dtype=float))
    alternate = next((score for score in ranked[:rule['rank_limit']]
                      if score in rule['eligible_scores'] and score not in baseline), None)
    candidate = list(baseline)
    if alternate is not None:
        candidate[rule['replace_one_based_slot'] - 1] = alternate
    return dict(status='LOCKED', baseline=baseline, candidate=candidate, applicable=alternate is not None,
                author_cutoff_at=af['cutoff_at'],
                author_freeze_sha256=author['record_sha256'], author_freeze_id=author['freeze_id'],
                pair_freeze_sha256=pair['record_sha256'], pair_freeze_id=pair['freeze_id'],
                pair_cutoff_at=pf['cutoff_at'], test_top3=ranked,
                models={key: dict(version=value['version'], matrix_hash=value['matrix_hash'])
                        for key, value in models.items()})


def _batches(state, registration):
    for path in sorted((state / 'batches').glob('*/000001.json')):
        record = _first_freeze(path, BATCH)
        if record['payload']['registration_sha256'] != registration['record_sha256']:
            raise ValueError('batch belongs to another registration')
        yield record


def _capture(handoff, fixture, registration, at, rule):
    try:
        return _pair(_read(handoff), fixture, registration, at, rule)
    except FileNotFoundError:
        return dict(status='AUTHOR_MISSING')
    except (TypeError, KeyError, ValueError):
        return dict(status='EVIDENCE_CONFLICT')


def lock_batch(state, roster_file, authored_dir):
    registration = _registration(state)
    protocol = registration['payload']['protocol']
    roster = _read(roster_file)
    fixtures = roster['fixtures']
    ids = {f['match_id'] for f in fixtures}
    names = [mid.rsplit(':', 1)[-1] for mid in ids]
    if (not roster['batch_id'] or not ids or len(ids) != len(fixtures)
            or any(not name or Path(name).name != name for name in names)):
        raise ValueError('stable batch ID and complete unique original fixture list required')
    mid = protocol['id'] + ':' + roster['batch_id']
    root = state / 'batches'
    with exclusive_lock(state / '.batches.lock'):
        path = _path(root, mid, BATCH)
        if path.exists():
            record = _first_freeze(path, BATCH)
            if (record['payload']['roster_sha256'] != digest(roster)
                    or record['payload']['registration_sha256'] != registration['record_sha256']):
                raise ValueError('existing batch differs from the original roster or registration')
            return record['payload']['rows'], record
        at = _now()
        seen = {r['fixture']['match_id'] for b in _batches(state, registration)
                for r in b['payload']['rows'] if r['status'] not in OUTSIDE}
        excluded = set(registration['payload']['r7_ids'])
        excluded_identities = set(registration['payload']['r7_fixture_identities'])
        rows, deadlines = [], []
        for fixture in fixtures:
            fixture = {k: fixture[k] for k in (*IDENTITY, 'kickoff_at', 'author_freeze_sha256') if k in fixture}
            event = fixture['match_id']
            start = aware(fixture['kickoff_at'])
            if event in excluded or _r7_identity(fixture) in excluded_identities:
                row = dict(status='R7')
            elif not aware(protocol['window_start']) <= start < aware(protocol['window_end_exclusive']):
                row = dict(status='OUTSIDE_WINDOW')
            elif event in seen:
                row = dict(status='ALREADY_REGISTERED')
            elif start <= at:
                row = dict(status='ALREADY_STARTED')
            else:
                name = event.rsplit(':', 1)[-1] + '.json'
                row = _capture(authored_dir / name, fixture, registration, at, protocol['rule'])
                deadlines.append(start)
            rows.append(dict(fixture=fixture) | row)
        if not deadlines:
            return rows, None
        payload = dict(registration_sha256=registration['record_sha256'], roster_sha256=digest(roster), rows=rows)
        # The batch contains final author inputs only. Use a sidecar freeze before its first pending deadline.
        batch = dict(match_id=mid, cutoff_at=at.isoformat(), kickoff_at=min(deadlines).isoformat())
        save(payload, batch, stage='prematch', track=BATCH, root=root, idempotency_key=registration['record_sha256'])
        return rows, _first_freeze(path, BATCH)


def _settle(row, locked_at, result, kickoff, at):
    if kickoff is None:
        return dict(row, settlement='ACTUAL_KICKOFF_MISSING')
    f = row['fixture']
    if any(kickoff[k] != f[k] for k in ('match_id', 'competition', 'home', 'away')):
        raise ValueError('actual kickoff belongs to a different fixture')
    started = aware(kickoff['actual_kickoff_at'])
    if started <= locked_at:
        return dict(row, settlement='LOCK_AT_OR_AFTER_ACTUAL_START')
    if result is None or started > at:
        return dict(row, settlement='REGULATION_RESULT_PENDING')
    if aware(result['source']['captured_at']) < started + timedelta(minutes=90):
        return dict(row, settlement='RESULT_BEFORE_REGULATION_COULD_FINISH')
    score = f"{result['home']}-{result['away']}"
    return dict(row, settlement='SETTLED', baseline_hit=score in row['baseline'],
                candidate_hit=score in row['candidate'])


def _summary(rows):
    statuses = Counter(r['status'] for r in rows)
    n = sum(v for k, v in statuses.items() if k not in OUTSIDE)
    locked = [r for r in rows if r['status'] == 'LOCKED']
    applicable = sum(r['applicable'] for r in locked)
    settled = [r for r in locked if r.get('settlement') == 'SETTLED']
    base = sum(r['baseline_hit'] for r in settled)
    candidate = sum(r['candidate_hit'] for r in settled)
    return dict(registered_n=n, statuses=dict(statuses), locked_n=len(locked),
                applicable_n=applicable, applicable_of_locked=applicable / len(locked) if locked else None,
                applicable_of_registered=applicable / n if n else None,
                paired_settled_n=len(settled), changed_settled_n=sum(r['applicable'] for r in settled),
                settlement_statuses=dict(Counter(r['settlement'] for r in locked if 'settlement' in r)),
                baseline_top3=dict(hits=base, rate=base / len(settled) if settled else None),
                candidate_top3=dict(hits=candidate, rate=candidate / len(settled) if settled else None),
                gained=sum(r['candidate_hit'] and not r['baseline_hit'] for r in settled),
                lost=sum(r['baseline_hit'] and not r['candidate_hit'] for r in settled), net=candidate - base)


def review(state, results_state=None, kickoffs=None):
    registration = _registration(state)
    rows = [(row, aware(batch['recorded_at'])) for batch in _batches(state, registration)
            for row in batch['payload']['rows']]
    fixtures = {r['fixture']['match_id']: r['fixture'] for r, _ in rows if r['status'] == 'LOCKED'}
    at = min(_now(), aware(registration['payload']['protocol']['review_cutoff']))
    starts = _read(kickoffs) if kickoffs is not None else {}
    results, issues = {}, {}
    if results_state is not None and (results_state / 'normalized-results.jsonl').is_file():
        results, issues = _results(results_state.resolve(), fixtures, at)
    settled = []
    for row, locked_at in rows:
        if row['status'] == 'LOCKED':
            mid = row['fixture']['match_id']
            row = _settle(row, locked_at, results.get(mid), starts.get(mid), at)
        settled.append(row)
    return dict(registration_sha256=registration['record_sha256'], **_summary(settled),
                result_evidence_issues_n=len(issues))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', required=True, type=Path, help='Local shadow state directory')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('register').add_argument('--r7-archive', required=True, type=Path)
    lock = sub.add_parser('lock')
    lock.add_argument('--roster', required=True, type=Path)
    lock.add_argument('--authored-dir', required=True, type=Path)
    grade = sub.add_parser('review')
    grade.add_argument('--results-state', type=Path)
    grade.add_argument('--kickoffs', type=Path)
    args = parser.parse_args(argv)
    if args.command == 'register':
        record = register(args.state, args.r7_archive)
        out = dict(registration_sha256=record['record_sha256'], recorded_at=record['recorded_at'],
                   excluded_r7_n=len(record['payload']['r7_ids']))
    elif args.command == 'lock':
        rows, record = lock_batch(args.state, args.roster, args.authored_dir)
        out = dict(batch_record_sha256=record['record_sha256'] if record else None, **_summary(rows))
    else:
        out = review(args.state, args.results_state, args.kickoffs)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
