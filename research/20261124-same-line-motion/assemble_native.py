"""Combine original batch projections, deduplicate first issues and protect the R7 lock."""
import argparse
from collections import Counter
from datetime import timedelta, timezone
import hashlib
import json
from pathlib import Path
import tarfile

from competition_scope import policy
from research.analysis_freeze import aware, verify
from study import RULES, packed, save, write


BEIJING = timezone(timedelta(hours=8))


def espn_team(value):
    if value is None:
        return None
    s = str(value)
    return 'espn:' + s if s.isdecimal() else s


def aliases(row):
    values = {row['id']}
    for provider, number in (row.get('source_ids') or {}).items():
        if number and provider in ('espn', 'fotmob', 'kleague'):
            text = str(number).removeprefix(provider + ':')
            values.add(provider + ':' + text)
    home, away = espn_team(row.get('home_id')), espn_team(row.get('away_id'))
    if home and away and row.get('kickoff_at'):
        values.add((row['competition'], home, away, aware(row['kickoff_at'])))
    return values


def protected(archive):
    with archive.open('rb') as f:
        if hashlib.file_digest(f, 'sha256').hexdigest() != RULES['r7_sha256']:
            raise ValueError('R7 identity changed')
    keys, issues = set(), set()
    with tarfile.open(archive) as tar:
        for member in tar:
            if not member.isfile() or not member.name.endswith('.json'):
                continue
            record = json.load(tar.extractfile(member))
            if record.get('track') != 'research-prematch-continuation-20261123':
                continue
            verify(record)
            f = record['fixture']
            issues.add(record['match_id'])
            keys.update(aliases(dict(id=record['match_id'], competition=f['cohort'].split(':')[0],
                                     home_id=f['home'], away_id=f['away'], source_ids=f.get('source_ids'),
                                     kickoff_at=f['kickoff_at'])))
    if len(issues) != 192:
        raise ValueError('R7 population changed')
    return keys


def original_order(row):
    value = row.get('recorded_at') or row.get('cutoff_at') or row.get('kickoff_at')
    if value is None:
        return float('inf')
    return aware(value).timestamp()


def assemble(paths, r7, out):
    allowed = {r['code'] for r in policy()['competitions']}
    original_by_key, unique, batch_rows = {}, {}, Counter()
    earlier = sorted((json.loads(line) for path in paths for line in path.read_bytes().splitlines()),
                     key=lambda r: (original_order(r), r['id'], not bool(r.get('latest_h2h'))))
    for row in earlier:
        if {'score', 'result', 'home_goals', 'away_goals'} & row.keys() or row['provider'] != 'DraftKings':
            raise ValueError('unexpected target or bookmaker in input-only projection')
        keys = aliases(row)
        matches = {original_by_key[k] for k in keys if k in original_by_key}
        if len(matches) > 1:
            raise ValueError('conflicting cross-provider fixture identities')
        batch_rows[row.get('batch', 'input')] += 1
        mid = next(iter(matches)) if matches else row['id']
        if mid not in unique:
            row['home_id'], row['away_id'] = espn_team(row.get('home_id')), espn_team(row.get('away_id'))
            if row['competition'] not in allowed:
                row['excluded'] = 'outside_competition_scope'
            unique[mid] = row
        elif (not unique[mid].get('excluded') and not row.get('excluded') and
              row.get('freeze_sha256') == unique[mid].get('freeze_sha256') and
              row.get('latest_h2h') and unique[mid].get('latest_h2h') and
              row['latest_h2h'] != unique[mid]['latest_h2h']):
            raise ValueError('different current quotes assigned to one original freeze')
        if keys & r7:
            unique[mid]['excluded'] = 'excluded_R7'
        for key in keys:
            original_by_key[key] = mid

    split, start, stop = [aware(RULES[k]) for k in ('native_split', 'native_start', 'native_stop_exclusive')]
    for r in unique.values():
        if not r.get('cutoff_at') or not r.get('kickoff_at'):
            r['excluded'] = r.get('excluded') or 'missing_original_clock'
            r.setdefault('split', 'excluded')
            continue
        ct, ko = aware(r['cutoff_at']), aware(r['kickoff_at'])
        r['date'] = ko.astimezone(BEIJING).date().isoformat()
        r['split'] = 'train' if start <= ct < ko < split else 'test' if split <= ct < ko < stop else 'excluded'
        if r['split'] == 'excluded':
            r['excluded'] = r.get('excluded') or 'outside_registered_time_window'
        if not r.get('excluded') and r.get('latest_h2h'):
            if (not r.get('freeze_sha256') or not r.get('raw_sha256') or not r.get('home_id') or
                    not r.get('away_id') or r['home_id'] == r['away_id'] or not r.get('capture_at')
                    or aware(r['capture_at']) >= ct):
                raise ValueError('price without original identity and strictly predecision capture')
    ordered = sorted(unique.values(), key=lambda x: (x['date'], x['id']))
    write(out/'inputs.jsonl', b''.join(packed(r) for r in ordered))
    stats = dict(input_references=len(earlier), unique=len(ordered), duplicate_references=len(earlier)-len(ordered),
                 r7_verified_fixture_issues=192, raw_batch_references=batch_rows,
                 split_counts=Counter(r['split'] for r in ordered),
                 dispositions=Counter(r.get('excluded') or 'consider_market' for r in ordered))
    save(out/'coverage.json', stats)
    return stats


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--r7', required=True, type=Path)
    p.add_argument('--out', required=True, type=Path)
    p.add_argument('projections', nargs='+', type=Path)
    args = p.parse_args()
    print(json.dumps(assemble(args.projections, protected(args.r7), args.out), sort_keys=True))
