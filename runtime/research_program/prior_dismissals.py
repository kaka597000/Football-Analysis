"""Shadow-only ablation of unequal red-card matches from prior goal-strength updates."""

from collections import Counter
from datetime import date
from itertools import groupby
from operator import itemgetter

from .prior_team_strength import LeagueStrength, SHAPE, observations, selections
from .score_form import poisson_matrix

MIN_HISTORY = 60


def card_pair(home, away):
    values = [str(x).strip() for x in (home, away)]
    if not all(x.isascii() and x.isdecimal() for x in values):
        return None
    return tuple(map(int, values))


def fit_season(rows, year):
    """Both fits stop on November 8. Later rows are discarded before field access."""
    models = (LeagueStrength(), LeagueStrength())
    counts = Counter()
    prior = sorted((r for r in rows if date(year, 7, 1) <= r['day'] <= date(year, 11, 8)),
                   key=itemgetter('day', 'home', 'away'))
    for day, fixtures in groupby(prior, itemgetter('day')):
        all_games, filtered = [], []
        for r in fixtures:
            if r.get('goals') is None:
                counts['no_regulation_result'] += 1
                continue
            game = (r['home'], r['away'], observations(r['goals']))
            all_games.append(game)
            counts['baseline'] += 1
            cards = card_pair(r.get('HR'), r.get('AR'))
            if cards is None:
                counts['unknown_cards'] += 1
            elif cards[0] != cards[1]:
                counts['excluded_unequal'] += 1
                continue
            else:
                counts['known_equal'] += 1
            filtered.append(game)
            counts['candidate'] += 1
        for model, games in zip(models, (all_games, filtered)):
            model.update_day(day, games)
    return models, dict(counts)


def forecast(fit, day, home, away, line=None):
    models, counts = fit
    if counts.get('baseline', 0) < MIN_HISTORY:
        return {'status': 'short_baseline_history'}
    if any(team not in models[0].teams for team in (home, away)) or home == away:
        return {'status': 'unresolved_club'}
    fallback = (counts.get('candidate', 0) < MIN_HISTORY
                or any(team not in models[1].teams for team in (home, away)))
    result = {'status': 'paired', 'candidate_fallback': fallback}
    for name, model in zip(('baseline', 'candidate'), (models[0], models[0] if fallback else models[1])):
        rates = model.rates(model.coefficients(day), home, away)[:, 0]
        p = selections(poisson_matrix(rates, SHAPE), line)
        result[name] = {'rates': rates.tolist(), 'first': p['primary'], 'top3': p['top3'],
                        'wdl': p['wdl'], 'total': p['total']['direction'] if p['total'] else None}
    return result
