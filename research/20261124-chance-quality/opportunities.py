"""Offline, outcome-free validation of native shot sequences and original team schedules."""
from collections import Counter
from datetime import datetime, timedelta
from hashlib import sha256
from math import isfinite
from pathlib import Path
import json
import re
from urllib.parse import parse_qs, urlsplit

import numpy as np
from scipy.stats import poisson


def clock(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.utcoffset() is None:
        raise ValueError('timezone_missing')
    return result


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def pair(row):
    return tuple(str(row[side]['id']) for side in ('home', 'away'))


def native_pair(raw):
    general = raw['general']
    ids = tuple(str(general[side]['id']) for side in ('homeTeam', 'awayTeam'))
    require(ids[0] != ids[1] and ids == tuple(str(v['id']) for v in raw['header']['teams']), 'team_identity')
    return ids


def check_source(receipt, raw_dir, kind, native_id, cutoff, indexed_at):
    """The completed index already contains the digest of each source. Never use local mtime."""
    url = urlsplit(receipt['url'])
    arg = 'id' if kind == 'teams' else 'matchId'
    expected = f'{kind}-{native_id}.json'
    require(url.scheme == 'https' and url.hostname == 'www.fotmob.com'
            and url.path == f'/api/data/{kind}' and parse_qs(url.query).get(arg) == [native_id]
            and str(receipt['query'][arg]) == native_id
            and Path(receipt['raw_file']).name == expected, 'source_identity')
    require(receipt['status'] == 200 and clock(receipt['query_time_utc']) <= indexed_at <= cutoff,
            'capture_outside_cutoff')
    raw = (raw_dir / expected).read_bytes()
    require(len(raw) == receipt['bytes'] and sha256(raw).hexdigest() == receipt['sha256'], 'raw_digest')
    return json.loads(raw)


def recent_three(calendar, team, target_id, target_ids, kickoff, cutoff):
    details = calendar['details']
    require(str(details['id']) == team and details['type'] == 'team' and details['gender'] == 'male',
            'calendar_identity')
    rows = calendar['fixtures']['allFixtures']['fixtures']
    target = [r for r in rows if str(r['id']) == target_id]
    require(len(target) == 1 and pair(target[0]) == target_ids
            and clock(target[0]['status']['utcTime']) == kickoff and not target[0]['status']['started'],
            'calendar_target')
    history = []
    for row in rows:
        status = row['status']
        when = clock(status['utcTime'])
        if not cutoff - timedelta(days=45) <= when < cutoff or not status.get('finished'):
            continue
        name = row['tournament']['name']
        if re.search(r'friendly|friendlies|youth|under.?\d+|\bu[- ]?\d{2}\b|\breserves?\b', name, re.I):
            continue
        require(team in pair(row) and pair(row)[0] != pair(row)[1], 'calendar_prior_identity')
        if not status.get('cancelled') and not status.get('awarded'):
            history.append(row)
    history.sort(key=lambda r: clock(r['status']['utcTime']), reverse=True)
    require(len({r['id'] for r in history}) == len(history) and
            len({clock(r['status']['utcTime']) for r in history}) == len(history), 'calendar_conflict')
    return history[:3]


def side_stat(raw, key):
    rows = raw['content']['stats']['Periods']['All']['stats']
    fields = [s['stats'] for g in rows for s in g.get('stats', [])
              if s.get('key') == key and s.get('stats') is not None]
    if not fields:
        return None
    require(all(v == fields[0] for v in fields) and len(fields[0]) == 2, 'conflicting_native_stat')
    return fields[0]


def shot_profile(raw, prior, captured):
    """A complete, FT90 non-penalty shot vector for both sides, including true zero shots."""
    general, status = raw['general'], raw['header']['status']
    ids = native_pair(raw)
    kickoff = clock(prior['status']['utcTime'])
    require(str(general['matchId']) == str(prior['id']) and ids == pair(prior) and
            kickoff == clock(general['matchTimeUTCDate']) == clock(status['utcTime']), 'prior_identity')
    require(general['gender'] == 'male' and general['finished'] and status['finished']
            and status.get('reason', {}).get('short') == prior['status'].get('reason', {}).get('short') == 'FT'
            and not status.get('cancelled') and not status.get('awarded') and
            not any((status.get('halfs') or {}).get(k) for k in ('firstExtraHalfStarted', 'secondExtraHalfStarted'))
            and kickoff + timedelta(minutes=90) < captured, 'no_completed_regulation')
    shots = (raw['content'].get('shotmap') or {}).get('shots')
    require(isinstance(shots, list) and general.get('coverageLevel') == 'xG', 'no_native_shotmap')
    totals = side_stat(raw, 'total_shots')
    require(totals is not None and all(type(v) is int and v >= 0 for v in totals), 'no_shot_denominator')
    seen, counts, probabilities = set(), Counter(), {k: [] for k in ids}
    for shot in shots:
        sid, team = str(shot.get('id') or ''), str(shot.get('teamId'))
        require(sid and sid not in seen and team in ids and shot.get('period') in ('FirstHalf', 'SecondHalf'),
                'shot_identity_or_period')
        seen.add(sid)
        require(type(shot.get('isOwnGoal')) is bool, 'unknown_own_goal')
        if shot['isOwnGoal']:
            continue
        counts[team] += 1
        situation = shot.get('situation')
        require(situation in {'RegularPlay','SetPiece','FreeKick','ThrowInSetPiece','FromCorner',
                              'FastBreak','IndividualPlay','Penalty'}, 'unknown_situation')
        if situation == 'Penalty':
            continue
        prob = shot.get('expectedGoals')
        require(type(prob) in (int, float) and isfinite(prob) and 0 <= prob <= 1, 'missing_shot_xg')
        probabilities[team].append(prob)
    require([counts[k] for k in ids] == totals, 'incomplete_shotmap')
    return probabilities


def distribution(vectors, *, at_mean=False):
    """Mix alternative previous games after calculating each game's distribution."""
    require(len(vectors) == 6, 'incomplete_history')
    pmfs = []
    for shots in vectors:
        if at_mean:
            mean = sum(shots)
            max_goals = max(1, int(poisson.ppf(1 - 1e-14, mean)))
            pmf = poisson.pmf(np.arange(max_goals + 1), mean)
        else:
            pmf = np.ones(1)
            for p in shots:
                pmf = np.convolve(pmf, [1-p, p])
        pmfs.append(pmf)
    result = np.zeros(max(map(len, pmfs)))
    for pmf in pmfs:
        result[:len(pmf)] += pmf / len(pmfs)
    return result / result.sum()


def matrix(home, away, *, at_mean=False):
    require(len(home) == len(away) == 3, 'both_recent_three_required')
    h = [attack for attack, _ in home] + [allowed for _, allowed in away]
    a = [attack for attack, _ in away] + [allowed for _, allowed in home]
    return np.outer(distribution(h, at_mean=at_mean), distribution(a, at_mean=at_mean))
