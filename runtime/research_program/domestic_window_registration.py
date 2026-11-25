"""Register complete consecutive display windows from a reconciled European league calendar."""
import argparse
import gzip
from hashlib import sha256
import json
from pathlib import Path

from publication import atomic_json
from research.analysis_freeze import aware, DEFAULT_ROOT, digest
from storage_lock import exclusive_lock
from . import goal_state_forward as ledger
from .goal_state_study import sha
from .kpi_validation import _first
from .r8_score_study import read

PROTOCOL = Path(__file__).resolve().parents[2] / 'research/20261125-europe-display-windows/protocol.json'
PROTOCOL_SHA = '5586c477082449beddc4c65d4929eeea8c7110dffdaf94d9b5d9eb0147c977da'
TRACKS = ('current-test-pair', 'comprehensive')


def slates(code, fixtures, spec):
    if code not in spec['competitions'] or len(fixtures) != len({f['match_id'] for f in fixtures}):
        raise ValueError('unexpected competition or duplicate fixture')
    windows = spec['windows']
    if len(windows) != 2 or aware(windows[0]['until']) != aware(windows[1]['from']):
        raise ValueError('two consecutive complete windows required')
    batches, minima = {}, {}
    for w in windows:
        rows = sorted((f for f in fixtures if aware(w['from']) <= aware(f['kickoff_at']) < aware(w['until'])),
                      key=lambda f:(aware(f['kickoff_at']), f['match_id']))
        for f in rows:
            if (f['cohort'] != code or f['stage'] != spec['stage'] or f['home'] == f['away']
                    or any(not f[k].startswith('espn:') or not f[k][5:].isdigit() for k in ('match_id', 'home', 'away'))):
                raise ValueError('invalid domestic regular-season fixture')
        n = len(rows)
        batches[w['name']] = [f['match_id'] for f in rows]
        minima[w['name']] = dict(registered=n, first=n, ordered_three=n, wdl=n, strict_lines=(8*n + 9)//10,
                                 nonpush=(7*n + 9)//10, selected_seats=3)
    if sum(map(len, batches.values())) != len(fixtures):
        raise ValueError('fixtures outside the registered windows')
    return batches, minima


def native_model_inputs(registry, code):
    active = read(registry)['models'][code]
    models = {}
    for arm, prefix in (('Current', ''), ('Test', 'test_')):
        path = Path(active[prefix+'artifact_path'])
        fingerprint = sha(path)
        if fingerprint != active[prefix+'artifact_sha256']:
            raise ValueError('active weight differs from registry: ' + code)
        artifact = read(path)
        models[arm] = dict(sha256=fingerprint, artifact_file=path.name, version=artifact.get('version'))
    return dict(registry_sha256=sha(registry), native_models=models)


def prematch_exceptions(code, fixtures, proof):
    """Keep resumed or otherwise unavailable matches in their calendar slots."""
    remaining = {f['match_id']: f for f in fixtures}
    exceptions = {}
    for entry in read(proof)['captures']:
        if entry['competition'] != code or entry['source_provider'] != 'ESPN':
            continue
        path = (proof.parent / entry['compressed_path']).resolve()
        if not path.is_relative_to(proof.parent.resolve()) or sha(path) != entry['compressed_sha256']:
            raise ValueError('calendar source file differs')
        body = gzip.decompress(path.read_bytes())
        if sha256(body).hexdigest() != entry['body_sha256']:
            raise ValueError('calendar source body differs')
        for event in json.loads(body)['events']:
            mid = 'espn:' + event['id']
            if mid not in remaining:
                continue
            fixture = remaining.pop(mid)
            game, = event['competitions']
            status = event['status']
            sides = {s['homeAway']: 'espn:' + s['team']['id'] for s in game['competitors']}
            if (game['status'] != status or game['id'] != event['id'] or not game['timeValid']
                    or sides != {k: fixture[k] for k in ('home', 'away')}
                    or aware(event['date']) != aware(fixture['kickoff_at']) or aware(game['date']) != aware(event['date'])):
                raise ValueError('calendar match, kickoff or original status differs: ' + mid)
            kind = status['type']
            if kind['name'] != 'STATUS_SCHEDULED' or kind['state'] != 'pre' or status.get('clock') != 0:
                exceptions[mid] = dict(status=kind['name'], game_clock_seconds=status.get('clock'),
                                       period=status.get('period'), source_sha256=entry['body_sha256'],
                                       observed_at=entry['observed_response_end_utc'])
    if remaining:
        raise ValueError('missing source statuses: ' + ', '.join(sorted(remaining)))
    return exceptions


def earlier_slots(fixtures, root, dest):
    dest.mkdir(parents=True, exist_ok=True)
    slots = {}
    for fixture in fixtures:
        mid = fixture['match_id']
        slots[mid] = {}
        for track in TRACKS:
            path = root / digest(dict(match_id=mid, stage='prematch', track=track)) / '000001.json'
            item = None
            if path.is_file():
                row = _first(read(path), track)
                if (any(row['fixture'][k] != fixture[k] for k in ('match_id', 'cohort', 'stage', 'home', 'away'))
                        or aware(row['fixture']['kickoff_at']) != aware(fixture['kickoff_at'])):
                    raise ValueError('earlier slot needs schedule reconciliation: ' + mid)
                filename = mid[5:]+'-'+track+'.json'
                target = dest / filename
                if target.exists() and target.read_bytes() != path.read_bytes():
                    raise ValueError('earlier first changed: ' + mid)
                if not target.exists():
                    with target.open('xb') as f:
                        f.write(path.read_bytes())
                item = dict(sha256=row['record_sha256'], recorded_at=row['recorded_at'], file=filename)
            slots[mid][track] = item
    return slots


def register(code, catalog, calendar_proof, goals, scope, registry, out, *, freeze_root=DEFAULT_ROOT):
    if sha(PROTOCOL) != PROTOCOL_SHA:
        raise ValueError('competition windows differ from the registered protocol')
    spec = read(PROTOCOL)
    if code not in {c['code'] for c in read(scope)['competitions'] if not c['requires_phase_identity']}:
        raise ValueError('unrecognized domestic regular-season competition')
    fixtures = read(catalog)[code]
    batches, coverage = slates(code, fixtures, spec)
    root = Path(freeze_root)
    root.mkdir(parents=True, exist_ok=True)
    track = code.replace('.', '-') + '-comprehensive-windows-20261125'
    folder = out / code
    folder.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(root / ('.' + track + '.lock')):
        index = folder / 'registration/plan.json'
        reservation = root / ('.' + track + '-first-plan.json')
        if reservation.is_file():
            identity = read(reservation)
            if (identity['protocol_sha256'] != PROTOCOL_SHA or identity['calendar_sha256'] != sha(catalog)
                    or identity['calendar_proof_sha256'] != sha(calendar_proof) or identity['slates'] != batches):
                raise ValueError('a different calendar cannot replace the original registration')
            dest = folder / 'earlier'
            dest.mkdir(exist_ok=True)
            for mid, tracks in identity['earlier_firsts'].items():
                for original_track, entry in tracks.items():
                    if entry:
                        path = root / digest(dict(match_id=mid, stage='prematch', track=original_track)) / '000001.json'
                        row = _first(read(path), original_track)
                        if row['record_sha256'] != entry['sha256']:
                            raise ValueError('recovered earlier issue differs: ' + mid)
                        target = dest / entry['file']
                        if target.exists() and target.read_bytes() != path.read_bytes():
                            raise ValueError('an earlier publication file changed: ' + mid)
                        if not target.exists():
                            with target.open('xb') as f:
                                f.write(path.read_bytes())
        else:
            roadmap = read(goals)['research_roadmap_20261124']['stages']
            model_inputs = native_model_inputs(registry, code)
            earlier = earlier_slots(fixtures, root, folder / 'earlier')
            exceptions = prematch_exceptions(code, fixtures, calendar_proof)
            identity = dict(version=spec['version'], competitions=[code], protocol=spec, protocol_sha256=PROTOCOL_SHA,
                            scope_sha256=sha(scope), goals_sha256=sha(goals), roadmap=roadmap, slates=batches, minima=coverage,
                            model_inputs=model_inputs, earlier_firsts=earlier, unavailable_new_firsts=exceptions, calendar_sha256=sha(catalog),
                            calendar_proof_sha256=sha(calendar_proof))
            if index.is_file() and ledger._read(index.parent, read(index))['payload']['identity'] != identity:
                raise ValueError('existing first has a different calendar or version')
            atomic_json(reservation, identity)
        ref = ledger.register(fixtures, identity, track, index.parent, freeze_root=root)
        if index.is_file() and read(index) != ref:
            raise ValueError('published registration changed')
        atomic_json(index, ref)
        return ref


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--code', required=True)
    for name in ('catalog', 'calendar-proof', 'goals', 'scope', 'registry', 'out'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    print(register(args.code, args.catalog, args.calendar_proof, args.goals, args.scope, args.registry, args.out))
