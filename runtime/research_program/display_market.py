"""Pair original J1 authored selections with the market snapshot issued beside them."""
from zoneinfo import ZoneInfo

from research.analysis_freeze import aware
from .market_baseline import quote_only_matrices
from .paired_kpi_inference import compare
from .three_arm_model import score_picks, top_three_scores

WDL = {'主胜':'home', '平局':'draw', '平':'draw', '客胜':'away'}
TOTAL = {'大':'over', '小':'under'}
SIDES = ('home', 'draw', 'away')


def _market_forecast(h2h, quote):
    line = quote['line']
    _, matrix = quote_only_matrices(h2h, dict(line=line, over=quote['over_odds'], under=quote['under_odds']))
    # Reproduce the support used by the first J1 display contract.
    matrix = matrix[:25, :25].copy()
    retained = matrix.sum()
    if 1. - retained > 1e-8:
        raise ValueError('material market tail outside score support')
    matrix /= retained
    picks = score_picks(matrix, line=line)
    wdl = picks['wdl_probabilities']
    winner = next(side for side in SIDES if wdl[side] >= max(wdl.values()) - 1e-12)
    return dict(primary=picks['primary'], top3=top_three_scores(matrix), wdl=winner,
                total_side=picks['total']['direction'], total_line=line)


def comparisons(per_match, records):
    """Use the actual and quote verifications already completed by display_window_review."""
    rows = []
    strict = []
    for review in per_match:
        mid = review['match_id']
        record = records.get(mid)
        p = record['payload'] if record else {}
        line_audit = review['quote']
        actual = review.get('actual_result')
        if actual is not None and not review.get('actual_result_source_sha256'):
            raise ValueError('market comparison requires the certified regulation source')
        line = p.get('total_line') if line_audit.get('raw_source_verified') else None
        authored = dict(primary=p.get('primary'), top3=p.get('displayed_scores') or None,
                        wdl=WDL.get(p.get('wdl')),total_side=TOTAL.get(p.get('total_direction')) if line is not None else None,
                        total_line=line)
        snapshot = p.get('market_snapshot') or {}
        market = {}
        if line is not None and snapshot.get('h2h') and snapshot.get('quote'):
            q = snapshot['quote']
            if q['line'] != line or snapshot['quote'] != record['fixture'].get('total_quote'):
                raise ValueError('paired market differs from the original quote')
            market = _market_forecast(snapshot['h2h'], q)
            if market != snapshot['forecast']:
                raise ValueError('same-book market forecast differs from the signed first issue')
        day = aware(review['kickoff_at']).astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
        row = dict(id=mid,date=day,cohort='jpn.1',actual=list(map(int,actual.split('-'))) if actual else None,
                   line=line,forecast=dict(authored=authored,market=market))
        rows.append(row)
        public = authored.copy() if review['confirmed_first_score'] else {}
        verified_market = market if line_audit['strict'] else {}
        if not line_audit['strict']:
            public.pop('total_side',None)
        strict.append(dict(row,forecast=dict(authored=public,market=verified_market)))
    return {name:compare(group,baseline='market',candidate='authored')
            for name,group in [('first_frozen_rows',rows),('channel_confirmed_rows',strict)]}
