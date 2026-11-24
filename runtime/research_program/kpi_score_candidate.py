"""Market and recent-form candidate with portable, source-dated fitted weights."""

from datetime import date
from functools import lru_cache
from math import log1p

import lightgbm as lgb
import numpy as np
from scipy.special import softmax

from research.analysis_freeze import aware, digest
from .market_baseline import predict as market_forecast, quote_only_matrices
from .score_form import cell_index, poisson_matrix
from .three_arm_model import score_picks, top_three_scores

VERSION = 'WCPP-KPI-SCORE-1'
LEAGUES = ('eng.1', 'esp.1', 'ger.1', 'ita.1', 'fra.1')
SHAPE = (21, 21)
FORM_FIELDS = ('gf', 'ga', 'xgf', 'xga', 'points', 'btts', 'over25', 'n', 'xg_n')
TILTS = ('market_tilt', 'blend_25', 'blend_50')
RECIPES = ('market', 'market_tilt', 'form_goals', 'form_xg', 'blend_25', 'blend_50')
FEATURE_NAMES = ('home_advantage', *LEAGUES,
                 *(f'{side}_{field}' for side in ('own', 'opponent') for field in (*FORM_FIELDS, 'gap')))


def form_features(cohort, home, away, neutral=False):
    league = tuple(int(cohort == league) for league in LEAGUES)

    def row(venue, own, opponent):
        values = [float(venue), *league]
        for side in (own, opponent):
            values.extend(side[k] for k in FORM_FIELDS)
            values.append(log1p(side['gap']))
        return values

    return np.asarray([row(not neutral, home, away), row(False, away, home)])


def mask_xg(x):
    """Use the same goals-only feature encoding at fit and at forward inference."""
    for prefix in ('own','opponent'):
        def ix(name):
            return FEATURE_NAMES.index(prefix+'_'+name)
        x[:,:,ix('xgf')]=x[:,:,ix('gf')]
        x[:,:,ix('xga')]=x[:,:,ix('ga')]
        x[:,:,ix('xg_n')]=0.


def market_context(matrices):
    h, a = np.indices(matrices.shape[-2:])
    goals = np.sum(matrices * (h + a), axis=(-2, -1))
    margins = np.sum(matrices * (h - a), axis=(-2, -1))
    return np.column_stack((np.ones(len(matrices)), goals - 2.7, margins, np.abs(margins) - .65))


def recalibrate(matrices, coefficients):
    cell = cell_index(matrices.shape[-2:])
    offsets = market_context(matrices) @ np.asarray(coefficients)
    logp = np.log(np.maximum(matrices, 1e-250)) + offsets[:, cell]
    return softmax(logp.reshape(len(matrices), -1), axis=1).reshape(matrices.shape)


def league_bucket(cohort):
    return cohort in LEAGUES


@lru_cache(maxsize=4)
def booster(model_text):
    return lgb.Booster(model_str=model_text)


def expected_form(model_text, features):
    values = booster(model_text).predict(features.reshape(-1, features.shape[-1]), num_threads=1)
    return np.clip(values.reshape(-1, 2), .04, 7.)


def quotes_matrix(snapshot):
    market = snapshot['market']
    _, matrix = quote_only_matrices(market['h2h'], market['total'])
    support = matrix[:SHAPE[0], :SHAPE[1]]
    if 1. - support.sum() > 1e-8:
        raise ValueError('material market mass outside model score support')
    return support / support.sum()


def artifact_identity(artifact):
    return digest({k: v for k, v in artifact.items() if k != 'artifact_sha256'})


def predict(artifact, fixture):
    """Forward-only call. Keep a market challenger when recent form is unavailable."""
    if (artifact.get('version') != VERSION or artifact_identity(artifact) != artifact.get('artifact_sha256')
            or artifact['selected_recipe'] not in RECIPES):
        raise ValueError('invalid candidate artifact')
    base = {'version': VERSION, 'artifact_sha256': artifact['artifact_sha256']}
    if (not league_bucket(fixture['cohort']) or fixture.get('stage') != 'regular'
            or fixture.get('score_basis') != 'regulation_90'):
        return dict(base, status='ABSTAIN_OUTSIDE_TRAINED_SCOPE')
    cutoff, kickoff = (aware(fixture[k]) for k in ('cutoff_at', 'kickoff_at'))
    if not aware(artifact['built_at']) <= cutoff < kickoff or artifact['selection_end'] >= kickoff.date().isoformat():
        raise ValueError('candidate publication or selection cutoff')
    market = market_forecast(fixture)
    m = None
    if market['status'] == 'AVAILABLE':
        _, full = quote_only_matrices(market['quote_snapshot']['h2h']['prices'],
                                    dict(market['quote_snapshot']['total']['prices'], line=market['quote_snapshot']['total']['line']))
        m = full[:SHAPE[0], :SHAPE[1]]
        m /= m.sum()
    recipe = artifact['selected_recipe']
    form = None
    if fixture.get('recent_league_form') and (recipe != 'market' and recipe != 'market_tilt' or m is None):
        from .live_score_form import side_form
        snapshot = fixture['recent_league_form']
        if (snapshot.get('cohort'), snapshot.get('match_id')) != (fixture['cohort'], fixture['match_id']):
            raise ValueError('recent form scope mismatch')
        h, a = [side_form(fixture, snapshot, key) for key in ('home', 'away')]
        if min(h['n'], a['n']):
            feature_row = form_features(fixture['cohort'], h, a, fixture['neutral'])[None, :, :]
            arm = 'form_goals' if recipe == 'form_goals' or not min(h['xg_n'], a['xg_n']) else 'form_xg'
            if arm == 'form_goals':
                mask_xg(feature_row)
            form = poisson_matrix(expected_form(artifact[arm], feature_row)[0], SHAPE)
    if m is None and form is None:
        return dict(base, status='ABSTAIN_NO_VERIFIED_MARKET_OR_FORM', quote_status=market)
    if m is None:
        chosen, pathway = form, 'FORM_WITHOUT_LIVE_MARKET'
    elif recipe in ('form_goals', 'form_xg') and form is not None:
        chosen, pathway = form, recipe
    else:
        market_used = recalibrate(m[None, :, :], artifact['tilt'])[0] if recipe != 'market' else m
        if recipe in ('blend_25','blend_50') and form is not None:
            form_fraction = .25 if recipe == 'blend_25' else .5
            chosen, pathway = (1-form_fraction)*market_used+form_fraction*form, recipe
        else:
            chosen, pathway = market_used, recipe if recipe in ('market','market_tilt') else 'MARKET_WITHOUT_VERIFIED_FORM'
    line = fixture['total_quote']['line'] if m is not None else None
    result = score_picks(chosen, line=line)
    return dict(base, **result, status='AVAILABLE', top3=top_three_scores(chosen),
                effective_recipe=pathway, matrix=chosen.tolist(), matrix_sha256=digest(chosen.tolist()))
