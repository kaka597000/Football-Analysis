"""Date-isolated Understat team histories for the registered R14 domestic leagues."""

from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from math import isfinite
from typing import NamedTuple
import unicodedata

import numpy as np


LEAGUES = {'EPL': 'eng.1', 'La_liga': 'esp.1', 'Bundesliga': 'ger.1',
           'Serie_A': 'ita.1', 'Ligue_1': 'fra.1'}
FEATURES = ('own_xg_attack', 'opponent_xg_concession', 'own_deep_attack',
            'opponent_deep_concession', 'own_pressing', 'opponent_pressing')
METRICS = ('xg_for', 'xg_against', 'deep_for', 'deep_against', 'ppda')
SIDES = ('home', 'away')
VENUE_PRIOR = np.array([1.35, 6., 220., 20.])  # xG, deep, PPDA att, PPDA def
FORM_WINDOW = timedelta(days=150)
CALENDAR_WINDOW = timedelta(days=400)
LAST_GAMES = 8

# Documented spellings from R5 forward/model-aliases.json and its ESPN fixture
# names, the provider crosswalk in optimization/dataset.py and v7/export_training.py.
# Each group denotes one club in one league. A spelling never supplies a source ID.
_SPELLINGS = {
    'eng.1': [('AFC Bournemouth', 'Bournemouth'), ('Brighton & Hove Albion', 'Brighton'),
              ('Coventry City', 'Coventry'), ('Hull City', 'Hull'), ('Ipswich Town', 'Ipswich'),
              ('Leeds United', 'Leeds'), ('Manchester City', 'Man City'),
              ('Manchester United', 'Man United', 'Man Utd'), ('Newcastle United', 'Newcastle'),
              ('Nottingham Forest', "Nott'm Forest", 'Nottm Forest'),
              ('Tottenham Hotspur', 'Tottenham', 'Spurs')],
    'esp.1': [('Athletic Club', 'Ath Bilbao'), ('Atlético Madrid', 'Ath Madrid'),
              ('Celta Vigo', 'Celta'), ('Deportivo', 'La Coruna'), ('Racing Santander', 'Santander'),
              ('Rayo Vallecano', 'Vallecano'), ('Real Betis', 'Betis'),
              ('Real Sociedad', 'Sociedad'), ('SD Huesca', 'Huesca')],
    'ger.1': [('1. FC Union Berlin', 'Union Berlin'), ('Bayer Leverkusen', 'Leverkusen'),
              ('Borussia Dortmund', 'Dortmund'),
              ('Borussia Mönchengladbach', 'Borussia M.Gladbach', "M'gladbach"),
              ('Eintracht Frankfurt', 'Ein Frankfurt'), ('FC Augsburg', 'Augsburg'),
              ('FC Cologne', 'FC Koln'), ('Hamburg SV', 'Hamburg'), ('SC Freiburg', 'Freiburg'),
              ('SC Paderborn 07', 'Paderborn'), ('SV Elversberg', 'Elversberg'),
              ('TSG Hoffenheim', 'Hoffenheim'), ('VfB Stuttgart', 'Stuttgart'),
              ('RasenBallsport Leipzig', 'RB Leipzig'), ('Mainz 05', 'Mainz'),
              ('FC Heidenheim', '1. FC Heidenheim 1846'), ('Greuther Fuerth', 'SpVgg Greuther Fürth')],
    'ita.1': [('AS Roma', 'Roma'), ('Internazionale', 'Inter')],
    'fra.1': [('AJ Auxerre', 'Auxerre'), ('AS Monaco', 'Monaco'), ('Le Havre AC', 'Le Havre'),
              ('Paris Saint-Germain', 'Paris SG'), ('Stade Rennais', 'Rennes')],
}


def _competition(value):
    return LEAGUES.get(value, value)


def _source_day(value):
    """Keep the calendar date printed by Understat, whose raw clock has no zone."""
    return date.fromisoformat(str(value)[:10])


def _target_day(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if isinstance(value, datetime):
        if value.utcoffset() is not None:
            value = value.astimezone(timezone.utc)
        return value.date()
    return value


def _name_key(name):
    if not isinstance(name, str) or not any(c.isalpha() for c in name):
        return ''
    return ''.join(c for c in unicodedata.normalize('NFKD', name.casefold()) if c.isalnum())


def _source_id(value):
    if isinstance(value, (int, str)) and not isinstance(value, bool):
        return str(value).strip() or None
    return None


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return value if isfinite(value) and value >= 0 else None


def _values(row, side):
    xg, deep, att, actions = (_number(row.get(f'{side}_{field}'))
                              for field in ('xg', 'deep', 'ppda_att', 'ppda_def'))
    if att is None or actions is None:
        att = actions = None
    return xg, deep, att, actions


def _oriented(own, opponent):
    return np.array([own[0], opponent[0], own[1], opponent[1], own[2], own[3]])


def _recent(entries, start, stop):
    """Last eight dated entries in [start, stop), including games with missing metrics."""
    end = bisect_left(entries, (stop,))
    begin = max(bisect_left(entries, (start,), 0, end), end - LAST_GAMES)
    return entries[begin:end]


class _Fixture(NamedTuple):
    day: date
    match_id: str | None
    ids: tuple
    names: tuple
    values: tuple
    sha256: str | None


class _Match(NamedTuple):
    team_id: str | None
    method: str
    signature: dict | None = None


class TerritoryHistory:
    """One competition's Understat observations and its accumulated R5 calendar.

    A game supplies date, home and away (R5 names), and optionally competition.
    walk returns float [N, 2, 6] and a list in input order. context returns one
    [2, 6] design and the same prematch metadata, without adding a calendar slot.
    Date windows are relative to the fixture. The earlier of cutoff and fixture
    date is exclusive for metrics, source names/IDs and both calendars.

    Metadata keys are competition, date, cutoff, available and, for each side,
    {side}_id, _name (R5), _source_name, _source_name_date (last observed raw
    spelling), _method, _signature, _history_dates, _exposure and _status.
    Methods are exact, alias, calendar, unmatched, ambiguous or competition_mismatch.
    A calendar search records games, matches and runner_up. Exposure gives usable
    game counts for METRICS. A resolved team has status full, partial, missing_metrics
    or stale. An unresolved team keeps its method as status. available means any
    observed component on either side, including an observed zero residual.
    audit_fixture is the separate evaluation-time fixture lookup.
    """

    def __init__(self, records, competition):
        self.competition = _competition(competition)
        self._aliases = defaultdict(set)
        for group in _SPELLINGS.get(self.competition, ()):
            names = set(map(_name_key, group))
            for name in names:
                self._aliases[name].update(names - {name})
        self._first_seen = defaultdict(dict)   # normalized raw name -> ID -> first date
        self._raw_names = defaultdict(list)    # ID -> dated original spellings
        self._source_slots = defaultdict(set)  # (day, venue) -> competing IDs
        self._history = defaultdict(list)      # ID -> dated observed/expected/count arrays
        self._r5_calendar = defaultdict(list)  # original R5 team key -> sorted unique slots
        self._fixtures = defaultdict(list)
        seen = {}
        for row in records:
            if _competition(row.get('competition')) != self.competition or not row.get('date'):
                continue
            event = _Fixture(_source_day(row['date']), _source_id(row.get('source_match_id')),
                             tuple(_source_id(row.get(side + '_id')) for side in SIDES),
                             tuple(row.get(side) for side in SIDES),
                             tuple(_values(row, side) for side in SIDES), row.get('source_sha256'))
            if event.match_id is not None:
                if event.match_id in seen:
                    if seen[event.match_id] != event:
                        raise ValueError('conflicting Understat fixture: ' + event.match_id)
                    continue
                seen[event.match_id] = event
            self._fixtures[event.day].append(event)

        totals = np.tile(40 * VENUE_PRIOR, (2, 1))
        counts = np.full((2, 4), 40.)
        for day, fixtures in sorted(self._fixtures.items()):
            expected = totals / counts  # fixed for all fixtures on this source day
            for event in sorted(fixtures, key=lambda e: (e.match_id or '', e.ids[0] or '', e.ids[1] or '')):
                values = np.asarray(event.values, dtype=float)
                valid = np.isfinite(values)
                observed = np.where(valid, values, 0.)
                for side, team in enumerate(event.ids):
                    if team is None:
                        continue
                    self._source_slots[day, SIDES[side]].add(team)
                    name = event.names[side]
                    key = _name_key(name)
                    if key:
                        self._first_seen[key].setdefault(team, day)
                        self._raw_names[team].append((day, name))
                    other = 1 - side
                    exposure = _oriented(valid[side], valid[other])
                    stats = np.stack((_oriented(observed[side], observed[other]),
                                      exposure * _oriented(expected[side], expected[other]), exposure))
                    self._history[team].append((day, stats))
                totals += observed
                counts += valid

    def _match(self, name, start, stop):
        key = _name_key(name)
        if not key:
            return _Match(None, 'unmatched')
        exact = {team for team, first in self._first_seen.get(key, {}).items() if first < stop}
        candidates = set(exact)
        for alias in self._aliases.get(key, ()):
            candidates.update(team for team, first in self._first_seen.get(alias, {}).items() if first < stop)
        if len(candidates) == 1:
            return _Match(next(iter(candidates)), 'exact' if exact else 'alias')

        # Distinct R5 spellings can normalize alike. Only this R5 team's own
        # archived calendar can supply the evidence for a source ID.
        slots = _recent(self._r5_calendar.get(name, ()), start, stop)
        votes = Counter()
        for slot in slots:
            votes.update(self._source_slots.get(slot, ()))
        leaders = votes.most_common(2)
        winner, best = leaders[0] if leaders else (None, 0)
        runner_up = leaders[1][1] if len(leaders) > 1 else 0
        signature = dict(games=len(slots), matches=best, runner_up=runner_up)
        if best >= 4 and 4 * best >= 3 * len(slots):
            if best - runner_up >= 2 and (not candidates or winner in candidates):
                return _Match(winner, 'calendar', signature)
            return _Match(None, 'ambiguous', signature)
        return _Match(None, 'ambiguous' if candidates else 'unmatched', signature)

    def _profile(self, match, start, stop):
        past = _recent(self._history.get(match.team_id, ()), start, stop)
        names = self._raw_names.get(match.team_id, ())
        last_name = bisect_left(names, (stop,)) - 1
        name_date, raw = names[last_name] if last_name >= 0 else (None, None)
        meta = dict(id=match.team_id, source_name=raw, source_name_date=name_date.isoformat() if name_date else None,
                    method=match.method, signature=match.signature, history_dates=[d.isoformat() for d, _ in past],
                    exposure=dict.fromkeys(METRICS, 0), status=match.method)
        if not past:
            if match.team_id is not None:
                meta['status'] = 'stale'
            return np.zeros(5), meta
        observed, expected, exposure = np.sum([stats for _, stats in past], axis=0)
        pseudo = 4 * _oriented(VENUE_PRIOR, VENUE_PRIOR)
        log_ratios = np.log((observed + pseudo) / (expected + pseudo))
        # log(expected PPDA / observed PPDA), using paired summed counts.
        profile = np.append(log_ratios[:4], log_ratios[5] - log_ratios[4])
        meta['exposure'] = dict(zip(METRICS, map(int, exposure[:5]), strict=True))
        meta['status'] = ('full' if np.all(exposure == len(past)) else
                          'partial' if np.any(exposure) else 'missing_metrics')
        return np.clip(profile, -1., 1.), meta

    def context(self, game, cutoff=None):
        target = _target_day(game['date'])
        stop = min(target, _target_day(cutoff)) if cutoff is not None else target
        code = _competition(game.get('competition', self.competition))
        if code != self.competition or code not in LEAGUES.values():
            matches = [_Match(None, 'competition_mismatch')] * 2
        else:
            matches = [self._match(game.get(side), target - CALENDAR_WINDOW, stop) for side in SIDES]
            if matches[0].team_id is not None and matches[0].team_id == matches[1].team_id:
                priority = {'exact': 3, 'alias': 2, 'calendar': 1}
                weakest = min(priority[m.method] for m in matches)
                matches = [m._replace(team_id=None, method='ambiguous') if priority[m.method] == weakest else m
                           for m in matches]
        meta = dict(competition=code, date=target.isoformat(), cutoff=stop.isoformat(), available=False)
        profiles = []
        for side, match in zip(SIDES, matches, strict=True):
            profile, detail = self._profile(match, target - FORM_WINDOW, stop)
            profiles.append(profile)
            meta[side + '_name'] = game.get(side)
            meta.update((f'{side}_{key}', value) for key, value in detail.items())
            meta['available'] |= any(detail['exposure'].values())
        home, away = profiles
        design = [[own[0], opp[1], own[2], opp[3], own[4], opp[4]] for own, opp in ((home, away), (away, home))]
        return np.asarray(design, dtype=float), meta

    def walk(self, games):
        """Predict a whole date, then add its R5 names and venues. Replays are idempotent."""
        games = list(games)
        by_day = defaultdict(list)
        for index, game in enumerate(games):
            by_day[_target_day(game['date'])].append(index)
        design = np.empty((len(games), 2, len(FEATURES)), dtype=float)
        metadata = [None] * len(games)
        for day, indices in sorted(by_day.items()):
            for index in indices:
                design[index], metadata[index] = self.context(games[index])
            for index in indices:
                game = games[index]
                if _competition(game.get('competition', self.competition)) != self.competition:
                    continue
                for side in SIDES:
                    name = game.get(side)
                    if _name_key(name):
                        calendar = self._r5_calendar[name]
                        slot = day, side
                        pos = bisect_left(calendar, slot)
                        if pos == len(calendar) or calendar[pos] != slot:
                            calendar.insert(pos, slot)
        return design, metadata

    def audit_fixture(self, game, metadata):
        """Compare the frozen prematch IDs to the source date and ordered pair."""
        day = _target_day(game['date'])
        code = _competition(game.get('competition', self.competition))
        if (metadata['competition'], metadata['date'], metadata['home_name'], metadata['away_name']) != (
                code, day.isoformat(), game.get('home'), game.get('away')):
            raise ValueError('metadata belongs to a different target fixture')
        ids = metadata['home_id'], metadata['away_id']
        fixtures = self._fixtures.get(day, ())
        pairs = [f for f in fixtures if f.ids == ids]
        status = 'matched'
        if code != self.competition or None in ids:
            status = 'unresolved'
        elif not fixtures:
            status = 'date_absent'
        elif not pairs:
            status = 'disagreement' if any(set(ids).intersection(f.ids) for f in fixtures) else 'pair_absent'
        elif len(pairs) > 1:
            status = 'ambiguous'
        event = pairs[0] if status == 'matched' else None
        return dict(status=status, source_match_id=event.match_id if event else None,
                    source_sha256=event.sha256 if event else None)
