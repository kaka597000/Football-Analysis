"""Pair original J1 authored selections with the market snapshot issued beside them."""
from zoneinfo import ZoneInfo

from research.analysis_freeze import aware
from . import all_scope_model as scores
from .paired_kpi_inference import compare

WDL = {'主胜':'home', '平局':'draw', '平':'draw', '客胜':'away'}
TOTAL = {'大':'over', '小':'under'}


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
            odds = tuple(snapshot['h2h'][side] for side in scores.SIDES)
            matrix = scores._full_market(odds,line,(q['over_odds'],q['under_odds']))
            market = scores.pick(matrix,line)
            if market != snapshot['forecast']:
                raise ValueError('same-book market forecast differs from the signed first issue')
        day = aware(review['kickoff_at']).astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
        row = dict(id=mid,date=day,cohort='jpn.1',actual=list(map(int,actual.split('-'))) if actual else None,
                   line=line,forecast=dict(authored=authored,market=market))
        rows.append(row)
        public = authored.copy() if review['confirmed_first_score'] else {}
        verified_market = market.copy()
        if not line_audit['strict']:
            public.pop('total_side',None)
            verified_market.pop('total_side',None)
        strict.append(dict(row,forecast=dict(authored=public,market=verified_market)))
    return {name:compare(group,baseline='market',candidate='authored')
            for name,group in [('first_frozen_rows',rows),('channel_confirmed_rows',strict)]}
