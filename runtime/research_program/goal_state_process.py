"""A pre-match regulation score distribution from time- and score-dependent goal rates."""

from collections import Counter, defaultdict
from datetime import date
from itertools import groupby
from math import exp

import numpy as np

from research.analysis_freeze import aware, digest
from . import market_baseline, three_arm_model
from .prior_team_strength import LeagueStrength, observations, selections

VERSION = 'WCPP-GOAL-STATE-1'
CHALLENGERS = ('process', 'process_half', 'process_projected')
PRIOR = 30.0
BLOCK_SECONDS = 1800.0
MATCH_SECONDS = 5400.0


def _record_clock(goal):
    # A stoppage goal belongs to its own half. Keep the original order even when
    # two stoppage goals map to the same endpoint in the 90-minute clock.
    half, seconds = goal['half'], goal['seconds']
    if half not in (1, 2) or not np.isfinite(seconds) or seconds < 0:
        raise ValueError('regulation half and nonnegative half-relative seconds required')
    return (half - 1) * 2700.0 + min(seconds, 2700.0)


def _state(diff):
    return 1 + int(diff > 0) - int(diff < 0)


def _exposure(start, stop, home_diff, rates, expected):
    for block in range(3):
        duration = min(stop, (block + 1) * BLOCK_SECONDS) - max(start, block * BLOCK_SECONDS)
        if duration > 0:
            expected[block, _state(home_diff)] += rates[0] * duration / MATCH_SECONDS
            expected[block, _state(-home_diff)] += rates[1] * duration / MATCH_SECONDS


def accumulate(game, rates, expected, goals):
    last, score = 0.0, [0, 0]
    previous = (0, 0.0)
    rates = np.asarray(rates, dtype=float)
    if rates.shape != (2,) or not np.all(np.isfinite(rates) & (rates > 0)):
        raise ValueError('two positive prematch goal rates required')
    for event in game['goals']:
        at = _record_clock(event)
        original = (event['half'], event['seconds'])
        if original < previous or event['side'] not in ('home', 'away'):
            raise ValueError('chronological goals with known beneficiary required')
        _exposure(last, at, score[0] - score[1], rates, expected)
        side = int(event['side'] == 'away')
        # Just before a goal at exactly 30, 60 or full time, the exposure
        # belongs to the block that ends there.
        block = max(0, min(2, int(np.ceil(at / BLOCK_SECONDS)) - 1))
        goals[block, _state(score[side] - score[1-side])] += 1
        score[side] += 1
        last, previous = at, original
    if list(game['actual']) != score:
        raise ValueError('goal beneficiaries do not reproduce regulation score')
    _exposure(last, MATCH_SECONDS, score[0] - score[1], rates, expected)


def fit(games, train_before, source_sha256):
    """Estimate each scorer's intensity relative to earlier team attack/defence."""
    cutoff = date.fromisoformat(train_before)
    games = sorted(games, key=lambda r: (r['date'], r['match_id']))
    if not games or len({r['match_id'] for r in games}) != len(games):
        raise ValueError('nonempty, unique training fixtures required')
    expected = np.zeros((3, 3))
    goals = np.zeros((3, 3), dtype=int)
    teams = defaultdict(LeagueStrength)
    coverage = Counter()
    for stamp, same_day in groupby(games, key=lambda r: r['date']):
        day = date.fromisoformat(stamp)
        if day >= cutoff:
            raise ValueError('training match reaches the exclusive cutoff')
        daily = defaultdict(list)
        for game in same_day:
            daily[game['cohort']].append(game)
        for cohort, items in daily.items():
            state = teams[cohort]
            coefficients = state.coefficients(day)
            for game in items:
                rates = state.rates(coefficients, game['home'], game['away'])[:, 0]
                accumulate(game, rates, expected, goals)
            state.update_day(day, [(g['home'], g['away'], observations(g['actual'])) for g in items])
            coverage[cohort] += len(items)
    return dict(version=VERSION, train_before=train_before, first_date=games[0]['date'],
                last_date=games[-1]['date'], source_sha256=source_sha256, n=len(games),
                fixture_ids_sha256=digest([g['match_id'] for g in games]),
                coverage=dict(coverage), scorer_states=['behind', 'level', 'ahead'],
                expected_exposure=expected.tolist(), goals=goals.tolist(), prior=PRIOR,
                factors=((goals + PRIOR) / (expected + PRIOR)).tolist())


def simulate(rates, factors, size):
    """Forward distribution of a time-inhomogeneous birth process by uniformization."""
    factors = np.asarray(factors, dtype=float)
    rates = np.asarray(rates, dtype=float)
    if (rates.shape != (2,) or factors.shape != (3, 3) or size < 2
            or not np.all(np.isfinite(rates) & (rates > 0))
            or not np.all(np.isfinite(factors) & (factors > 0))):
        raise ValueError('two positive rates and three time-by-state factors required')
    h, a = np.indices((size, size))
    states = 1 + np.sign(h - a)
    mass = np.zeros((size, size))
    mass[0, 0] = 1.0
    for block in factors:
        home = rates[0] * block[states]
        away = rates[1] * block[2 - states]
        # Last cell per axis contains the negligible overflow tail on that axis.
        home[-1, :] = 0.
        away[:, -1] = 0.
        max_rate = float(np.max(home + away))
        home /= max_rate
        away /= max_rate
        stay = np.maximum(0., 1. - home - away)
        mean = max_rate / 3.
        weight = exp(-mean)
        if weight == 0.:
            raise ValueError('rates exceed uniformization precision')
        cumulative = weight
        updated = weight * mass
        jumps = 0
        while cumulative < 1. - 1e-13:
            next_mass = mass * stay
            next_mass[1:, :] += mass[:-1, :] * home[:-1, :]
            next_mass[:, 1:] += mass[:, :-1] * away[:, :-1]
            mass = next_mass
            jumps += 1
            weight *= mean / jumps
            cumulative += weight
            updated += weight * mass
        mass = updated / cumulative
    return mass / mass.sum()


def matrices(artifact, h2h, total, game_day):
    if artifact['version'] != VERSION or date.fromisoformat(game_day) < date.fromisoformat(artifact['train_before']):
        raise ValueError('training artifact must precede this fixture')
    plain, market = market_baseline.quote_only_matrices(h2h, total)
    support = np.arange(len(plain))
    rates = (support @ plain.sum(axis=1), support @ plain.sum(axis=0))
    process = simulate(rates, artifact['factors'], len(plain))
    target = three_arm_model._quote_values(dict(h2h=h2h, total=total), None, proxy=True)
    projected = three_arm_model._market_matrix(process, target)
    return dict(market=market, process=process, process_half=.5*(process+market),
                process_projected=projected)


def combine_heads(arms, policy):
    """Keep the first score, take two distinct backups, and use independent direction heads."""
    primary = arms[policy['first']]['primary']
    backups = [s for s in arms[policy['backups']]['top3'] if s != primary]
    total = arms[policy['total']]
    return dict(primary=primary, top3=[primary, *backups[:2]], wdl=arms[policy['wdl']]['wdl'],
                total_side=total['total_side'], total_line=total['total_line'])


def compact(picks):
    total = picks['total']
    return dict(primary=picks['primary'], top3=picks['top3'], wdl=picks['wdl'],
                total_side=total['direction'] if total else None, total_line=total['line'] if total else None)


def predict(artifact, fixture, selected, heads=None):
    """Use the normal same-event, same-book 90-minute market capture audit for a first issue."""
    if selected not in CHALLENGERS:
        raise ValueError('unknown registered challenger')
    reference = market_baseline.predict(fixture)
    if reference['status'] != 'AVAILABLE':
        return dict(status='ABSTAIN', reasons=reference['reasons'])
    capture = reference['quote_snapshot']
    total = dict(capture['total']['prices'], line=capture['total']['line'])
    slate = matrices(artifact, capture['h2h']['prices'], total, aware(fixture['kickoff_at']).date().isoformat())
    names = ('market', *CHALLENGERS) if heads else ('market', selected)
    arms = {name: selections(slate[name], total['line']) for name in names}
    composite = combine_heads({k: compact(v) for k, v in arms.items()}, heads) if heads else None
    return dict(status='AVAILABLE', version=VERSION, selected=selected,
                artifact_sha256=digest(artifact), quote_snapshot=capture,
                quote_snapshot_sha256=reference['quote_snapshot_sha256'],
                arms=arms, composite=composite)
