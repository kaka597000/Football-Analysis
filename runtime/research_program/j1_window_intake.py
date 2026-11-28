"""Prepare verified J1 result evidence for the original registered window."""

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import gzip
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from research.analysis_freeze import aware, digest
from storage_lock import exclusive_lock
from . import display_window_review as display
from . import goal_state_forward as ledger
from . import j1_checkpoint_review as market_checkpoints
from . import jleague_kickoff_proof as kickoff
from . import nov25_first_review as evidence
from .goal_state_study import write_new


def read(path):
    return json.loads(path.read_bytes())


def acquisitions(root, at):
    """Read the legacy archive and any completed immutable capture batches."""
    root = root.resolve()
    espn, official = [], defaultdict(list)
    for folder in [root, *sorted((root / 'captures').glob('*'))]:
        if not folder.is_dir() or folder.is_symlink():
            continue
        def qualify(ref, key):
            if folder == root or not ref.get(key):
                return ref
            path = evidence.file_in(folder, ref[key])
            return dict(ref, **{key: path.relative_to(root).as_posix()})

        espn_log = folder / 'espn.jsonl'
        official_log = folder / 'official-captures.json'
        if espn_log.is_file():
            espn.extend((line, qualify(ref, 'path')) for line, ref in evidence.dated_log(espn_log, at)[0])
        if official_log.is_file():
            for mid, refs in read(official_log).items():
                official[mid].extend(qualify(ref, 'raw_file') for ref in refs)
    return espn, official


def check(fixtures, bindings, espn, official, root, at):
    root = Path(root).resolve()
    if set(bindings) != set(fixtures) or not set(official) <= set(fixtures):
        raise ValueError('input scope differs from the original J1 window')
    status, labelled, _, failed = evidence.inspect(root, fixtures, espn, at)
    starts, results, rows = {}, [], {}
    for mid, fixture in fixtures.items():
        refs = [ref for ref in official.get(mid, []) if aware(ref['captured_at_utc']) <= at]
        pages = [evidence.official_card(kickoff.raw(root, ref, at), ref, fixture, binding=bindings[mid])
                 for ref in refs if ref['http_status'] == 200]
        pages.sort(key=lambda r: aware(r['source']['captured_at_utc']))
        espn_row, result = status[mid], labelled.get(mid)
        checked = evidence.official_crosscheck(pages, espn_row, result)
        rejected = set(checked['issues'])
        rejected.update(issue for issue in espn_row['issues'] if issue.startswith('conflicting'))
        if checked['kickoff_interval'] and checked['official_state'] not in ('live', 'final'):
            rejected.add('later official page no longer establishes actual play')
        start = None
        if not rejected:
            if espn_row['kickoff']:
                exact = espn_row['kickoff']
                start = dict(earliest=exact['at'], latest=exact['at'], source=exact['source'], precision='second')
            elif interval := checked['kickoff_interval']:
                start = dict(earliest=interval['start_inclusive'], latest=interval['end_exclusive'],
                             source=checked['kickoff_source'], precision='minute')
        if start:
            starts[mid] = start
        ready = bool(start and result)
        if ready:
            results.append(result)
        verdict = ('pending_conflict' if rejected else 'verified_regulation' if ready
                   else 'awaiting_actual_kickoff' if result else 'awaiting_regulation_result')
        rows[mid] = dict(status=verdict, actual_start=start, certified_espn_result=result,
                         official_state=checked['official_state'], official_final=checked['official_final'],
                         official_result_agreement=checked['verdict'],
                         official_schedule_minute_overlap=any(row['ko_matches_schedule'] for row in pages),
                         conflicts=sorted(rejected), espn_issues=espn_row['issues'],
                         verification_failures=sum(ref['http_status'] != 200 for ref in refs))
    report = dict(registered=len(fixtures), actual_starts=len(starts), certified_espn_finals=len(labelled),
                  settled=len(results), pending=len(fixtures)-len(results),
                  match_evidence=rows, failed_espn_requests=failed)
    return report, results, starts


def review_tracks(folders, fixtures, results, starts, root):
    result_by_match = {row['match_id']: row for row in results}
    reports = {}
    for folder in folders:
        index = read(folder / 'receipts.json')
        registration = ledger._read(folder, index['registration'])
        batch = registration['payload']
        if (set(batch['fixtures']) != set(fixtures) or set(index['matches']) != set(fixtures)
                or batch['track'] in reports):
            raise ValueError('research issues must cover the exact registered window once')
        usable, late, choices = [], [], {}
        for mid, ref in index['matches'].items():
            record = ledger._read(folder, ref)
            evidence.same_fixture(fixtures[mid], record['fixture'])
            payload = dict(record['payload'])
            payload.pop('registration_sha256')
            if digest(payload) != batch['identity']['forecast_sha256'][mid]:
                raise ValueError('the research list differs from its pregame registration')
            choices[mid] = payload['forecasts']
            if mid in result_by_match:
                if aware(record['recorded_at']) < aware(starts[mid]['earliest']):
                    usable.append(result_by_match[mid])
                else:
                    late.append(mid)
        verified = ledger.review(folder, usable, root, by_competition=True)['jpn.1']
        actuals = {row['match_id']: (row['home_goals'], row['away_goals']) for row in usable}
        selected = {arm: evidence.seats(ids, {mid: row[arm] for mid, row in choices.items()}, actuals)
                    for arm, ids in batch['identity'].get('selected', {}).items()}
        reports[batch['track']] = dict(verified, freeze_id=registration['freeze_id'],
                                       late_or_overlap=late, selected_seats=selected)
    return reports


def _copy_espn(root, staging, path, expected, target):
    body = evidence.file_in(root, path).read_bytes()
    if sha256(body).hexdigest() != expected:
        raise ValueError('ESPN evidence changed during export: ' + path)
    (staging / target).write_bytes(body)
    return target


def _copy_official(root, staging, ref, name, at):
    target = 'official/' + name + '.html'
    (staging / target).write_bytes(kickoff.raw(root, ref, at))
    return dict(ref, raw_file=target)


def publish(out, root, report, results, *, window, fixtures, starts, binding_root, packet, captures, at):
    """Publish a result state only after the complete new evidence set has been written."""
    out = Path(out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(str(out) + '.lock'):
        if out.exists():
            raise FileExistsError(out)
        with TemporaryDirectory(prefix='j1-intake-', dir=out.parent) as scratch:
            staging = Path(scratch) / 'review'
            (staging / 'raw').mkdir(parents=True)
            (staging / 'official').mkdir()
            state_rows, exported = [], []
            for row in results:
                proof = row['evidence']
                name = row['match_id'].replace(':', '-')
                raw_path = _copy_espn(root, staging, proof['file'], proof['sha256'], 'raw/' + name + '.json')
                exported.append(dict(row, evidence=dict(proof, file=raw_path)))
                metadata = dict(path=raw_path, source_url=proof['url'], sha256=proof['sha256'],
                                captured_at=proof['query_time'])
                core = {key: row[key] for key in ('match_id', 'cohort', 'home', 'away', 'stage', 'kickoff_at',
                                                 'home_goals', 'away_goals', 'event_status', 'score_basis')}
                state_rows.append(dict(core, known_at=proof['query_time'], source_url=proof['url'],
                                       raw_reference=metadata))
            write_new(staging / 'verified-results.json', exported)
            write_new(staging / 'review.json', report)
            exact = {}
            for mid, start in starts.items():
                if start['precision'] != 'second':
                    continue
                source = start['source']
                target = 'raw/start-' + mid.replace(':', '-') + '.json'
                fixture = fixtures[mid]
                exact[mid] = dict(match_id=mid, competition='jpn.1', home=fixture['home'], away=fixture['away'],
                                  identity_verified=True, observed_at=source['captured_at_utc'],
                                  actual_kickoff_at=start['earliest'], source_url=source['url'],
                                  source_sha256=source['sha256'],
                                  raw_file=_copy_espn(root, staging, source['path'], source['sha256'], target))
            write_new(staging / 'actual-kickoffs.json', {window: exact})
            write_new(staging / 'jleague-kickoffs.json', dict(binding_record=packet['binding_record'], captures={
                mid: [_copy_official(root, staging, ref, 'live-' + mid.replace(':', '-') + '-' + str(i), at)
                      for i, ref in enumerate(refs) if ref['http_status'] == 200 and aware(ref['captured_at_utc']) <= at]
                for mid, refs in captures.items()}))
            # The binding SHA covers the original relative paths. Copy rather than rewrite them.
            for entry in packet['binding_record']['payload']['bindings'].values():
                source = entry['source']
                path = (staging / source['raw_file']).resolve()
                if not path.is_relative_to(staging):
                    raise ValueError('binding path outside the new state')
                path.parent.mkdir(parents=True, exist_ok=True)
                body = kickoff.raw(binding_root, source, at)
                if path.suffix == '.gz':
                    body = gzip.compress(body, mtime=0)
                with path.open('xb') as stream:
                    stream.write(body)
            with (staging / 'normalized-results.jsonl').open('x', encoding='utf-8') as stream:
                for row in state_rows:
                    stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n')
            staging.rename(out)


def intake(registration, window, packet_path, root, out, issues, at, *, binding_root=None, checkpoint_folders=()):
    plan, universe, slates, _ = display._registered(read(registration))
    fixtures = {mid: universe[mid] for mid in slates[window]}
    packet = read(packet_path)
    if packet['captures']:
        raise ValueError('use the original pregame binding packet and keep live evidence in the intake archive')
    binding_root = (binding_root or packet_path.parent).resolve()
    all_bindings = kickoff.bindings(packet, plan, universe, binding_root, at)
    bindings = {mid: all_bindings[mid] for mid in fixtures}
    espn, official = acquisitions(root, at)
    report, results, starts = check(fixtures, bindings, espn, official, root, at)
    report.update(window=window, at=at.isoformat(), registration=plan['freeze_id'],
                  binding_freeze=packet['binding_record']['freeze_id'],
                  research=review_tracks(issues, fixtures, results, starts, root.resolve()))
    if checkpoint_folders:
        report['market_checkpoints'] = market_checkpoints.review(checkpoint_folders, plan, fixtures, results, starts, at)
    publish(out, root.resolve(), report, results, window=window, fixtures=fixtures, starts=starts,
            binding_root=binding_root, packet=packet, captures=official, at=at)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ('registration', 'binding', 'intake', 'out'):
        parser.add_argument('--' + arg, type=Path, required=True)
    parser.add_argument('--window', required=True)
    parser.add_argument('--binding-root', type=Path, help='base for the pregame response paths in the signed binding')
    parser.add_argument('--issued', action='append', type=Path, default=[])
    parser.add_argument('--market-checkpoint', action='append', type=Path, default=[],
                        help='self-contained scheduled capture with a separately frozen benchmark index')
    parser.add_argument('--at')
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    at = aware(args.at) if args.at else now
    if at > now:
        raise ValueError('review time cannot be in the future')
    intake(args.registration, args.window, args.binding, args.intake, args.out, args.issued, at,
           binding_root=args.binding_root, checkpoint_folders=args.market_checkpoint)
