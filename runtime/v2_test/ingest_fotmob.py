"""Import retained FotMob captures into V2 observations, never backdate history.

Capture manifests require reviewed provider match/team/competition identities.
No network requests, source modification, imputed xG or automatic model training.
"""
import argparse
import hashlib
import json
from pathlib import Path

try:
    from .data_contract import validate, number
except ImportError:
    from data_contract import validate, number


def import_capture(capture, evidence_root, *, now=None):
    root = Path(evidence_root).resolve()
    relative = Path(capture['file'])
    path = (root / relative).resolve()
    if relative.is_absolute() or not path.is_relative_to(root):
        raise ValueError('capture path outside evidence root')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != capture['sha256']:
        raise ValueError('capture file changed')
    x = json.loads(raw)
    general, header = x['general'], x['header']
    teams = header['teams']
    if (str(general['matchId']) != str(capture['expected_match_id'])
            or len(teams) != 2
            or [str(t['id']) for t in teams] != [str(capture['expected_home_id']), str(capture['expected_away_id'])]
            or str(general['leagueId']) != str(capture['expected_league_id'])
            or general.get('gender') != 'male' or capture.get('identity_verified') is not True):
        raise ValueError('reviewed match/team/competition identity mismatch')
    status = header['status']
    if (status.get('finished') is not True or status.get('cancelled') or status.get('awarded')
            or status.get('reason', {}).get('short') != 'FT'
            or status.get('whoLostOnPenalties')
            or any(status.get('halfs', {}).get(k) for k in ('firstExtraHalfStarted', 'secondExtraHalfStarted'))):
        raise ValueError('unambiguous regulation-90 completed match required')
    candidates = []
    for section in x.get('content', {}).get('stats', {}).get('Periods', {}).get('All', {}).get('stats', []):
        for row in section.get('stats', []):
            if row.get('key') == 'expected_goals' or row.get('title') == 'Expected goals (xG)':
                values = row.get('stats', [])
                if len(values) == 2 and all(v is not None for v in values):
                    if any(type(v) is bool for v in values):
                        raise ValueError('boolean is not an xG value')
                    candidates.append(tuple(map(float, values)))
    if not candidates:
        raise ValueError('no full-match xG; missing is not zero')
    if len(set(candidates)) != 1:
        raise ValueError('conflicting full-match xG summaries')
    values = candidates[0]
    shots = x.get('content', {}).get('shotmap', {}).get('shots', [])
    check = 'UNAVAILABLE'
    if shots:
        relevant = [s for s in shots if not s.get('isOwnGoal')]
        complete = all(s.get('expectedGoals') is not None and
                       str(s.get('teamId')) in {str(t['id']) for t in teams} for s in relevant)
        check = 'INCOMPLETE' if not complete else 'AGREE_WITHIN_DISPLAY_ROUNDING'
        if complete:
            for shot in relevant:
                number(shot['expectedGoals'])
            for team, displayed in zip(teams, values):
                total = sum(float(s['expectedGoals']) for s in relevant if str(s['teamId']) == str(team['id']))
                if abs(total - displayed) > .02:
                    raise ValueError('shot-map and summary xG conflict; preserve for review')
    mid = 'fotmob:' + str(capture['expected_match_id'])
    evidence = {'url': capture['url'], 'query_time': capture['query_time'],
        'file': capture['file'], 'sha256': capture['sha256'],
        'identity_verified': True, 'source_tier': 'L2'}
    result = []
    for side, team, own, opponent in zip(('home', 'away'), teams, values, reversed(values)):
        for field, value in (('xg', own), ('xga', opponent)):
            row = {'provider': 'FotMob', 'cohort': capture['cohort'], 'match_id': mid,
                'entity_id': 'fotmob:' + str(team['id']), 'entity_side': side,
                'field': field, 'value': value, 'period': 'regulation_90', 'event_status': 'FT',
                'event_kickoff_at': status['utcTime'], 'observed_at': capture['query_time'],
                'first_known_at': None, 'source_published_at': None, 'evidence': evidence,
                'capture_basis': 'retained_response; not source first-publication time',
                'shotmap_crosscheck': check}
            if field == 'xga':
                row['derived_from'] = {'provider': 'FotMob', 'match_id': mid, 'field': 'xg',
                    'side': 'away' if side == 'home' else 'home'}
            validate(row, evidence_root, now=now)
            result.append(row)
    return result


def ingest(captures, evidence_root, *, now=None):
    observations, quarantined, seen = [], [], set()
    for i, capture in enumerate(captures):
        key = (capture.get('expected_match_id'), capture.get('query_time'))
        if key in seen:
            raise ValueError('duplicate capture manifest entry')
        seen.add(key)
        try:
            observations.extend(import_capture(capture, evidence_root, now=now))
        except (ValueError, KeyError, TypeError, OSError, AttributeError) as error:
            # Preserve raw files; do not echo paths, URLs, source text or secret values.
            quarantined.append({'manifest_index': i, 'reason': type(error).__name__ + ': capture failed validation'})
    return {'schema': 'WCPP-V2-OBSERVATION-IMPORT-1', 'captures_supplied': len(captures),
        'observations': observations, 'quarantined': quarantined,
        'historical_asof_certified': False, 'active_model_changed': False,
        'notice': 'Quarantine remains in the denominator; inspect retained inputs. '
                  'Captured history is not certified historical out-of-fold evidence.'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--captures', required=True)
    p.add_argument('--evidence-root', required=True)
    p.add_argument('--out', required=True)
    a = p.parse_args()
    path = Path(a.out)
    if path.exists():
        raise FileExistsError(path)
    result = ingest(json.loads(Path(a.captures).read_text()), a.evidence_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as f:
        json.dump(result, f, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({'captures': result['captures_supplied'],
                     'observations': len(result['observations']), 'quarantined': len(result['quarantined'])}))


if __name__ == '__main__':
    main()
