"""Build a replay input from saved ESPN captures, never from assumed dates."""
import argparse
from collections import Counter, defaultdict
import gzip
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from rolling_state import CONFIG, digest, fixture, stamp
from automation.trainer import _verify_raw_result


def read_raw(root, reference):
    path = (root/reference['path']).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('raw path outside source root')
    with (gzip.open(path, 'rb') if path.suffix == '.gz' else path.open('rb')) as h:
        raw = h.read(20_000_001)
    if len(raw) > 20_000_000 or hashlib.sha256(raw).hexdigest() != reference['sha256']:
        raise ValueError('raw hash mismatch or excessive size')
    return json.loads(raw)


def prepare(root, *, cohorts, start, end, evaluation_at, batch_time_utc='00:00:00'):
    """Observed capture scope with an independent daily UTC clock.

    Late pre-match captures stay abstained, not excluded. Historical source
    captures can seed state only from their actual capture/import time onward.
    """
    root = Path(root)
    if not cohorts or not set(cohorts) <= {'aus.1', 'jpn.1'}:
        raise ValueError('capture adapter currently supports only aus.1 and jpn.1 league cohorts')
    requests = [json.loads(s) for s in (root/'requests.jsonl').read_text().splitlines()]
    snapshots = defaultdict(list)
    for request in requests:
        url = urlsplit(request['source_url'])
        if (request.get('status') != 'OK' or url.hostname != 'site.api.espn.com'
                or stamp(request['captured_at']) > stamp(evaluation_at)):
            continue
        competition = next((c for c in cohorts if url.path ==
            '/apis/site/v2/sports/soccer/'+c+'/scoreboard'), None)
        if competition is None:
            continue
        if stamp(request['captured_at']) != stamp(request['raw']['captured_at']):
            raise ValueError('scoreboard capture time mismatch')
        raw = read_raw(root, request['raw'])
        for event in raw.get('events', []):
            if len(event.get('competitions', [])) != 1:
                continue
            match = event['competitions'][0]
            kickoff = stamp(match['date']); day = kickoff.date().isoformat()
            if not start <= day <= end:
                continue
            teams = {t.get('homeAway'): t for t in match['competitors']}
            if set(teams) != {'home', 'away'} or str(event['id']) != str(match['id']):
                raise ValueError('ambiguous scoreboard fixture')
            row = dict(match_id='espn:'+str(event['id']), cohort=competition,
                competition=competition, date=day, kickoff_at=kickoff.isoformat(),
                home='espn:'+str(teams['home']['team']['id']),
                away='espn:'+str(teams['away']['team']['id']),
                neutral=match.get('neutralSite'), stage='regular',
                season=str(event.get('season', {}).get('year', 'UNKNOWN')),
                fixture_evidence={'verified': True, 'captured_at': request['captured_at'],
                    'source_url': request['source_url'], 'sha256': request['raw']['sha256']})
            snapshots[row['match_id']].append(row)
    first = []
    for mid, rows in snapshots.items():
        keys = ('home', 'away', 'kickoff_at', 'neutral', 'competition')
        if len({digest({k: r[k] for k in keys}) for r in rows}) != 1:
            raise ValueError('fixture identity changed in capture history: '+mid)
        first.append(min(rows, key=lambda r: stamp(r['fixture_evidence']['captured_at'])))
    cutoffs = {}
    for day in sorted({r['date'] for r in first}):
        cutoff = stamp(day+'T'+batch_time_utc+'+00:00')
        if cutoff.date().isoformat() != day:
            raise ValueError('batch clock must lie within the target UTC day')
        cutoffs[day] = cutoff.isoformat()
    normalized = [json.loads(s) for s in (root/'normalized-results.jsonl').read_text().splitlines()]
    results, rejected = [], []
    for source in normalized:
        if source.get('cohort') not in cohorts:
            continue
        try:
            cache = {}; status = _verify_raw_result(source, root, cache)
            if status != 'VERIFIED_RAW_REGULATION_90':
                raise ValueError(status)
            ref = source['raw_reference']; known = stamp(source['known_at'])
            if known < stamp(ref['captured_at']):
                raise ValueError('result known_at precedes capture')
            raw = next(iter(cache.values())); row = fixture(source)
            row['kickoff_at'] = stamp(raw['header']['competitions'][0]['date']).isoformat()
            row.update(home_goals=source['home_goals'], away_goals=source['away_goals'],
                result_available_at=known.isoformat(), score_basis='regulation_90',
                result_evidence={'status': status, 'captured_at': ref['captured_at'],
                    'sha256': ref['sha256'], 'source_url': source['source_url']})
            results.append(row)
        except (KeyError, OSError, ValueError) as exc:
            rejected.append({'match_id': source.get('match_id'), 'reason': str(exc)})
    first.sort(key=lambda r: (r['date'], r['match_id']))
    payload = {'fixtures': first, 'results': results,
        'origin_at': start+'T00:00:00+00:00', 'evaluation_at': evaluation_at,
        'day_cutoffs': cutoffs, 'config': CONFIG}
    audit = {'fixture_scope': len(first), 'fixture_ids': [r['match_id'] for r in first],
        'result_snapshots_verified': len(results), 'result_snapshots_rejected': rejected,
        'cohorts': list(cohorts), 'date_start': start, 'date_end': end,
        'cutoff_policy': 'Fixed daily UTC clock: '+batch_time_utc+'; independent of future fixture captures.',
        'fixture_source': 'Saved raw scoreboard captures, independent of result table.',
        'scope_status': 'OBSERVED_CAPTURE_SCOPE_NOT_FULL_OFFICIAL_SCHEDULE_CERTIFICATION',
        'coverage_before_cutoff': dict(Counter('AVAILABLE' if stamp(r['fixture_evidence']['captured_at'])
            <= stamp(cutoffs[r['date']]) else 'LATE_CAPTURE' for r in first)),
        'source_files': {name: hashlib.sha256((root/name).read_bytes()).hexdigest()
            for name in ('requests.jsonl', 'normalized-results.jsonl')},
        'input_hash': digest(payload), 'prospective': False}
    return payload, audit


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state-dir', required=True); p.add_argument('--cohorts', nargs='+', required=True)
    p.add_argument('--start', required=True); p.add_argument('--end', required=True)
    p.add_argument('--evaluation-at', required=True); p.add_argument('--out', required=True)
    p.add_argument('--batch-time-utc', default='00:00:00'); a = p.parse_args()
    payload, audit = prepare(a.state_dir, cohorts=a.cohorts, start=a.start, end=a.end,
                            evaluation_at=a.evaluation_at, batch_time_utc=a.batch_time_utc)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=False)
    for name, value in [('input.json', payload), ('input-audit.json', audit)]:
        with (out/name).open('x') as h:
            json.dump(value, h, indent=2, allow_nan=False)
    print(json.dumps(audit, indent=2))


if __name__ == '__main__':
    main()
