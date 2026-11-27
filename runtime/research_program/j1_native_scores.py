"""Grade the original Current and Test score selections on admissible J1 results."""

from collections import defaultdict
from datetime import timedelta
from zoneinfo import ZoneInfo

from research.analysis_freeze import aware, digest, verify
from .paired_kpi_inference import compare


ROLES = ('current', 'test')
METRICS = ('score_top1', 'score_top3')
BEIJING = ZoneInfo('Asia/Shanghai')


def _picks(pair, model):
    fixture = pair['fixture']
    matrix = model.get('matrix')
    if matrix is None:
        status = model.get('status', '')
        if (not isinstance(status, str) or not status.startswith(('ABSTAIN', 'UNAVAILABLE'))
                or model.get('primary') or model.get('top3')):
            raise ValueError('missing native matrix requires an explicit unavailable forecast')
        return {}
    if (model['input'] != fixture or digest(matrix) != model['matrix_hash']
            or not aware(fixture['cutoff_at']) <= aware(model['generated_at']) <= aware(pair['recorded_at'])):
        raise ValueError('original native score matrix or its source chronology differs')
    primary = model.get('primary')
    scores = model.get('top3')
    if scores:
        scores = [entry['score'] if isinstance(entry, dict) else entry for entry in scores]
    return dict(primary=primary, top3=scores or None)


def _pair_forecasts(pair, fixture):
    pair = verify(pair)
    source = pair['fixture']
    if (pair['stage'] != 'prematch' or pair['track'] != 'current-test-pair'
            or pair['revision'] != 1 or pair['previous_sha256'] is not None
            or pair['evaluation_slot'] != 'FIRST_PREMATCH'
            or any(source[key] != fixture[key] for key in ('match_id', 'cohort', 'home', 'away'))
            or source['stage'] not in ('regular', 'Japanese J1 League')
            or aware(source['kickoff_at']) != aware(fixture['kickoff_at'])
            or aware(pair['recorded_at']) >= aware(fixture['kickoff_at'])):
        raise ValueError('native model evidence differs from its original J1 pair')
    models = {role: pair['payload'][role+'_model'] for role in ROLES}
    versions = tuple(models[role].get('version') or 'UNSPECIFIED' for role in ROLES)
    return versions, {role: _picks(pair, model) for role, model in models.items()}


def _actual(pair, result, start, at):
    if result is None:
        return None, 'AWAITING_REGULATION_RESULT'
    if start is None:
        return None, 'AWAITING_VERIFIED_KICKOFF'
    if 'actual_kickoff_at' in start:
        earliest = latest = aware(start['actual_kickoff_at'])
    else:
        interval = start['actual_kickoff_interval']
        earliest, latest = (aware(interval[key]) for key in ('start_inclusive', 'end_exclusive'))
    if not aware(pair['recorded_at']) < earliest <= latest <= at:
        return None, 'UNVERIFIED_PREMATCH_PAIR'
    if not latest + timedelta(minutes=90) <= aware(result['observed_at']) <= at:
        return None, 'UNVERIFIED_REGULATION_DURATION'
    return (result['home_goals'], result['away_goals']), 'SETTLED'


def review(fixtures, pairs, results, starts, at):
    """Use caller-verified actual kickoffs and regulation evidence for both native tracks."""
    if not pairs.keys() <= fixtures.keys():
        raise ValueError('native pair outside the original registered window')
    groups = defaultdict(list)
    rows = {}
    bound = 0
    for mid, fixture in fixtures.items():
        if mid not in pairs:
            rows[mid] = dict(status='ORIGINAL_PAIR_MISSING')
            continue
        pair = pairs[mid]
        if aware(pair['recorded_at']) > at:
            rows[mid] = dict(status='PAIR_AFTER_REVIEW_CUTOFF')
            continue
        versions, forecasts = _pair_forecasts(pair, fixture)
        bound += 1
        actual, status = _actual(pair, results.get(mid), starts.get(mid), at)
        day = aware(fixture['kickoff_at']).astimezone(BEIJING).date().isoformat()
        groups[versions].append(dict(id=mid, cohort=fixture['cohort'], date=day,
                                     actual=actual, line=None, forecast=forecasts))
        score = '-'.join(map(str, actual)) if actual is not None else None
        selections = {}
        for role, choices in forecasts.items():
            primary, three = choices.get('primary'), choices.get('top3')
            selections[role] = dict(primary=primary, top3=three,
                                    first_hit=score == primary if score and primary else None,
                                    three_hit=score in three if score and three else None)
        rows[mid] = dict(status=status, pair_freeze_id=pair['freeze_id'], actual=score, forecasts=selections)
    by_version = []
    for versions, group in sorted(groups.items()):
        paired = compare(group, baseline='current', candidate='test')
        scores = {role: {metric: paired['tracks'][role]['metrics'][metric] for metric in METRICS}
                  for role in ROLES}
        by_version.append(dict(versions=dict(zip(ROLES, versions, strict=True)),
                               registered=paired['population'], settled=paired['settled_population'],
                               scores=scores, paired={metric: paired['paired'][metric] for metric in METRICS}))
    return dict(registered=len(fixtures), bound_original_pairs=bound,
                settled=sum(row['status'] == 'SETTLED' for row in rows.values()),
                by_version=by_version, per_match=rows)
