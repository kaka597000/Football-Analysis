"""Recover W/D/L and identifiable Asian totals from an entire HKJC correct-score book."""

from collections import Counter
from itertools import count
from math import isfinite

from optimization.evaluate import settle_ou
from .displayed_exact_scores import SCORE, _decimal

VERSION = 'WCPP-DISPLAYED-MARKET-DIRECTIONS-1'
GROUPS = {'HO': 'home', 'OHN': 'home', 'DO': 'draw', 'AO': 'away', 'OAN': 'away'}
OUTCOMES = ('FULL_WIN','HALF_WIN','PUSH','HALF_LOSS','FULL_LOSS')


def _group(home, away):
    if home == away:
        return 'DO'
    if home > away:
        return 'OHN' if away == 0 else 'HO'
    return 'OAN' if home == 0 else 'AO'


def _minimum_remaining(exact):
    minima = {}
    for total in count():
        for home in range(total+1):
            away = total-home
            if (home, away) not in exact:
                minima.setdefault(_group(home, away), total)
        if minima.keys() == GROUPS.keys():
            return minima


def predict(offers, groups, line):
    """All nonlisted scores in a bucket must have the same settlement to identify its total."""
    if type(line) not in (int, float) or not isfinite(line) or line < 0 or not (float(line)*4).is_integer():
        raise ValueError('actual quarter-step total required')
    exact = {}
    for offer in offers:
        key = offer['score']
        if not SCORE.fullmatch(key):
            raise ValueError('invalid named score')
        score = tuple(map(int,key.split('-')))
        if score in exact:
            raise ValueError('duplicate named score')
        exact[score] = 1 / _decimal(offer['decimal'])
    other = {g['code']:1/_decimal(g['decimal']) for g in groups}
    if len(exact) != 28 or set(other) != set(GROUPS) or len(groups) != len(other):
        raise ValueError('complete 28 named scores and all five official other outcomes required')
    book = sum(exact.values()) + sum(other.values())
    minima = _minimum_remaining(exact)
    wdl = dict.fromkeys(('home','draw','away'), 0.)
    for (h,a), price in exact.items():
        wdl[GROUPS[_group(h,a)]] += price/book
    for group, price in other.items():
        wdl[GROUPS[group]] += price/book
    out = dict(version=VERSION, wdl=max(wdl, key=wdl.get), wdl_probabilities=wdl, line=line,
               book_percentage=book, other_probability=sum(other.values())/book,
               minimum_unlisted_total_by_group=minima, total_side=None, total_settlements=None)
    # Each other group permits arbitrarily high totals. Once the minimum is a
    # full over win, monotonicity identifies settlement of the entire group.
    if any(settle_ou(value,line,'over') != 'FULL_WIN' for value in minima.values()):
        return out
    totals = {}
    for side in ('over','under'):
        settled = Counter()
        for (h,a), price in exact.items():
            settled[settle_ou(h+a,line,side)] += price/book
        settled['FULL_WIN' if side=='over' else 'FULL_LOSS'] += sum(other.values())/book
        totals[side] = {label:settled[label] for label in OUTCOMES}
    over, under = totals['over'], totals['under']
    side = 'over'
    for diff in (over['FULL_WIN']-under['FULL_WIN'], over['HALF_WIN']-under['HALF_WIN']):
        if abs(diff)>1e-12:
            side = 'over' if diff>0 else 'under'
            break
    return dict(out, total_side=side, total_settlements=totals)
