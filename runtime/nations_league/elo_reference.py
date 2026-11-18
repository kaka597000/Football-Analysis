"""Independent international Elo and league-specific regulation score references."""

from collections import defaultdict
import csv
import io
from itertools import groupby
from operator import itemgetter

import numpy as np
from scipy.optimize import minimize

from research_program.three_arm_model import score_picks
from v2.model import matrix


ALIASES = {'Türkiye': 'Turkey', 'Czechia': 'Czech Republic', 'Bosnia-Herzegovina': 'Bosnia and Herzegovina'}
ABANDONED = ('2024-11-15', 'Romania', 'Kosovo')


def international_matches(raw, last_available_day):
    """Read the dated senior international result file available at a confirmed vintage."""
    games = []
    for row in csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))):
        if row['date'] < '1990-01-01' or row['date'] > last_available_day:
            continue
        key = (row['date'], row['home_team'], row['away_team'])
        if key == ABANDONED or not (row['home_score'].isdigit() and row['away_score'].isdigit()):
            continue
        if row['neutral'] not in ('TRUE', 'FALSE'):
            raise ValueError(f'unknown venue status in world result {key}')
        games.append({'date': key[0], 'home':key[1], 'away':key[2], 'neutral':row['neutral']=='TRUE',
                      'score':[int(row['home_score']),int(row['away_score'])], 'tournament':row['tournament']})
    return games


def dated_ratings(matches, cutoffs):
    """Materialize ratings before each requested UTC calendar day, excluding that day's results.

    Source match dates have no kickoff time. Update all matches from a date together,
    including the rare case of a country fielding two sides on the same date.
    """
    wanted = iter(sorted(set(cutoffs)))
    day = next(wanted, None)
    ratings, snapshots = {}, {}
    by_day = sorted(matches, key=itemgetter('date'))
    for played, batch in groupby(by_day, key=itemgetter('date')):
        while day is not None and day <= played:
            snapshots[day] = ratings.copy()
            day = next(wanted, None)
        if day is None:
            break
        changes = defaultdict(float)
        for match in batch:
            home, away = match['home'], match['away']
            home_rating = ratings.get(home, 1500.)
            away_rating = ratings.get(away, 1500.)
            advantage = 0. if match['neutral'] else 60.
            expected = 1. / (1. + 10. ** ((away_rating - home_rating - advantage) / 400.))
            home_goals, away_goals = match['score']
            outcome = 1. if home_goals > away_goals else (0. if home_goals < away_goals else .5)
            change = 20. * (outcome - expected)
            changes[home] += change
            changes[away] -= change
        for team, change in changes.items():
            ratings[team] = ratings.get(team, 1500.) + change
    while day is not None:
        snapshots[day] = ratings.copy()
        day = next(wanted, None)
    return snapshots


def design(home_rating, away_rating, neutral, include_elo):
    home = [1., 0. if neutral else 1.]
    away = [1., 0. if neutral else -1.]
    if include_elo:
        gap = (home_rating - away_rating) / 400.
        home.append(gap)
        away.append(-gap)
    return np.array([home, away])


def fit(training, include_elo):
    """Fit the two score intensities on regulation scores from one prior league grade."""
    features = np.vstack([design(*r['ratings'], r['neutral'], include_elo) for r in training])
    goals = np.asarray([r['score'] for r in training]).ravel()

    def objective(weights):
        log_rates = features @ weights
        rates = np.exp(log_rates)
        return np.mean(rates - goals * log_rates), features.T @ (rates - goals) / len(goals)

    result = minimize(objective, np.zeros(features.shape[1]), jac=True, method='BFGS', tol=1e-9)
    if not result.success and max(abs(result.jac)) > 1.e-7:
        raise ValueError(f'Poisson convergence failed: {result.message}')
    return {'include_elo': include_elo, 'coefficients': result.x.tolist(), 'matches': len(training)}


def forecast(artifact, ratings, neutral):
    rates = np.exp(design(*ratings, neutral, artifact['include_elo']) @ artifact['coefficients'])
    return matrix(float(rates[0]), float(rates[1]))


def selections(pmf, score):
    """Evaluate the joint mode, the 1X2 marginals, and a fixed research 2.5 line."""
    pmf = np.asarray(pmf)
    picks = score_picks(pmf, line=2.5)
    home, away = score
    direction = 'home' if home > away else ('away' if away > home else 'draw')
    over = sum(score) >= 3
    observed = float(pmf[home, away]) if 0 <= home < pmf.shape[0] and 0 <= away < pmf.shape[1] else 0.
    return {
        'primary': picks['primary'], 'wdl': picks['wdl'], 'total_2_5': picks['total']['direction'],
        'primary_hit': picks['primary'] == f'{home}-{away}', 'wdl_hit': picks['wdl'] == direction,
        'total_hit': (picks['total']['direction'] == 'over') == over,
        'p_observed_score': observed, 'score_log_loss': -float(np.log(observed)) if observed > 0 else None,
        'wdl_probabilities': picks['wdl_probabilities'], 'p_over_2_5': float(picks['total']['settlements']['over']['win']),
    }
