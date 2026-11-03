"""Source-backed V2 observations and cutoff-safe feature snapshots; no fitting.

Captured evidence establishes availability no earlier than its actual capture.
An asserted publication/first-known timestamp never backdates a downloaded file.
Hashes and time checks validate supplied evidence, not a provider's truthfulness.
"""
import argparse
import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

FIELDS = {'xg', 'xga', 'npxg', 'shots', 'shots_on_target',
          'player_minutes', 'player_availability', 'market_quote'}
SENSITIVE = {'key', 'api_key', 'apikey', 'api-key', 'token', 'access_token',
             'password', 'authorization'}


def aware(value):
    if not isinstance(value, str):
        raise ValueError('explicit timestamp required')
    d = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if d.tzinfo is None:
        raise ValueError('timezone required')
    return d


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def public_url(value):
    u = urlparse(value)
    if (u.scheme != 'https' or not u.hostname or u.username or u.password
            or u.fragment or any(k.lower() in SENSITIVE for k, _ in parse_qsl(u.query))):
        raise ValueError('public credential-free HTTPS source required')


def number(value, *, integer=False):
    if (type(value) not in (int, float) or not math.isfinite(value) or value < 0
            or integer and type(value) is not int):
        raise ValueError('finite nonnegative football value required')


def safe_content(value):
    if isinstance(value, dict):
        if any(k.lower() in SENSITIVE for k in value):
            raise ValueError('credential fields prohibited')
        for item in value.values():
            safe_content(item)
    elif isinstance(value, list):
        for item in value:
            safe_content(item)
    elif isinstance(value, str) and any(s in value.lower() for s in ('bearer ', 'api_key=', 'apikey=', 'access_token=')):
        raise ValueError('credential-bearing text prohibited')


def validate(row, evidence_root, *, now=None):
    now = now or datetime.now(timezone.utc)
    safe_content(row)
    for k in ('provider', 'cohort', 'match_id', 'entity_id', 'field'):
        if not isinstance(row.get(k), str) or not row[k].strip():
            raise ValueError('explicit provider/competition/match/entity/field required')
    field = row['field']
    if field not in FIELDS or row.get('period') != 'regulation_90':
        raise ValueError('supported field and 90-minute scope required')
    observed = aware(row['observed_at'])
    event = aware(row['event_kickoff_at'])
    if observed > now:
        raise ValueError('future capture')
    # A first-known claim may be retained, but cannot override capture availability.
    for k in ('first_known_at', 'source_published_at'):
        if row.get(k) is not None and aware(row[k]) > observed:
            raise ValueError('source time later than capture')
    evidence = row['evidence']
    public_url(evidence['url'])
    if aware(evidence['query_time']) != observed:
        raise ValueError('observation/capture time mismatch')
    if evidence.get('identity_verified') is not True:
        raise ValueError('match and team identity must be reviewed')
    base = Path(evidence_root).resolve()
    relative = Path(evidence['file'])
    path = (base / relative).resolve()
    if relative.is_absolute() or not path.is_relative_to(base) or not path.is_file():
        raise ValueError('evidence must be a retained file inside evidence root')
    if hashlib.sha256(path.read_bytes()).hexdigest() != evidence['sha256']:
        raise ValueError('evidence checksum mismatch')
    value = row['value']
    if field in {'xg', 'xga', 'npxg', 'shots', 'shots_on_target', 'player_minutes'}:
        number(value, integer=field in {'shots', 'shots_on_target'})
        if row.get('event_status') != 'FT' or observed < event + timedelta(minutes=90):
            raise ValueError('completed historical performance required')
    if field in {'xg', 'xga', 'npxg'} and row.get('entity_side') not in {'home', 'away'}:
        raise ValueError('explicit home/away entity identity required')
    if field == 'xga':
        derived = row.get('derived_from', {})
        opposite = 'away' if row['entity_side'] == 'home' else 'home'
        if (derived.get('provider') != row['provider'] or derived.get('match_id') != row['match_id']
                or derived.get('field') != 'xg' or derived.get('side') != opposite):
            raise ValueError('xGA requires opposite-side xG from same provider and match')
    if field in {'player_minutes', 'player_availability'} and not row.get('player_id'):
        raise ValueError('player identity required')
    if field == 'player_availability':
        if value.get('status') not in {'reported_out', 'doubtful', 'available', 'unknown', 'confirmed_out'}:
            raise ValueError('explicit availability classification required')
        if value['status'] == 'confirmed_out' and evidence.get('source_tier') != 'L1':
            raise ValueError('confirmed absence requires reviewed official evidence')
    if field == 'market_quote':
        quoted = aware(value['quoted_at'])
        if quoted > observed or quoted >= event:
            raise ValueError('pre-match source quote time required')
        market = value.get('market')
        odds = value.get('odds') or {}
        if not value.get('bookmaker') or market not in {'h2h', 'totals', 'asian_handicap'}:
            raise ValueError('explicit bookmaker and market required')
        sides = {'home', 'draw', 'away'} if market == 'h2h' else {'over', 'under'} if market == 'totals' else {'home', 'away'}
        if set(odds) != sides:
            raise ValueError('complete same-market prices required')
        for v in odds.values():
            number(v)
            if v <= 1:
                raise ValueError('decimal odds must exceed one')
        if market != 'h2h':
            line = value.get('line')
            if type(line) not in (int, float) or not math.isfinite(line) or line * 4 != round(line * 4):
                raise ValueError('explicit quarter-goal market line required')
            if market == 'totals' and line < 0:
                raise ValueError('negative total line')
    return observed


def snapshot(rows, target, evidence_root, *, provider, last_n=3, lookback_days=90,
             market_max_age_seconds=60, availability_max_age_seconds=21600, now=None):
    now = now or datetime.now(timezone.utc)
    cutoff, kickoff = aware(target['cutoff_at']), aware(target['kickoff_at'])
    if not cutoff <= now < kickoff or not target.get('cohort') or not target.get('match_id'):
        raise ValueError('actual future fixture and current/past cutoff required')
    entities = {target['home_id'], target['away_id']}
    if len(entities) != 2 or any(not isinstance(x, str) or not x for x in entities):
        raise ValueError('two distinct explicit team identities required')
    ages = (market_max_age_seconds, availability_max_age_seconds)
    if (not isinstance(provider, str) or not provider.strip()
            or type(last_n) is not int or last_n < 1 or type(lookback_days) is not int
            or lookback_days < 1 or any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in ages)):
        raise ValueError('valid predeclared snapshot windows required')
    ids, latest, excluded = set(), {}, Counter()
    target_ids = {target['match_id'], *target.get('provider_match_ids', [])}
    for row in rows:
        observed = validate(row, evidence_root, now=now)
        rid = digest(row)
        if rid in ids:
            raise ValueError('duplicate observation; do not count duplicate feeds as evidence')
        ids.add(rid)
        if row['provider'] != provider or row['cohort'] != target['cohort']:
            excluded['provider_or_cohort_mismatch'] += 1
            continue
        if row['entity_id'] not in entities:
            excluded['other_entity'] += 1
            continue
        if observed > cutoff:
            excluded['captured_after_cutoff'] += 1
            continue
        event = aware(row['event_kickoff_at'])
        current = row['field'] in {'market_quote', 'player_availability'}
        if current:
            if row['match_id'] not in target_ids or event != kickoff:
                excluded['different_target_match'] += 1
                continue
            age = (cutoff - (aware(row['value']['quoted_at']) if row['field'] == 'market_quote' else observed)).total_seconds()
            limit = market_max_age_seconds if row['field'] == 'market_quote' else availability_max_age_seconds
            if age > limit:
                excluded['stale_current_evidence'] += 1
                continue
        elif row['match_id'] in target_ids or not kickoff - timedelta(days=lookback_days) <= event < cutoff:
            excluded['not_prior_history_in_window'] += 1
            continue
        market_key = tuple(row['value'].get(k) for k in ('bookmaker', 'market', 'line')) if row['field'] == 'market_quote' else ()
        key = (row['match_id'], row['entity_id'], row['field'], row.get('player_id'), row['period'], market_key)
        prior = latest.get(key)
        if prior and observed == aware(prior['observed_at']) and rid != digest(prior):
            raise ValueError('conflicting same-time observations; preserve for review')
        if prior is None or observed > aware(prior['observed_at']):
            latest[key] = row
    features = {}
    retained = []
    for entity in sorted(entities):
        fields = {}
        for field in sorted(FIELDS):
            group = [r for r in latest.values() if r['entity_id'] == entity and r['field'] == field]
            group.sort(key=lambda r: (aware(r['event_kickoff_at']), r['match_id']), reverse=True)
            if field not in {'market_quote', 'player_availability', 'player_minutes'}:
                group = group[:last_n]
            if not group:
                continue
            fields[field] = {'n': len(group), 'observation_sha256': [digest(r) for r in group]}
            if field not in {'market_quote', 'player_availability', 'player_minutes'}:
                fields[field]['mean'] = sum(r['value'] for r in group) / len(group)
                fields[field]['requested_n'] = last_n
            retained.extend(group)
        features[entity] = fields
    result = {'schema': 'WCPP-V2-FEATURE-SNAPSHOT-1', 'target': target,
        'provider': provider, 'created_at': now.isoformat(), 'features': features,
        'observations': retained, 'excluded': dict(excluded),
        'policy': {'last_n': last_n, 'lookback_days': lookback_days,
                   'market_max_age_seconds': market_max_age_seconds,
                   'availability_max_age_seconds': availability_max_age_seconds},
        'claims': {'source_first_known_time_inferred': False,
                   'historical_asof_certified': False, 'new_model_trained': False,
                   'active_model_changed': False},
        'notice': 'Capture/hash/identity checks use supplied evidence. No publication time inferred; '
                  'freshly downloaded history is available only after capture. Small windows, missing '
                  'players and provider differences remain explicit. No prediction or learned impact.'}
    result['snapshot_sha256'] = digest(result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--observations', required=True)
    p.add_argument('--target', required=True)
    p.add_argument('--evidence-root', required=True)
    p.add_argument('--provider', required=True)
    p.add_argument('--out', required=True)
    a = p.parse_args()
    path = Path(a.out)
    if path.exists():
        raise FileExistsError(path)
    result = snapshot(json.loads(Path(a.observations).read_text()),
        json.loads(Path(a.target).read_text()), a.evidence_root, provider=a.provider)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as f:
        json.dump(result, f, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({'snapshot_sha256': result['snapshot_sha256'],
                     'observations': len(result['observations']), 'excluded': result['excluded']}))


if __name__ == '__main__':
    main()
