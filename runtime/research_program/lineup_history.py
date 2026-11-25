"""Date-isolated J1 starter and club-form features for R15.

LineupHistory(rosters) accepts an ESPN event-ID mapping. Each available value
has match_id, home_id, away_id, home_starters and away_starters. An eligible
starter list contains eleven {id, position} records from the verified source.
The supplied mapping defines which completed summaries are available.
An absent event or a None side still occupies its club's actual appearance slot.

X, metadata = history.walk(games, anchors) consumes R5 games and their original
R7 input matrices in matching order. X has shape (N, 2, 6) in FEATURES order,
home scorer first. The list of JSON metadata follows the original input order.
history.context(game, cutoff=...) returns (x, meta) for one scheduled fixture
from the ingested appearances. Only dates strictly before both game.date and
cutoff are visible. Window ages are measured from game.date. Bare dates use
the R5 calendar. Zoned timestamps use their UTC date. Optional home_id and
away_id are verified schedule IDs available at prediction time. Both methods
use the scheduled teams and previously observed ID/name links to
resolve the target teams. context needs no target score, anchor or event ID.

meta['home'] and meta['away'] describe each club's own attack and defence.
Each has team_id, club_key, identity_source, form_games, club_games_365,
previous, penultimate, attack and defence. A slot is None outside the 90-day
window, otherwise it reports date, match_ids, status and roster_available.
The two group dictionaries report players, rating_available and
retention_available. both_prior_rosters, personnel_neutral and all_neutral
are fixture-level fields. The two feature rows use opposite clubs' defences.
"""

from bisect import bisect_left, insort
from collections import defaultdict
from collections.abc import Mapping
from datetime import date, datetime, timezone
from itertools import groupby
from typing import NamedTuple
import unicodedata

import numpy as np


VERSION = 'R15-J1-prior-starters-1'
FEATURES = ('own_attack_form', 'opponent_concession_form', 'own_attack_rating',
            'opponent_defensive_concession_rating', 'own_attack_retention', 'opponent_defence_retention')
SIDES = ('home', 'away')


def _day(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if isinstance(value, datetime):
        if value.utcoffset() is not None:
            value = value.astimezone(timezone.utc)
        return value.date()
    if isinstance(value, date):
        return value
    raise ValueError('expected an R5 calendar date or timestamp')


def _id(value):
    if isinstance(value, (str, int, np.integer)) and not isinstance(value, bool):
        return str(value).strip() or None
    return None


def _match_id(value):
    key = _id(value)
    if not key:
        raise ValueError('missing R5 event ID')
    number = key.removeprefix('espn:')
    return 'espn:' + number if number.isascii() and number.isdigit() else key


def _spelling(value):
    if not isinstance(value, str):
        return ''
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().split())


def _normalized(spelling):
    normalized = unicodedata.normalize('NFKD', spelling)
    return ''.join(c for c in normalized if c.isalnum())


def _groups(starters):
    if not isinstance(starters, (list, tuple)) or len(starters) != 11:
        return None
    players = {}
    for entry in starters:
        if not isinstance(entry, Mapping) or entry.get('starter', True) is not True:
            return None
        player = _id(entry.get('id'))
        pos = entry.get('position')
        if not player or player in players or not isinstance(pos, str):
            return None
        pos = pos.strip().upper()
        if pos not in ('M', 'F', 'G', 'D'):
            return None
        players[player] = pos
    return tuple(frozenset(p for p, pos in players.items() if pos in roles) for roles in ('MF', 'GD'))


class _Appearance(NamedTuple):
    day: int
    mid: str
    residual: tuple
    groups: tuple | None


def _slot(recent, offset):
    if len(recent) < offset:
        return None, None
    row = recent[-offset]
    tied = [a.mid for a in recent if a.day == row.day]
    groups = row.groups if len(tied) == 1 else None
    status = 'tied_date' if len(tied) > 1 else 'complete' if groups is not None else 'missing_roster'
    return groups, dict(date=date.fromordinal(row.day).isoformat(), match_ids=tied,
                        status=status, roster_available=groups is not None)


def _personnel(rows, average, latest, previous, role):
    players = latest[role] if latest is not None else ()
    coverage = dict(players=len(players), rating_available=bool(players),
                    retention_available=bool(players) and previous is not None)
    if not players:
        return 0., 0., coverage
    effects = []
    for player in sorted(players):
        # Count all verified starts for this club. The projected role determines
        # which residual to score. Retention uses the player's role in BOTH XIs.
        values = [r.residual[role] for r in rows if r.groups is not None
                  and (player in r.groups[0] or player in r.groups[1])]
        effects.append((sum(values) - len(values) * average) / (len(values) + 8))
    rating = sum(effects) / len(players)
    retention = len(players & previous[role]) / len(players) - .75 if previous is not None else 0.
    return rating, retention, coverage


class LineupHistory:
    def __init__(self, rosters):
        self._rosters = rosters
        self._keys = {_match_id(k): k for k in rosters}
        if len(self._keys) != len(rosters):
            raise ValueError('duplicate normalized roster event ID')
        self._by_id = defaultdict(list)
        self._by_name = defaultdict(list)  # appearances without a directly verified ID
        self._links = defaultdict(dict)    # R5 spelling -> {verified ESPN ID: earliest source day}
        self._variants = defaultdict(set)
        self._games = {}

    def _known_ids(self, spelling, stop):
        exact = {club for club, first in self._links.get(spelling, {}).items() if first < stop}
        if exact:
            return exact
        return {club for alias in self._variants.get(_normalized(spelling), ())
                for club, first in self._links[alias].items() if first < stop}

    def _profile(self, name, scheduled_id, target, stop):
        # A link learned after the cutoff cannot reinterpret an earlier forecast.
        candidates = self._known_ids(name, stop)
        club = scheduled_id
        if club is not None:
            source = 'schedule_id'
        elif len(candidates) == 1:
            club, = candidates
            source = 'prior_id'
        else:
            source = 'ambiguous' if candidates else 'r5_name' if name else 'unknown'
        indexes = []
        if club is not None:
            indexes.append(self._by_id.get(club, ()))
            for spelling, appearances in self._by_name.items():
                if (spelling == name and not candidates) or self._known_ids(spelling, stop) == {club}:
                    indexes.append(appearances)
        elif source == 'r5_name':
            # Unlinked R5 names keep their punctuation. A normalization
            # collision alone cannot join the appearances of two clubs.
            indexes.append(self._by_name.get(name, ()))
        start = target - 365
        rows = sorted((row for idx in indexes for row in idx[bisect_left(idx, (start,)):bisect_left(idx, (stop,))]),
                      key=lambda r: (r.day, r.mid))
        recent = rows[bisect_left(rows, (target - 90,)):]
        # The original event ID breaks a tie at the sixth form slot only. A
        # tied date cannot identify an immediately preceding starting group.
        form_rows = recent[-6:]
        form = [sum(r.residual[i] for r in form_rows) / (4 + len(form_rows)) for i in range(2)]
        latest, last_meta = _slot(recent, 1)
        previous, prev_meta = _slot(recent, 2)
        key = 'espn:' + club if club is not None else 'r5:' + name if source == 'r5_name' else None
        meta = dict(club_key=key, team_id=club, identity_source=source, form_games=len(form_rows),
                    club_games_365=len(rows), previous=last_meta, penultimate=prev_meta)
        ratings, retention = [], []
        for role, group in enumerate(('attack', 'defence')):
            average = sum(r.residual[role] for r in rows) / len(rows) if rows else 0.
            rating, retained, meta[group] = _personnel(rows, average, latest, previous, role)
            ratings.append(rating)
            retention.append(retained)
        return (*form, *ratings, *retention), meta

    def context(self, game, cutoff=None):
        if game.get('competition', 'jpn.1') != 'jpn.1':
            raise ValueError('R15 history requires a J1 fixture')
        target = _day(game['date'])
        stop = min(target, _day(cutoff)) if cutoff is not None else target
        meta = dict(date=target.isoformat(), cutoff=stop.isoformat(),
                    match_id=_id(game.get('id', game.get('match_id'))))
        profiles = []
        for side in SIDES:
            profile, meta[side] = self._profile(_spelling(game.get(side)), _id(game.get(side + '_id')),
                                                 target.toordinal(), stop.toordinal())
            profiles.append(profile)
        home, away = profiles
        x = np.clip([[own[0], opp[1], own[2], opp[3], own[4], opp[5]]
                     for own, opp in ((home, away), (away, home))], -1., 1.)
        meta['both_prior_rosters'] = all(meta[s]['previous'] is not None and meta[s]['previous']['roster_available']
                                         for s in SIDES)
        meta['personnel_neutral'] = not np.any(x[:, 2:])
        meta['all_neutral'] = not np.any(x)
        return x, meta

    def _observation(self, game, anchor, mid, day):
        p = np.asarray(anchor, dtype=float)
        if (p.ndim != 2 or not p.size or not np.isfinite(p).all() or np.any(p < 0.)
                or not np.isclose(p.sum(), 1., atol=0., rtol=1e-7)):
            raise ValueError('invalid chronological R7 input anchor for ' + mid)
        goals = [game[s + '_goals'] for s in SIDES]
        if (game.get('score_basis', 'regulation_90') != 'regulation_90'
                or any(isinstance(g, bool) or not isinstance(g, (int, np.integer)) or g < 0 for g in goals)):
            raise ValueError('expected completed regulation goals for ' + mid)
        means = [p.sum(axis=1) @ np.arange(p.shape[0]), p.sum(axis=0) @ np.arange(p.shape[1])]
        residual = tuple(map(float, np.clip(goals - np.asarray(means), -3., 3.)))
        record = self._rosters[self._keys[mid]] if mid in self._keys else None
        if record is not None and _match_id(record['match_id']) != mid:
            raise ValueError('roster event ID differs from R5 event ' + mid)
        sides = []
        for i, side in enumerate(SIDES):
            name = _spelling(game.get(side))
            scheduled = _id(game.get(side + '_id'))
            verified = _id(record.get(side + '_id')) if record is not None else None
            if scheduled and verified and scheduled != verified:
                raise ValueError('ordered roster and schedule team IDs disagree for ' + mid)
            club = scheduled or verified
            if not (club or name):
                raise ValueError('missing R5 club identity for ' + mid)
            groups = _groups(record.get(side + '_starters')) if verified else None
            row = _Appearance(day, mid, (residual[i], residual[1-i]), groups)
            sides.append((club, name, row))
        if sides[0][0] is not None and sides[0][0] == sides[1][0]:
            raise ValueError('home and away share a verified team ID for ' + mid)
        return sides

    def walk(self, games, anchors):
        """Add completed games by whole date and return features in input order.

        Disjoint batches can extend the history. Replaying an identical event
        is idempotent. Conflicting dates, club identities, residuals or starter
        groups raise before that date is added.
        """
        games = list(games)
        if len(games) != len(anchors):
            raise ValueError('one chronological R7 input anchor is required per J1 fixture')
        ids = [_match_id(g.get('id', g.get('match_id'))) for g in games]
        if len(set(ids)) != len(ids):
            raise ValueError('duplicate R5 event in the supplied panel')
        dates = [_day(g['date']).toordinal() for g in games]
        x, metadata = np.empty((len(games), 2, len(FEATURES))), [None] * len(games)
        for day, group in groupby(sorted(range(len(games)), key=dates.__getitem__), key=dates.__getitem__):
            indices = list(group)
            for i in indices:
                x[i], metadata[i] = self.context(games[i])
            pending = {}
            for i in indices:
                mid = ids[i]
                sides = self._observation(games[i], anchors[i], mid, day)
                if mid not in self._games:
                    pending[mid] = sides
                elif self._games[mid] != sides:
                    raise ValueError('conflicting replay of R5 event ' + mid)
            for mid, sides in pending.items():
                for club, name, row in sides:
                    if club is None:
                        insort(self._by_name[name], row)
                    else:
                        insort(self._by_id[club], row)
                        if name:
                            links = self._links[name]
                            links[club] = min(day, links.get(club, day))
                            self._variants[_normalized(name)].add(name)
                self._games[mid] = sides
        return x, metadata
