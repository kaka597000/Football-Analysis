"""An independently trained joint score ranker anchored on valid market evidence."""

from functools import lru_cache

import lightgbm as lgb
import numpy as np

from research.analysis_freeze import aware, digest
from .elo_kelly_shadow import devig
from .kpi_score_candidate import FEATURE_NAMES, LEAGUES, form_features
from .live_score_form import side_form
from .market_baseline import _paired_capture, predict as market_forecast, quote_only_matrices
from .score_form import cell_index
from .three_arm_model import score_picks, top_three_scores

VERSION = 'WCPP-MARKET-SCORE-RANKER-1'
SHAPE = (25, 25)
PRICES = ('home', 'draw', 'away')
WDL_TIE_TOLERANCE = 1e-12
FRACTIONS = (0.25, 0.50)
ARMS = ('market', 'consensus', 'market_rank_25', 'market_rank_50',
        'form_rank_25', 'form_rank_50', 'path_rank_25', 'path_rank_50')


def stable_wdl(probabilities):
    """Use one fixed tie-order so floating-point rewrites do not change tied picks."""
    p = np.asarray(probabilities)
    return (p >= p.max(axis=-1, keepdims=True) - WDL_TIE_TOLERANCE).argmax(axis=-1)


def market_features(main, alternatives):
    main_wdl, _ = devig(main['h2h'])
    line = float(main['total']['line'])
    # Keep one estimate per bookmaker and use only matches at the common line.
    books = {main['bookmaker']: main}
    books.update({k: v for k, v in alternatives.items() if v['total']['line'] == line and k not in books})
    vector = []

    def over(total):
        return total['under'] / (total['under'] + total['over'])

    for snapshot in books.values():
        wdl, _ = devig(snapshot['h2h'])
        vector.append([*[wdl[name] for name in PRICES], over(snapshot['total'])])
    prices = np.asarray(vector)
    average = prices.mean(axis=0)
    spread = prices.std(axis=0)
    main_row = [*[main_wdl[k] for k in PRICES], over(main['total']), line]
    probability_features = np.asarray([*main_row, *average, *spread, np.log1p(len(books))])
    consensus = {'h2h': {k: float(1/average[i]) for i, k in enumerate(PRICES)},
                 'total': {'line': line, 'over': float(1/average[3]), 'under': float(1/(1-average[3]))}}
    return probability_features, consensus


def normalize_market(market):
    _, matrix = quote_only_matrices(market['h2h'], market['total'])
    part = matrix[:SHAPE[0], :SHAPE[1]]
    if 1-part.sum() > 1e-8:
        raise ValueError('market score distribution outside ranker support')
    return part / part.sum()


def design_row(cohort, main, alternatives, form):
    values, consensus = market_features(main, alternatives)
    comp = [int(cohort==code) for code in LEAGUES]
    form_values = form[:,len(LEAGUES)+1:].ravel()
    return np.hstack((values,comp)), np.hstack((values,comp,form_values)), consensus


def historical_path(main, stages):
    """Source-labeled archival market movement, with missing dimensions explicit."""
    opening = stages.get('open') or {}
    closing = stages.get('close') or {}
    if 'h2h' in opening:
        start, _ = devig(opening['h2h'])
        end, _ = devig(main['h2h'])
        changes = [end[k]-start[k] for k in PRICES]
    else:
        changes = [np.nan]*3
    old_total = opening.get('total')
    if old_total and old_total['line']==main['total']['line']:
        def implied(total):
            return total['under'] / (total['under']+total['over'])
        changes.append(implied(main['total'])-implied(old_total))
    else:
        changes.append(np.nan)
    handicap = closing.get('handicap') or {}
    handicap_line = handicap.get('home_line', np.nan)
    fraction = (handicap['away']/(handicap['away']+handicap['home'])
                if {'home','away'} <= handicap.keys() else np.nan)
    return np.asarray([*changes, handicap_line, fraction])


def _redistribute(anchors, class_masses):
    """Preserve within-class exact score shape for all 4+ goals tails."""
    lookup = cell_index(anchors.shape[-2:])
    p = np.asarray(class_masses, dtype=float)
    if p.shape != (len(anchors),25) or not np.isfinite(p).all() or p.min()<0 or not np.allclose(p.sum(axis=1),1):
        raise ValueError('malformed joint score class probabilities')
    totals = np.stack([anchors[:,lookup==i].sum(axis=1) for i in range(25)],axis=1)
    scales = p / totals
    m = anchors * scales[:,lookup]
    return m / m.sum(axis=(1,2),keepdims=True)


@lru_cache(maxsize=2)
def learner(text):
    return lgb.Booster(model_str=text)


def mix(anchors, model_text, features, fraction):
    if fraction not in FRACTIONS:
        raise ValueError('unknown fitted mixture')
    rank = learner(model_text).predict(features, num_threads=1)
    return (1-fraction)*anchors+fraction*_redistribute(anchors,rank)


def artifact_identity(artifact):
    return digest({k:v for k,v in artifact.items() if k!='artifact_sha256'})


def simultaneous_peers(fixture, quote):
    """Use only other books on the audited provider event, line and response."""
    pairs = {}
    blocked = set()
    for row in fixture.get('market_snapshots', []):
        if (not isinstance(row, dict) or row.get('market')!='totals'
                or row.get('provider_match_id')!=quote['provider_match_id']
                or row.get('source_sha256')!=quote['source_sha256']
                or row.get('line')!=quote['total']['line']):
            continue
        addr = row.get('bookmaker')
        if not isinstance(addr,str) or not addr.strip():
            continue
        book = addr.strip().casefold()
        if book==quote['bookmaker'] or book in blocked:
            continue
        odds = row.get('decimal_odds') or {}
        if not isinstance(odds,dict) or set(odds)!= {'over','under'}:
            continue
        try:
            if aware(row['query_time'])!=aware(quote['query_time']):
                continue
            total = dict(bookmaker=book,provider_match_id=row['provider_match_id'],line=row['line'],
                         query_time=row['query_time'],quoted_at=row['quoted_at'],
                         source_sha256=row['source_sha256'],over_odds=odds['over'],under_odds=odds['under'])
            pair, reason = _paired_capture(fixture,total)
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        if reason:
            blocked.add(book)
            pairs.pop(book,None)
            continue
        pairs[book] = {'h2h':pair['h2h']['decimal_odds'],
                       'total':dict(pair['total']['decimal_odds'],line=row['line'])}
    return pairs


def predict(artifact, fixture):
    base={'version':VERSION,'artifact_sha256':artifact['artifact_sha256'],'match_id':fixture['match_id']}
    book_scope = artifact.get('book_scope','single')
    if book_scope not in ('single','capture_consensus'):
        raise ValueError('unrecognized ranker market scope')
    if (artifact['version']!=VERSION or artifact_identity(artifact)!=artifact['artifact_sha256']
            or artifact['arm'] not in ARMS):
        raise ValueError('ranker artifact integrity or arm mismatch')
    if (fixture.get('cohort') not in LEAGUES or fixture.get('stage')!='regular'
            or fixture.get('score_basis')!='regulation_90'):
        return dict(base,status='ABSTAIN',reasons=['OUTSIDE_TRAINED_SCOPE'])
    cutoff,kickoff=[aware(fixture[k]) for k in ('cutoff_at','kickoff_at')]
    if not aware(artifact['built_at'])<=cutoff<kickoff or artifact['selection_end']>=kickoff.date().isoformat():
        raise ValueError('invalid prediction cutoff')
    market=market_forecast(fixture)
    if market['status']!='AVAILABLE':
        return dict(base,status='ABSTAIN',reasons=market['reasons'])
    quote=market['quote_snapshot']
    line=quote['total']['line']
    main={'bookmaker':quote['bookmaker'],'h2h':quote['h2h']['prices'],'total':dict(quote['total']['prices'],line=line)}
    arm=artifact['arm']
    form=np.full((2,len(FEATURE_NAMES)),np.nan)
    fallback=None
    if arm.startswith('path_rank'):
        fallback=arm
        arm=arm.replace('path_rank','form_rank')
    if arm.startswith('form_rank'):
        snapshot=fixture.get('recent_league_form')
        if snapshot and snapshot.get('cohort')==fixture['cohort'] and snapshot.get('match_id')==fixture['match_id']:
            home,away=[side_form(fixture,snapshot,k) for k in ('home','away')]
            if min(home['n'],away['n']):
                form=form_features(fixture['cohort'],home,away,fixture['neutral'])
        if np.isnan(form).all():
            fallback=fallback or arm
            arm=arm.replace('form_rank','market_rank')
    peers = simultaneous_peers(fixture,quote) if book_scope=='capture_consensus' else {}
    x_market,x_form,consensus=design_row(fixture['cohort'],main,peers,form)
    distribution=normalize_market(consensus if peers and arm!='market' else main)
    if arm not in ('market','consensus'):
        is_form=arm.startswith('form_rank')
        x=x_form if is_form else x_market
        fraction=int(arm.rsplit('_',1)[-1])/100
        distribution=mix(distribution[None,:,:],artifact['form_model' if is_form else 'market_model'],x[None,:],fraction)[0]
    numerics=score_picks(distribution,line=line)
    numerics['wdl']=PRICES[stable_wdl([numerics['wdl_probabilities'][d] for d in PRICES])]
    return dict(base, **numerics, status='AVAILABLE',top3=top_three_scores(distribution),arm=arm,
                fallback_from=fallback,quote_snapshot_sha256=market['quote_snapshot_sha256'],
                native_quote_books=1+len(peers),configured_book_scope=book_scope,matrix=distribution.tolist(),matrix_sha256=digest(distribution.tolist()))
