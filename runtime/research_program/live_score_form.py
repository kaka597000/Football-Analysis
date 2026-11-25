"""Apply a source-timed form snapshot to the independent score candidate."""
import json
import math
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

import numpy as np

from research.analysis_freeze import aware, digest
from . import score_form as form
from .three_arm_model import score_picks, top_three_scores
from .future_validation import quote_audit


def side_form(fixture, snapshot, side):
    team=snapshot[side]
    if team['team_id']!=fixture[side]:
        raise ValueError('form team identity mismatch')
    cutoff=aware(fixture['cutoff_at'])
    target=aware(fixture['kickoff_at'])
    if not cutoff<target: raise ValueError('score form requires prematch cutoff')
    seen=set()
    records=[]
    for r in team['matches']:
        kickoff,observed=aware(r['kickoff_at']),aware(r['observed_at'])
        if (not r['id'] or r['id'] in seen or r['id']==fixture['match_id'] or
                r['cohort']!=fixture['cohort'] or r['score_basis']!='regulation_90' or
                not kickoff < observed <= cutoff or kickoff.date()>=target.date()):
            raise ValueError('form competition, fixture, or observation time mismatch')
        if r.get('espn_match_id') == fixture['match_id'] or (
                'home_team_id' in r and 'away_team_id' in r and team['team_id'] not in (r['home_team_id'],r['away_team_id'])):
            raise ValueError('historical event or team identity mismatch')
        if (urlsplit(r['source_url']).scheme not in ('http','https') or not urlsplit(r['source_url']).netloc
                or not re.fullmatch('[0-9a-f]{64}',r['source_sha256'])):
            raise ValueError('form observation source required')
        if any(type(r[k]) is not int or r[k]<0 for k in ('gf','ga')):
            raise ValueError('invalid regulation goals')
        a,b=r.get('xgf'),r.get('xga')
        if (a is None)!=(b is None) or (a is not None and any(type(v) not in (int,float) or not math.isfinite(v) or v<0 or v>30 for v in (a,b))):
            raise ValueError('invalid paired xG')
        if a is not None and r.get('xg_observed_at') is not None:
            if not kickoff<aware(r['xg_observed_at'])<=cutoff:
                raise ValueError('xG outside evidence cutoff')
            if (not re.fullmatch('[0-9a-f]{64}',r['xg_source_sha256']) or
                    urlsplit(r['xg_source_url']).scheme not in ('http','https') or not urlsplit(r['xg_source_url']).netloc):
                raise ValueError('independent xG source required')
        seen.add(r['id'])
        records.append(dict(r,date=kickoff.date().isoformat()))
    records.sort(key=lambda r:aware(r['kickoff_at']),reverse=True)
    return form.summarize(records[:form.WINDOW],target.date())


def update_test_matrix(artifact, fixture, test, home, away):
    from v6 import ml_model

    if (test.get('model_hash')!=ml_model.legacy.digest(artifact) or artifact['cohort']!=fixture['cohort']
            or test['matrix_hash']!=ml_model.legacy.digest(test['matrix']) or artifact['feature_names']!=ml_model.FEATURES):
        raise ValueError('frozen ML artifact or matrix identity mismatch')
    original=np.asarray(test['matrix'])
    rates=ml_model.legacy.rates(artifact['core']['long'],fixture)[:2]
    x=np.asarray(test['ml']['feature_values'],dtype=float).copy()
    names=ml_model.FEATURES
    for idx,(own,opponent) in enumerate(((home,away),(away,home))):
        for prefix,side in [('own_',own),('opponent_',opponent)]:
            if not side['n']: continue
            updates={'recent_scored':side['gf'],'recent_conceded':side['ga'],'recent_ppg':side['points'],
                     'same_cohort_gap':side['gap'],'recent_btts':side['btts'],'recent_over25':side['over25'],
                     'recent_xg':side['raw_xgf'],'recent_xga':side['raw_xga'],'xg_coverage':side['xg_n']/side['n']}
            for key,value in updates.items():
                x[idx,names.index(prefix+key)]=np.nan if value is None else value
    booster=ml_model.lgb.Booster(model_str=artifact['booster_model'])
    rho=artifact['core']['long']['nuisance']['dixon_coles']
    # Reuse the frozen DC contribution; only the ML residual changes.
    original_features=np.asarray(test['ml']['feature_values'],dtype=float)
    previous,_=ml_model.ml_matrix(booster,original_features,rates,rho)
    updated,_=ml_model.ml_matrix(booster,x,rates,rho)
    m=original+artifact['alpha']*(updated-previous)
    if m.min() < -1e-12 or abs(m.sum()-1)>1e-10: raise ValueError('invalid refreshed mixture')
    m=np.maximum(m,0.)
    return m/m.sum()


def candidate(fixture, test, test_artifact, snapshot, fitted=None, *, historical_replay=False):
    from v6 import ml_model

    if (snapshot['match_id']!=fixture['match_id'] or snapshot['cohort']!=fixture['cohort']
            or fixture['stage']!='regular' or fixture['score_basis']!='regulation_90'
            or test['version']!=ml_model.VERSION):
        raise ValueError('regular competition and matching ML/form identities required')
    native_input=test['input']
    if any(native_input[k]!=fixture[k] for k in ('match_id','cohort','stage','home','away','neutral')) or (
            aware(native_input['kickoff_at'])!=aware(fixture['kickoff_at']) or
            aware(native_input['cutoff_at'])>aware(fixture['cutoff_at'])):
        raise ValueError('test and form fixture identities or cutoffs differ')
    if fitted is not None:
        if (fitted['version']!=form.VERSION or fitted['cohort']!=fixture['cohort'] or
                fitted['artifact_sha256']!=digest({k:v for k,v in fitted.items() if k!='artifact_sha256'}) or
                fitted['selection_end']>=fixture['kickoff_at'][:10]):
            raise ValueError('score form artifact identity or selection period')
        if not historical_replay and aware(fitted['built_at'])>aware(fixture['cutoff_at']):
            raise ValueError('score form artifact built after cutoff')
    home,away=[side_form(fixture,snapshot,k) for k in ('home','away')]
    if not (home['n'] and away['n']):
        return {'version':form.VERSION,'status':'INSUFFICIENT_BOTH_TEAM_HISTORIES','formal_kpi_eligible':False}
    fresh=update_test_matrix(test_artifact,fixture,test,home,away)
    fm=form.form_matrix(fitted,home,away,fresh.shape,fixture['neutral'])
    pooled,selections=form.selection_inputs(fresh,fm)
    quote=quote_audit(fixture)
    line=quote['quote']['line'] if quote['eligible'] else None
    def view(m):
        return {**score_picks(m,line=line),'top3':top_three_scores(m),'matrix_sha256':digest(m.tolist())}
    return {'version':form.VERSION,'status':'AVAILABLE','formal_kpi_eligible':False,
            'scope':'POSTMATCH_REPLAY' if historical_replay else 'PREMATCH_RESEARCH',
            'form_basis':'LEAGUE_XG_FIT' if fitted else 'RECENT_XG_SHRUNK_TO_GOALS',
            'form_counts':{k:{s:v[s] for s in ('n','xg_n','gap')} for k,v in [('home',home),('away',away)]},
            'snapshot_sha256':digest(snapshot),'fit_sha256':fitted['artifact_sha256'] if fitted else None,
            'native_test_matrix_sha256':test['matrix_hash'],'refreshed_test':view(fresh),
            'xg_expert':view(fm),'pooled':view(pooled),**selections,
            'selection':form.select_scores(selections)}


def supports_test(test, fixture):
    if not test.get('ml') or fixture.get('stage') != 'regular':
        return False
    from v6 import ml_model
    return test.get('version') == ml_model.VERSION


def attach_candidate(output, fixture, test_artifact, snapshot):
    test=output['test_model']
    if not supports_test(test, fixture):
        output['score_form_candidate']={'version':form.VERSION,'status':'UNSUPPORTED_TEST_ROUTE','formal_kpi_eligible':False}
        return
    try:
        fitted=fixture.get('score_form_artifact')
        if fitted is None and fixture['cohort'] in ('eng.1','ger.1','esp.1','ita.1','fra.1'):
            model_root=Path(os.environ.get('WCPP_RUNTIME_ROOT','/workspace/WCPP'))/'models'/'score-form-v1'
            path=model_root/(fixture['cohort']+'.json')
            if path.is_file():
                fitted=json.loads(path.read_bytes())
        output['score_form_candidate']=candidate(fixture,test,test_artifact,snapshot,fitted)
    except (KeyError, TypeError, ValueError) as exc:
        output['score_form_candidate']={'version':form.VERSION,'status':'INVALID_FORM_EVIDENCE',
                                        'reason':str(exc),'formal_kpi_eligible':False}
