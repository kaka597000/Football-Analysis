"""Rebuild the previously studied Nov 20–21 shadow panel from its retained pregame bytes."""

import argparse
from datetime import timedelta, timezone
from hashlib import sha256
from importlib.util import find_spec
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

from history import count, make_state, score
from research.analysis_freeze import aware

HERE = Path(__file__).resolve().parent
PROTOCOL = HERE / 'protocol.json'
PR69 = '54ec6d70fdff7145c1889ffe6071b2188663bef2'
PR70 = '6e3906fb24e487eec736e73ff304975d0ac35e93'
BEIJING = timezone(timedelta(hours=8))
START = aware('2026-11-20T00:00:00+08:00')
END = aware('2026-11-22T00:00:00+08:00')


def enc(obj):
    return json.dumps(obj, ensure_ascii=True, separators=(',', ':'), sort_keys=True,
                      allow_nan=False).encode()


def digest(data):
    return sha256(data).hexdigest()


def pinned_auditors():
    """Use clean, explicit #69/#70 exports; the model and grader live in this study."""
    spec = find_spec('clocked_evidence')
    if spec is None or not spec.origin:
        raise ValueError('add the #70 research exporter and #69 audit modules/runtime to PYTHONPATH')
    script = Path(spec.origin).resolve()
    repo = script.parents[2]
    version = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    if version != PR70 or script != repo / 'research/20261124-halftime-balance/clocked_evidence.py':
        raise ValueError('wrong #70 audit source')
    subprocess.run(['git', '-C', str(repo), 'diff', '--quiet', 'HEAD', '--', 'runtime',
                    'research/20261124-halftime-balance'], check=True)
    import clocked_evidence
    if clocked_evidence.PROTOCOL['read_only_audit_source'] != PR69:
        raise ValueError('wrong #69 audit pin')
    clocked_evidence.verify_auditor()
    import archive
    import replay
    return archive, replay, clocked_evidence


def originals(archives, audit):
    """Project only original signed pregame records. The joined outcomes file is never opened."""
    manifest = json.load(archives.assets.extractfile('ASSET-MANIFEST.json'))
    rows = []
    for mid, path in sorted(manifest['wcpp_frozen_records'].items()):
        record = audit.verify(json.loads(audit.checked(archives.assets.extractfile(path).read(),
                                                      manifest['files'][path])))
        f = record['fixture']
        if not START <= aware(f['kickoff_at']) < END:
            continue
        if not (record['match_id'] == mid == f['match_id'] and
                path == 'issued/' + record['freeze_id'] + '.json'):
            raise ValueError('wrong original freeze identity')
        row = {k: f[k] for k in ('match_id', 'cohort', 'stage', 'home', 'away', 'kickoff_at',
                                'cutoff_at', 'score_basis')}
        row['freeze_reference'] = {'sha256': record['record_sha256'], 'freeze_id': record['freeze_id']}
        rows.append(row)
    if len(rows) != 33 or len({audit.identity(f) for f in rows}) != len(rows):
        raise ValueError('original Nov 20–21 roster changed')
    return rows


def previous_games(season, origin):
    rows = []
    for row in season:
        if row['day'] >= origin - timedelta(days=1):
            continue
        raw = row['raw']
        goals = score(raw)
        if goals is None:
            continue
        sot = tuple(count(raw.get(k)) for k in ('HST', 'AST'))
        rows.append(dict(date=row['day'], home=row['home'], away=row['away'], goals=goals, sot=sot))
    return rows


def clocked_inputs(archives, fixtures, aliases, audit):
    states = {}
    out = []
    for r in fixtures:
        league = r['cohort']
        cutoff = aware(r['cutoff_at'])
        origin = cutoff.astimezone(BEIJING).date()
        row = dict(id=r['match_id'], league=league, date=aware(r['kickoff_at']).astimezone(BEIJING).date().isoformat(),
                   home=r['home'], away=r['away'], kickoff_at=r['kickoff_at'], cutoff_at=r['cutoff_at'],
                   freeze_sha=r['freeze_reference']['sha256'], freeze_id=r['freeze_reference']['freeze_id'],
                   rates=None, for_against=None, line=None, status='missing_original')
        out.append(row)
        if r['match_id'] in archives.r7_ids or audit.identity(r) in archives.r7_keys:
            row['status'] = 'excluded_R7'
            continue
        if r['stage'] != 'regular':
            row['status'] = 'outside_approved_stage'
            continue
        fixture = archives.original(r)
        if fixture is None:
            continue
        # Only the certified two-sided total is needed. Missing 1X2 never removes a strength forecast.
        row['line'] = archives.quote_line(fixture)
        if league not in audit.CODES:
            row['status'] = 'no_captured_league_history'
            continue
        season, captured = archives.season(league, 2026)
        if captured is None or captured >= cutoff:
            row['status'] = 'no_history_available_at_cutoff'
            continue
        key = league, origin
        if key not in states:
            prior = previous_games(season, origin)
            clubs = {r[role] for r in prior for role in ('home', 'away')}
            states[key] = make_state(prior, origin), clubs
        state, clubs = states[key]
        names = [audit.resolve_club({fixture[side+'_name'], *aliases[league,fixture[side]]}, clubs)
                 for side in ('home', 'away')]
        row.update(source_available_at=captured.isoformat(), source_clubs=names)
        if None in names or names[0] == names[1]:
            row['status'] = 'unresolved_club'
            continue
        # Both panels reuse the same strength/day blocks, latest-six selector and drought features.
        row.update(state.inputs(*names))
        if row['rates'] is None:
            row['status'] = 'missing_strength_history'
        elif row['for_against'] is None:
            row['status'] = 'anchor_only'
        else:
            row['status'] = 'eligible'
    return out


def verify_prediction_lock(lock_path, input_path, forecast_path, fixtures):
    if any(p is None for p in (lock_path, input_path, forecast_path)):
        raise ValueError('supply the previously sealed clocked inputs, forecasts and lock')
    lock = json.loads(lock_path.read_bytes())
    raw_inputs, raw_forecasts = input_path.read_bytes(), forecast_path.read_bytes()
    ids = sorted(r['match_id'] for r in fixtures)
    if (lock.get('panel') != 'clocked' or lock.get('n') != len(ids)
        or not lock.get('fit_sha256') or lock.get('protocol_sha256') != digest(PROTOCOL.read_bytes())
        or lock.get('inputs_sha256') != digest(raw_inputs) or lock.get('forecast_sha256') != digest(raw_forecasts)
        or lock.get('roster_sha256') != digest(enc(ids))):
        raise ValueError('clocked forecast lock, inputs or roster mismatch')
    rows = [json.loads(s) for s in raw_inputs.splitlines()]
    sealed = [json.loads(s) for s in raw_forecasts.splitlines()]
    if sorted(r['id'] for r in rows) != ids or sorted(r['id'] for r in sealed) != ids:
        raise ValueError('missing or duplicate sealed fixture')
    expected = {r['match_id']: r for r in fixtures}
    for row in rows:
        original = expected[row['id']]
        if (row['freeze_sha'] != original['freeze_reference']['sha256']
            or row['freeze_id'] != original['freeze_reference']['freeze_id']
            or row['home'] != original['home'] or row['away'] != original['away']
            or row['league'] != original['cohort'] or aware(row['kickoff_at']) != aware(original['kickoff_at'])
            or aware(row['cutoff_at']) != aware(original['cutoff_at'])):
            raise ValueError('sealed fixture does not match original first issue')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('inputs', 'labels'))
    parser.add_argument('--data', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--sealed-inputs', type=Path)
    parser.add_argument('--forecasts', type=Path)
    parser.add_argument('--forecast-lock', type=Path)
    args = parser.parse_args()
    audit, evidence, clocks = pinned_auditors()
    archives = audit.Archives(args.data)
    fixtures = originals(archives, audit)
    with TemporaryDirectory() as tmp:
        if args.mode == 'inputs':
            aliases = evidence.prior_aliases(archives, min(aware(r['cutoff_at']) for r in fixtures), Path(tmp))
            records = clocked_inputs(archives, fixtures, aliases, audit)
        else:
            verify_prediction_lock(args.forecast_lock, args.sealed_inputs, args.forecasts, fixtures)
            records = []
            r7 = {r['match_id'] for r in fixtures if r['match_id'] in archives.r7_ids
                  or audit.identity(r) in archives.r7_keys}
            pending = [f for f in fixtures if f['match_id'] not in r7]
            for r in clocks.labels(archives, pending, Path(tmp), audit, evidence):
                records.append({'id':r['id'], 'score':r['score'], 'status':r['reason']})
            records.extend({'id':mid,'score':None,'status':'excluded_R7'} for mid in r7)
    data = b''.join(enc(r)+b'\n' for r in sorted(records,key=lambda r:r['id']))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    evidence.put(args.out, data)
    print(json.dumps({'mode':args.mode,'rows':len(records),'sha256':digest(data)}))


if __name__ == '__main__':
    main()
