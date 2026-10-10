"""Competition identity and analysis requirements, before any rate estimation.

Registered identities are explicit; names and market prices never infer sex/age.
Profiles are descriptive, not validated numerical adjustment coefficients.
"""
import sys
import re
from pathlib import Path
from datetime import datetime, timedelta
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'database'))
from registry import LEAGUES, competition

CONTRACT = 'wcpp-competition-router-1'
FIELDS = ('competition', 'gender', 'age', 'level', 'entity', 'kind', 'stage')
CUPS = {'jpn.league_cup', 'jpn.emperor', 'kor.cup', 'eng.league_cup'}
CONTINENTAL = {'uefa.champions', 'afc.champions', 'conmebol.libertadores'}
LOWER = {'eng.2', 'ger.2', 'ned.2', 'jpn.2', 'kor.2'}
PHASED_LEAGUES = {'usa.1', 'aus.1', 'eng.2', 'jpn.2', 'kor.1', 'kor.2', 'ned.1', 'fin.1'}
REGULAR_SLUGS = {'bra.1':'brasileiro-serie-a', 'jpn.1':'japanese-j1-league',
                 'nor.1':'eliteserien', 'swe.1':'allsvenskan'}
BASE_CHECKS = ['opponent_adjusted_strength', 'objective_and_draw_acceptability',
               'all_competition_previous14_next7_schedule', 'official_lineup_and_rotation',
               'team_tactical_matchup', 'venue_travel_weather', 'actual_bookmaker_total_line']


def aware(value):
    date = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if date.tzinfo is None: raise ValueError('router evidence requires timezone')
    return date


def evidence(e, cutoff):
    parsed = urlparse(e.get('source_url', ''))
    if parsed.scheme != 'https' or not parsed.netloc or e.get('source_tier') != 'L1':
        raise ValueError('classification/rules require official evidence')
    if 'competition_classification' not in e.get('supports_field', []):
        raise ValueError('evidence must support competition_classification')
    query = aware(e['query_time'])
    if query > aware(cutoff): raise ValueError('later classification/rules evidence')
    for key in ('known_at', 'source_published_at'):
        if e.get(key) and aware(e[key]) > query: raise ValueError('later evidence publication')


def registered(code):
    if code not in LEAGUES: return None
    kind = ('domestic_cup' if code in CUPS else 'continental_cup' if code in CONTINENTAL
            else 'national_competitive' if code == 'uefa.nations'
            else 'friendly' if code == 'fifa.friendly'
            else 'transition' if '.centennial.' in code else 'league')
    return dict(competition=code, gender='men', age='senior',
                level='tier2' if code in LOWER else 'tier1' if kind == 'league' else 'mixed',
                entity='national' if code in ('uefa.nations', 'fifa.friendly') else 'club', kind=kind)


def classify(item):
    raw = item.get('competition') or item.get('cohort', '').split(':', 1)[0]
    code = competition(raw)
    supplied = item.get('competition_classification')
    base = registered(code)
    if supplied:
        evidence(supplied.get('evidence') or {}, item.get('cutoff_at') or item['observed_at'])
        explicit = {k: supplied.get(k) for k in FIELDS if k != 'stage'}
        if base and explicit != base: raise ValueError('classification conflicts with registered identity')
        base = explicit
    if not base:
        return dict(contract=CONTRACT, complete=False, reasons=['Unknown competition identity; no adult-men fallback'],
                    route_key=None, classification=None, requirements=list(BASE_CHECKS))
    stage = item.get('stage') or (supplied or {}).get('stage')
    if item.get('stage') and (supplied or {}).get('stage') and item['stage'] != supplied['stage']:
        raise ValueError('explicit stage conflicts with fixture stage')
    if not stage and ':' in item.get('cohort', ''): stage = item['cohort'].split(':', 1)[1]
    if not stage:
        stage = ('regular' if base['kind'] == 'league' and code not in PHASED_LEAGUES
                 else 'friendly' if base['kind'] == 'friendly' else 'unknown')
    if stage == 'regular-season': stage = 'regular'
    if code in REGULAR_SLUGS and re.fullmatch(r'\d{4}-'+re.escape(REGULAR_SLUGS[code]), stage):
        stage = 'regular'
    if base['kind'] == 'friendly' and re.fullmatch(r'\d{4}-international-friendly', stage): stage = 'friendly'
    if code == 'usa.1' and stage == 'all-star-game':
        base = dict(base, kind='friendly', level='mixed')
    c = dict(base, stage=stage)
    reasons = []
    if c['gender'] not in ('men', 'women'): reasons.append('gender unknown')
    if c['age'] not in ('senior', 'U23', 'U21', 'U20', 'U19', 'U18', 'U17', 'U16', 'U15'):
        reasons.append('age eligibility unknown')
    if c['level'] not in ('tier1', 'tier2', 'tier3', 'lower', 'mixed', 'international'):
        reasons.append('competition level unknown')
    if c['entity'] not in ('club', 'national'): reasons.append('club/national identity unknown')
    if c['kind'] not in ('league', 'domestic_cup', 'continental_cup', 'national_competitive', 'friendly', 'transition'):
        reasons.append('competition type unknown')
    if not c['competition'] or c['stage'] in ('unknown', 'cup'): reasons.append('competition/stage unknown')
    checks = list(BASE_CHECKS)
    if c['kind'] != 'league': checks += ['official_format_and_qualification_state']
    if c['kind'] in ('domestic_cup', 'continental_cup', 'national_competitive', 'transition'):
        checks += ['single_or_two_leg', 'aggregate_before_match', 'extra_time_and_penalty_rules']
    if c['kind'] == 'friendly': checks += ['substitution_rules', 'experimentation_and_rotation']
    if c['age'] != 'senior': checks += ['edition_age_and_overage_rules', 'squad_continuity']
    if c['gender'] == 'women': checks += ['women_only_strength_and_calibration_samples']
    route = '|'.join(str(c[k]) for k in FIELDS) if not reasons else None
    return dict(contract=CONTRACT, complete=not reasons, reasons=reasons, classification=c,
                route_key=route, requirements=checks, provenance=(supplied or {}).get('evidence') or
                {'registry': 'runtime/database/registry.py', 'identity_basis': 'explicit competition-code mapping'})


def validate_training(rows):
    routes = [classify(r) for r in rows]
    if not routes or any(not r['complete'] for r in routes):
        raise ValueError('classified competition identity/stage required before training')
    if len({r['route_key'] for r in routes}) != 1:
        raise ValueError('gender/age/level/type/stage cohorts must be fitted separately')
    return routes[0]


def format_audit(fixture, route):
    """Additional release gates; unknown tournament state never becomes a zero."""
    c = route['classification']; reasons = []
    if type(fixture.get('neutral')) is not bool: reasons.append('Neutral venue not verified')
    if not c or (c['kind'] == 'league' and c['age'] == 'senior' and c['stage'] == 'regular'): return reasons
    rules = fixture.get('competition_rules') or {}
    try:
        evidence(rules['evidence'], fixture['cutoff_at'])
        if rules.get('competition') != c['competition'] or rules.get('stage') != c['stage']:
            raise ValueError('format evidence competition/stage mismatch')
        if rules.get('score_basis') != 'regulation_90': raise ValueError('90-minute target required')
        if rules.get('neutral') != fixture.get('neutral'): raise ValueError('venue conflict')
        for k in ('qualification_state', 'rotation_summary'):
            if not isinstance(rules.get(k), str) or not rules[k].strip(): raise ValueError(k + ' unknown')
        if c['kind'] == 'league' and c['stage'] == 'regular':
            if not rules.get('age_eligibility_rule'): raise ValueError('edition-specific age rule unknown')
        elif c['kind'] == 'friendly':
            if not rules.get('substitution_rules') or not rules.get('experimentation_summary'):
                raise ValueError('friendly substitution/experimentation unknown')
        else:
            if rules.get('format') not in ('group', 'single_leg', 'two_leg', 'league_phase'):
                raise ValueError('format unknown')
            if type(rules.get('extra_time_possible')) is not bool or type(rules.get('penalties_possible')) is not bool:
                raise ValueError('extra-time/penalty rules unknown')
            if rules['format'] == 'two_leg':
                if type(rules.get('leg_number')) is not int or rules['leg_number'] not in (1, 2):
                    raise ValueError('leg number unknown')
                aggregate = rules.get('aggregate_before_match')
                if not isinstance(aggregate, list) or len(aggregate) != 2 or any(type(v) is not int or v < 0 for v in aggregate):
                    raise ValueError('aggregate must be home/away ordered known goals')
                if rules['leg_number'] == 1 and aggregate != [0, 0]: raise ValueError('first-leg aggregate inconsistent')
                if type(rules.get('away_goals_rule')) is not bool: raise ValueError('away-goals rule unknown')
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        reasons.append('Competition format audit: ' + str(exc))
    if c['age'] != 'senior' and not rules.get('age_eligibility_rule'):
        reasons.append('Edition-specific age/overage rules unknown')
    return reasons


def empirical_profile(rows, fixture, days=730):
    """Strictly prior same-route score tendencies, never a tactical stereotype.

    Result-only data cannot establish possession, xG, pace or attacking intent.
    Newly retrieved old results do not constitute prospective evidence.
    """
    route = classify(fixture); cutoff = aware(fixture['cutoff_at'])
    end = aware(fixture['kickoff_at']).date(); start = end - timedelta(days=days)
    past = [r for r in rows if route['complete'] and start.isoformat() <= r['date'] < end.isoformat()
            and aware(r['observed_at']) <= cutoff and classify(r)['route_key'] == route['route_key']]
    n = len(past)
    return dict(route_key=route['route_key'], n=n, window_days=days,
                status='DESCRIPTIVE_ONLY' if n >= 60 else 'INSUFFICIENT_SAMPLE',
                mean_total_goals=sum(r['home_goals']+r['away_goals'] for r in past)/n if n else None,
                both_scored_rate=sum(r['home_goals']>0 and r['away_goals']>0 for r in past)/n if n else None,
                draw_rate=sum(r['home_goals']==r['away_goals'] for r in past)/n if n else None,
                possession_style='UNKNOWN', xg_style='UNKNOWN', numeric_adjustment_applied=False,
                limitation='Score tendencies do not prove attacking/possession style or prospective accuracy')
