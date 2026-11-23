"""Rank the exact scores a bookmaker displayed together on a regulation-time page."""

from collections import Counter
from math import isfinite, log
import re

from .market_baseline import quote_only_matrices
from .three_arm_model import score_picks, top_three_scores

VERSION = 'WCPP-DISPLAYED-EXACT-SCORES-1'
SCORE = re.compile(r'(0|[1-9][0-9]?)-(0|[1-9][0-9]?)')


def _decimal(value):
    if type(value) not in (float, int) or not isfinite(value) or value <= 1:
        raise ValueError('positive decimal price over one required')
    return value


def compare(offers, grouped, *, h2h=None, totals=()):
    """Grouped 'other' outcomes stay grouped, even when shorter than a singleton."""
    prices = {}
    for offer in offers:
        score = offer['score']
        if not SCORE.fullmatch(score) or score in prices:
            raise ValueError('one price per explicit score required')
        prices[score] = _decimal(offer['decimal'])
    if len(prices) < 3:
        raise ValueError('three distinct displayed scores required')
    other = {r['label']:_decimal(r['decimal']) for r in grouped}
    if len(other) != len(grouped):
        raise ValueError('ambiguous grouped selection')

    def coords(score):
        return tuple(map(int,score.split('-')))

    matrix = None
    inferred = None
    if h2h is not None and totals:
        line = min(totals,key=lambda t:(abs(log(_decimal(t['over'])/_decimal(t['under']))),t['line']))
        _, matrix = quote_only_matrices(h2h,line)
        inferred = dict(score_picks(matrix,line=line['line']), top3=top_three_scores(matrix),
                        source_total=dict(line))

    def ranking(score):
        h,a = coords(score)
        p = matrix[h,a] if matrix is not None and h < matrix.shape[0] and a < matrix.shape[1] else 0.
        return prices[score], -p, h, a

    ordered = sorted(prices,key=ranking)
    lexical = sorted(prices,key=lambda s:(prices[s],coords(s)))
    first_three = ordered[:3]
    counts = Counter(prices.values())
    return {'version':VERSION, 'primary':first_three[0], 'top3':first_three, 'lexical_top3':lexical[:3],
            'ranked_singles':[[s,prices[s]] for s in ordered],
            'price_tie_counts':[counts[prices[s]] for s in first_three],
            'grouped_offers':other,
            'other_buckets_at_or_shorter_than_third':[k for k,v in other.items() if v <= prices[first_three[-1]]],
            'inferred_same_page':inferred}
