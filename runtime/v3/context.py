"""Source-backed pre-match context. Unknown inputs are never zero-filled."""
import math
from datetime import timedelta
from urllib.parse import urlparse
from model import aware

VERSION = 'WCPP-CLOUD-CHALLENGER-0.3'
OBJECTIVES = ('title', 'qualification', 'relegation', 'cup', 'midtable')
BLOCKS = ('strength', 'motivation', 'schedule', 'availability', 'tactics', 'integrity')

def number(value, name, minimum=0, maximum=None, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(name + ': finite number required')
    if value < minimum or (maximum is not None and value > maximum) or (integer and int(value) != value):
        raise ValueError(name + ': outside supported range')
    return float(value)

def source(e, cutoff, field, official=False):
    if not isinstance(e, dict) or urlparse(e.get('source_url', '')).scheme != 'https' or not urlparse(e.get('source_url', '')).netloc:
        raise ValueError(field + ': source required')
    if e.get('source_tier') not in ('L1', 'L2') or (official and e['source_tier'] != 'L1'):
        raise ValueError(field + ': insufficient source tier')
    if field not in e.get('supports_field', []):
        raise ValueError(field + ': evidence does not support field')
    query = aware(e['query_time'])
    if query > cutoff:
        raise ValueError(field + ': later query')
    for key in ('known_at', 'source_published_at'):
        if e.get(key) and aware(e[key]) > query:
            raise ValueError(field + ': invalid knowledge/publication time')
    return dict(e)

def assess(fixture):
    cutoff, kickoff = aware(fixture['cutoff_at']), aware(fixture['kickoff_at'])
    if cutoff >= kickoff:
        raise ValueError('context must be prematch')
    audit = fixture.get('context_audit') or {}
    features, evidence, blocks, reasons = {}, [], {}, []
    for side in ('home', 'away'):
        data = audit.get(side) or {}
        blocks[side] = {}
        for block in BLOCKS:
            local = {}
            try:
                b = data[block]
                e = source(b['evidence'], cutoff, block, official=block == 'availability')
                if block == 'strength':
                    games = number(b['games'], 'games', 1, integer=True)
                    points = number(b['points'], 'points', 0, games * 3, integer=True)
                    venue_games = number(b['venue_games'], 'venue_games', 1, games, integer=True)
                    local = {'points_per_game': points / games,
                             'venue_goals_for_per_game': number(b['venue_goals_for'], 'venue_goals_for', integer=True) / venue_games,
                             'venue_goals_against_per_game': number(b['venue_goals_against'], 'venue_goals_against', integer=True) / venue_games}
                    if b.get('competition') != fixture['cohort']:
                        raise ValueError('strength: competition mismatch')
                elif block == 'motivation':
                    # Goal/pressure category must have evidence, never guessed from odds.
                    objective = b['objective']
                    if objective not in OBJECTIVES:
                        raise ValueError('motivation: objective unknown')
                    gap = number(b['points_gap_to_target'], 'points_gap', -200, 200, integer=True)
                    remaining = number(b['remaining_matches'], 'remaining_matches', 1, 100, integer=True)
                    if type(b.get('draw_acceptable')) is not bool:
                        raise ValueError('motivation: draw acceptability unverified')
                    local = {'target_gap_per_remaining_match': gap / remaining,
                             'draw_acceptable': float(b['draw_acceptable'])}
                    local.update({'objective_' + k: float(objective == k) for k in OBJECTIVES})
                elif block == 'schedule':
                    cov = b['coverage']
                    if cov.get('all_competitions_verified') is not True:
                        raise ValueError('schedule: only league history or incomplete coverage')
                    if aware(cov['from']) > kickoff - timedelta(days=14) or aware(cov['to']) < kickoff + timedelta(days=7):
                        raise ValueError('schedule: coverage must span previous14/next7 days')
                    matches = b['matches']; previous, upcoming, ids = [], [], set()
                    if not isinstance(matches, list):
                        raise ValueError('schedule: matches must be a list')
                    for m in matches:
                        if m['match_id'] in ids:
                            raise ValueError('schedule: duplicate match')
                        ids.add(m['match_id'])
                        dt = aware(m['kickoff_at'])
                        if dt < aware(cov['from']) or dt > aware(cov['to']):
                            raise ValueError('schedule: match outside coverage')
                        if not m.get('competition'):
                            raise ValueError('schedule: match competition unknown')
                        source(m['evidence'], cutoff, 'schedule')
                        if m['match_id'] == fixture['match_id']:
                            if dt != kickoff:
                                raise ValueError('schedule: current match date conflict')
                            continue
                        if dt == kickoff:
                            raise ValueError('schedule: simultaneous unidentified match')
                        if dt < kickoff:
                            if dt >= cutoff or m.get('status') != 'finished':
                                raise ValueError('schedule: unresolved intervening match')
                            previous.append(dt)
                        else:
                            if m.get('status') != 'scheduled':
                                raise ValueError('schedule: future result leakage')
                            upcoming.append(dt)
                    rest = min(336., (kickoff - max(previous)).total_seconds() / 3600) if previous else 336.
                    local = {'kickoff_gap_hours_capped336': rest,
                             'matches_previous14d': float(sum(kickoff - timedelta(days=14) <= d < kickoff for d in previous)),
                             'matches_next7d': float(sum(kickoff < d <= kickoff + timedelta(days=7) for d in upcoming))}
                    # This is kickoff spacing, not minutes since actual full-time.
                elif block == 'availability':
                    source(b['lineup_evidence'], cutoff, 'lineup', official=True)
                    if b.get('lineup_confirmed') is not True:
                        raise ValueError('availability: official lineup unconfirmed')
                    local = {k: number(b[k], k, 0, 11, integer=True) for k in
                             ('missing_regular_starters', 'missing_key_attackers', 'missing_key_defenders')}
                    if max(local['missing_key_attackers'], local['missing_key_defenders']) > local['missing_regular_starters']:
                        raise ValueError('availability: missing-player counts inconsistent')
                elif block == 'tactics':
                    if b.get('verified') is not True or not b.get('matchup_summary'):
                        raise ValueError('tactics: matchup evidence incomplete')
                elif block == 'integrity':
                    if b.get('verified') is not True or not all(b.get(k) for k in ('venue', 'travel_summary', 'weather_summary')):
                        raise ValueError('integrity: venue/travel/weather incomplete')
                features.update({side + '_' + k: v for k, v in local.items()})
                evidence.append(e)
                if block == 'availability': evidence.append(b['lineup_evidence'])
                if block == 'schedule': evidence.extend(m['evidence'] for m in b['matches'])
                blocks[side][block] = {'complete': True}
            except (ValueError, KeyError, TypeError, AttributeError, OverflowError) as exc:
                reason = side + '.' + block + ': ' + str(exc)
                blocks[side][block] = {'complete': False, 'reason': reason}
                reasons.append(reason)
    identity = fixture.get('identity_verified') is True
    if not identity: reasons.append('identity: independent fixture/lottery verification incomplete')
    return {'version': VERSION, 'complete': not reasons, 'identity_verified': identity,
            'blocks': blocks, 'features': features, 'evidence': evidence,
            'reasons': reasons, 'missing_features_zero_filled': False}
