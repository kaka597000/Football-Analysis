"""Seeded minute-grid Poisson research, isolated from the active forecast chain.

State effects are disabled unless a separately fitted, coverage-gated artifact
is supplied. Simulation frequencies are model-implied, not calibrated forecasts.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

import numpy as np

VERSION = 'WCPP-MINUTE-SIM-0.1'
BASELINE_SCHEMA = 'WCPP-MINUTE-RATES-1'
HAZARD_SCHEMA = 'WCPP-MINUTE-STATE-HAZARDS-1'
DEFAULT_COVERAGE_POLICY = {'minimum_matches': 200, 'minimum_complete_fraction': .95}


def aware(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('timezone required')
    return result


def digest_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest_object(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def checked_reference(ref):
    """Read a local evidence file only when its immutable identity is supplied."""
    path = Path(ref['path'])
    if digest_file(path) != ref['sha256']:
        raise ValueError('source evidence checksum mismatch')
    return json.loads(path.read_text())


def pointer(value, path):
    if not path.startswith('/'):
        raise ValueError('explicit source JSON Pointer required')
    for token in path[1:].split('/'):
        token = token.replace('~1', '/').replace('~0', '~')
        value = value[int(token)] if isinstance(value, list) else value[token]
    return value


def nonnegative_int(value, label):
    if type(value) is not int or value < 0:
        raise ValueError(label + ' must be a nonnegative integer')
    return value


def validate_rates(artifact, as_of):
    if artifact.get('schema') != BASELINE_SCHEMA:
        raise ValueError('source-backed minute rates artifact required')
    if artifact.get('score_basis') != 'regulation_90_including_stoppage':
        raise ValueError('regulation 90-minute scope required')
    if not artifact.get('cohort'):
        raise ValueError('cohort required')
    if not aware(artifact['trained_through']) < as_of:
        raise ValueError('training must precede prediction cutoff')
    if aware(artifact['created_at']) > as_of or aware(artifact['source']['observed_at']) > as_of:
        raise ValueError('source or artifact unavailable at cutoff')
    source = checked_reference(artifact['source'])
    rates = pointer(source, artifact['source']['rates_pointer'])
    if (not isinstance(rates, list) or len(rates) != 2 or
            any(type(x) not in (int, float) or not math.isfinite(x) or not 0 <= x <= 12 for x in rates)):
        raise ValueError('two sourced finite full-match goal rates required')
    return np.asarray(rates, dtype=float)


def validate_hazards(artifact, cohort, as_of, policy):
    if artifact.get('schema') != HAZARD_SCHEMA or artifact.get('status') != 'FITTED_RESEARCH':
        raise ValueError('state hazards have not been fitted')
    if artifact.get('cohort') != cohort:
        raise ValueError('state hazard cohort mismatch')
    if artifact.get('score_basis') != 'regulation_90_including_stoppage':
        raise ValueError('state hazard score basis mismatch')
    if not aware(artifact['trained_through']) < as_of or aware(artifact['created_at']) > as_of:
        raise ValueError('state hazard time leakage')
    coverage = artifact['coverage']
    if (type(coverage['matches']) is not int or coverage['matches'] < policy['minimum_matches']
            or not policy['minimum_complete_fraction'] <= coverage['complete_fraction'] <= 1):
        raise ValueError('event coverage gate failed')
    checked_reference(artifact['training_manifest'])
    checked_reference(artifact['temporal_validation_report'])
    multipliers = artifact['score_state_multipliers']
    if set(multipliers) != {'home_ahead', 'draw', 'home_behind'}:
        raise ValueError('all score states required')
    for pair in multipliers.values():
        if (not isinstance(pair, list) or len(pair) != 2 or
                any(type(x) not in (int, float) or not math.isfinite(x) or not 0 < x <= 4 for x in pair)):
            raise ValueError('invalid fitted hazard multipliers')
    # Card interactions need a separate trained engine. No guessed multipliers.
    return multipliers


def validate_state(state, as_of, duration):
    minute = state['minute']
    if type(minute) not in (int, float) or not math.isfinite(minute) or not 0 <= minute <= duration:
        raise ValueError('current regulation minute out of range')
    if state.get('clock_basis') != 'regulation_elapsed_minutes':
        raise ValueError('normalized regulation clock required')
    if aware(state['observed_at']) > as_of:
        raise ValueError('future match state')
    score = state['score']
    if not isinstance(score, list) or len(score) != 2:
        raise ValueError('home/away score required')
    for x in score:
        nonnegative_int(x, 'score')
    card_state = state.get('red_cards')
    if card_state is None:
        if minute > 0:
            raise ValueError('live red-card state UNKNOWN; explicit home/away red_cards required')
        card_state = [0, 0]
    if not isinstance(card_state, list) or len(card_state) != 2:
        raise ValueError('explicit home/away red_cards required')
    for x in card_state:
        nonnegative_int(x, 'red_cards')
    if minute == 0 and (score != [0, 0] or any(card_state) or state.get('events')):
        raise ValueError('prematch minute zero cannot contain occurred score or events')
    counts = [0, 0]
    card_counts = [0, 0]
    previous = -1
    for event in state.get('events', []):
        if event.get('period') not in ('first_half', 'second_half'):
            raise ValueError('extra time and shootouts are excluded')
        when = event['minute']
        if type(when) not in (int, float) or not math.isfinite(when) or not 0 <= when <= minute or when < previous:
            raise ValueError('future, negative or unordered event')
        if aware(event['observed_at']) > as_of:
            raise ValueError('future event information')
        if event['type'] == 'goal':
            if event.get('beneficiary_side') not in ('home', 'away'):
                raise ValueError('explicit goal beneficiary required; own goals use benefiting team')
            counts[event['beneficiary_side'] == 'away'] += 1
        elif event['type'] == 'red_card':
            side = event.get('carded_side', event.get('beneficiary_side'))
            if side not in ('home', 'away'):
                raise ValueError('explicit red-card recipient side required')
            card_counts[side == 'away'] += 1
        else:
            raise ValueError('unsupported event type')
        previous = when
    if any(a > b for a, b in zip(counts, score)):
        raise ValueError('observed goals exceed current score')
    if state.get('events_complete') is True and counts != score:
        raise ValueError('complete events do not reproduce current score')
    if any(a > b for a, b in zip(card_counts, card_state)):
        raise ValueError('observed red-card events exceed declared card state')
    if state.get('events_complete') is True and card_counts != card_state:
        raise ValueError('complete events do not reproduce red-card state')
    return float(minute), np.asarray(score, dtype=int), sum(card_state)


def simulate(artifact, state, *, as_of, regulation_minutes=96, draws=20000,
             seed=0, state_artifact=None, coverage_policy=None):
    """Return a source-backed Monte Carlo diagnostic, never mutate inputs.

    `regulation_minutes` is a fixed scenario horizon, including explicit planned
    stoppage, not a prediction of how much added time a referee will award.
    """
    cutoff = aware(as_of)
    if cutoff > datetime.now(timezone.utc):
        raise ValueError('prediction cutoff cannot be in the future')
    if type(regulation_minutes) not in (int, float) or not math.isfinite(regulation_minutes) or not 90 <= regulation_minutes <= 120:
        raise ValueError('explicit regulation-plus-stoppage horizon required')
    if type(draws) is not int or not 100 <= draws <= 1_000_000:
        raise ValueError('draws must be 100..1000000')
    nonnegative_int(seed, 'seed')
    rates = validate_rates(artifact, cutoff)
    minute, initial_score, red_cards = validate_state(state, cutoff, regulation_minutes)
    if red_cards:
        raise ValueError('red-card transition engine NOT_TRAINED; refusing card-adjusted simulation')
    modifiers = None
    if state_artifact is not None:
        modifiers = validate_hazards(state_artifact, artifact['cohort'], cutoff,
                                    coverage_policy or DEFAULT_COVERAGE_POLICY)
    rng = np.random.default_rng(seed)
    score = np.tile(initial_score, (draws, 1))
    trajectory = []
    elapsed = minute
    while elapsed < regulation_minutes:
        step = min(1.0, regulation_minutes - elapsed)
        means = np.tile(rates / regulation_minutes * step, (draws, 1))
        if modifiers is not None:
            diff = score[:, 0] - score[:, 1]
            for label, mask in (('home_ahead', diff > 0), ('draw', diff == 0), ('home_behind', diff < 0)):
                means[mask] *= np.asarray(modifiers[label])
        score += rng.poisson(means)
        elapsed += step
        trajectory.append({'minute': elapsed, 'home_mean': float(score[:, 0].mean()),
                           'away_mean': float(score[:, 1].mean())})
    values, counts = np.unique(score, axis=0, return_counts=True)
    frequencies = [{'score': [int(h), int(a)], 'frequency': int(n) / draws}
                   for (h, a), n in zip(values, counts)]
    frequencies.sort(key=lambda x: (-x['frequency'], *x['score']))
    diff = score[:, 0] - score[:, 1]
    remaining = rates * (regulation_minutes - minute) / regulation_minutes
    return {'version': VERSION, 'status': 'EXPERIMENTAL_MINUTE_GRID_ONLY',
            'cohort': artifact['cohort'], 'as_of': as_of, 'seed': seed, 'draws': draws,
            'input_sha256': digest_object({'artifact': artifact, 'state': state, 'as_of': as_of,
                'regulation_minutes': regulation_minutes, 'draws': draws, 'seed': seed,
                'state_artifact': state_artifact,
                'coverage_policy': coverage_policy or DEFAULT_COVERAGE_POLICY}),
            'rates_artifact_sha256': digest_object(artifact),
            'state_artifact_sha256': digest_object(state_artifact) if state_artifact else None,
            'regulation_minutes': regulation_minutes,
            'source_sha256': artifact['source']['sha256'],
            'state_effect_status': 'FITTED_RESEARCH_NOT_PROMOTED' if modifiers else 'NOT_TRAINED_IDENTITY_ONLY',
            'red_card_effect_status': 'NOT_TRAINED',
            'stoppage_time_model_status': 'NOT_TRAINED_FIXED_SCENARIO_HORIZON',
            'mean_final_goals': score.mean(axis=0).tolist(),
            'analytic_identity_mean': (initial_score + remaining).tolist() if not modifiers else None,
            'wdl_frequencies': {'home': float(np.mean(diff > 0)), 'draw': float(np.mean(diff == 0)),
                                'away': float(np.mean(diff < 0))},
            'score_frequencies': frequencies, 'minute_mean_trajectory': trajectory,
            'active_forecast_modified': False, 'prospective_accuracy_proven': False,
            'calibration_status': 'MODEL_IMPLIED_MONTE_CARLO_NOT_EMPIRICALLY_CALIBRATED',
            'limitations': ['No event time, future red card or state-change story is supplied by an LLM.',
                           'Minute-grid baseline has no extra predictive information over the sourced independent Poisson rates.',
                           'Fitted state effects need separate temporal evaluation; coverage metadata alone is not accuracy evidence.']}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('input', help='JSON bundle: artifact, state, as_of and optional simulator arguments')
    p.add_argument('--out', required=True)
    a = p.parse_args()
    target = Path(a.out)
    if target.exists():
        raise FileExistsError(a.out)
    result = simulate(**json.loads(Path(a.input).read_text()))
    with target.open('x') as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2, allow_nan=False)
    print(a.out)


if __name__ == '__main__':
    main()
