"""Continued ML research: expanded audited history, partial process data, temporal tuning."""
import copy
import hashlib
import math
import sys
from pathlib import Path
from datetime import datetime,timezone
import numpy as np
import lightgbm as lgb

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'v6'))
import ml_model as previous
legacy=previous.legacy
VERSION='WCPP-ML-0.2'
EXTRA=['shots_total','shots_total_coverage']
FEATURES=previous.FEATURES+[p+k for p in ('own_','opponent_') for k in EXTRA]
CONFIG={'alpha_candidates':[0.,.25,.5,.75,1.], 'residual_cap':.4,'seed':20261012,
        'candidates':[
            {'name':'shallow','num_leaves':4,'min_data_in_leaf':40,'lambda_l2':20.,'rounds':60},
            {'name':'regular','num_leaves':7,'min_data_in_leaf':25,'lambda_l2':5.,'rounds':70},
            {'name':'regularized','num_leaves':7,'min_data_in_leaf':50,'lambda_l2':20.,'rounds':100},
            {'name':'flexible','num_leaves':15,'min_data_in_leaf':40,'lambda_l2':10.,'rounds':100}],
        'selection':'calibration Top1, then score log loss; final test never selects'}


def code_hash():
    return legacy.digest({'continued':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                          'previous':previous.code_hash()})


def feature_matrix(rows,fixture,rates):
    f=previous.feature_matrix(rows,fixture,rates)
    date=fixture.get('date') or legacy.aware(fixture['kickoff_at']).date().isoformat()
    sides=[]
    for team in (fixture['home'],fixture['away']):
        recent=[r for r in rows if r['date']<date and team in (r['home'],r['away'])][-6:]
        totals=[];sots=[]
        for r in recent:
            evidence=r.get('additional_process_evidence')
            if not evidence:continue
            if not legacy.url(evidence.get('source_url')) or legacy.aware(evidence['query_time'])>legacy.aware(r['observed_at']):
                raise ValueError('additional process provenance invalid')
            side=0 if r['home']==team else 1
            values=r.get('additional_process',{})
            for name,target in [('shots_total',totals),('shots_on_target',sots)]:
                v=values.get(name)
                if v is None:continue
                if len(v)!=2 or any(type(x) not in (int,float) or not math.isfinite(x) or not 0<=x<=100 for x in v):
                    raise ValueError('invalid partial process value')
                target.append(v[side])
        sides.append([float(np.mean(totals)) if totals else float('nan'),len(totals)/max(1,len(recent))])
        # Fill a missing historical SOT feature; do not replace an existing provider series.
        if sots:
            own_index=previous.FEATURES.index('own_recent_shots_on_target')
            opposite_index=previous.FEATURES.index('opponent_recent_shots_on_target')
            own_row=0 if team==fixture['home'] else 1
            if math.isnan(f[own_row,own_index]):f[own_row,own_index]=float(np.mean(sots))
            if math.isnan(f[1-own_row,opposite_index]):f[1-own_row,opposite_index]=float(np.mean(sots))
    return np.concatenate((f,np.array([sides[0]+sides[1],sides[1]+sides[0]])),axis=1)


def rolling_items(rows,targets):
    items,folds=previous.rolling_items(rows,targets)
    for item in items:item['features']=feature_matrix(rows,item['row'],item['rates'])
    return items,folds


def fit(items,params):
    if len(items)<30:raise ValueError('insufficient real feature targets')
    x=np.concatenate([i['features'] for i in items]);y=np.array([i['row'][k] for i in items for k in ('home_goals','away_goals')])
    offset=np.log(np.array([v for i in items for v in i['rates']]))
    settings={k:params[k] for k in ('num_leaves','min_data_in_leaf','lambda_l2')}
    settings.update(objective='poisson',learning_rate=.03,seed=CONFIG['seed'],num_threads=1,
                    deterministic=True,force_col_wise=True,verbosity=-1)
    return lgb.train(settings,lgb.Dataset(x,label=y,init_score=offset,feature_name=FEATURES),num_boost_round=params['rounds'])


def matrices(items,booster,alpha):
    return [(it['row'],(1-alpha)*it['matrix']+alpha*previous.ml_matrix(booster,it['features'],it['rates'],it['rho'])[0],it['meta']) for it in items]


def train(rows,original_rows,old_report):
    rows=legacy.history(rows);original_rows=legacy.history(original_rows)
    initial,selection,test=previous.chronological_split(original_rows)
    selection_start=selection[0]['date'];test_start=test[0]['date']
    fit_rows=[r for r in rows if r['date']<selection_start]
    lookup={r['match_id']:r for r in rows}
    selection=[lookup[r['match_id']] for r in selection];test=[lookup[r['match_id']] for r in test]
    fit_items,ff=rolling_items(rows,fit_rows);select_items,sf=rolling_items(rows,selection);test_items,tf=rolling_items(rows,test)
    if len(select_items)!=len(selection) or len(test_items)!=len(test):raise ValueError('evaluation denominator changed')
    candidates=[]
    for params in CONFIG['candidates']:
        booster=fit(fit_items,params)
        for alpha in CONFIG['alpha_candidates']:
            metrics=legacy.score_matrices(matrices(select_items,booster,alpha))
            candidates.append({'params':params,'alpha':alpha,'selection':metrics})
    chosen=max(candidates,key=lambda r:(r['selection']['top1'],-r['selection']['score_log_loss']))
    # Preserve the same original test cases and never use their results in tuning.
    booster=fit(fit_items+select_items,chosen['params'])
    prediction_items=matrices(test_items,booster,chosen['alpha'])
    candidate=legacy.score_matrices(prediction_items)
    current=old_report['test']['current_baseline'];old=old_report['test']['ml']
    if [r['match_id'] for r in candidate['records']] != [r['match_id'] for r in old['records']]:
        raise ValueError('test IDs differ from previous version')
    report={'version':VERSION,'cohort':rows[0]['cohort'],'mode':'REUSED_INSPECTED_TEST_DEVELOPMENT_ONLY',
            'formal_validation':False,'config':CONFIG,'selected':{'params':chosen['params'],'alpha':chosen['alpha']},
            'selection_candidates':candidates,'test':{'new':candidate,'previous_test':old,'current':current},
            'comparison_current':legacy.comparison(candidate,current),
            'comparison_previous':legacy.comparison(candidate,old),
            'split':{'fit_n':len(fit_rows),'fit_start':fit_rows[0]['date'],'selection_start':selection_start,
                     'selection_n':len(selection),'test_start':test_start,'test_n':len(test)},
            'folds':{'fit':ff,'selection':sf,'test':tf},
            'data':{'history_n':len(rows),'xg_rows':sum(bool(r.get('xg_evidence')) for r in rows),
                    'additional_process_rows':sum(bool(r.get('additional_process')) for r in rows),
                    'xg_feature_rows_fit':sum(bool(np.isfinite(i['features'][:,previous.FEATURES.index('own_recent_xg')]).any()) for i in fit_items)},
            'limitations':['Historical test has already been inspected; no independent prospective claim',
                           'Expanded prior history available only for audited explicit-venue records',
                           'xG provider definitions and retrospective publication times differ',
                           'No numeric training of lineup, injuries, incentives, travel or integrity',
                           'Fixed 2.5 metrics remain diagnostics, not actual-line accuracy']}
    final=fit(fit_items+select_items+test_items,chosen['params'])
    artifact={'version':VERSION,'cohort':rows[0]['cohort'],'history':rows,'competition_route':legacy.validate_training(rows),
              'core':legacy.fit_core(rows),'booster_model':final.model_to_string(),'alpha':chosen['alpha'],
              'params':chosen['params'],'config':CONFIG,'feature_names':FEATURES,
              'code_hash':code_hash(),'report_hash':legacy.digest(report),'built_at':datetime.now(timezone.utc).isoformat(),
              'observed_at':max(r['observed_at'] for r in rows),'train_end':rows[-1]['date'],
              'acceptance':report['comparison_previous'],'comparison_current':report['comparison_current'],
              'formal_validation':False,'formal_kpi_eligible':False,'data_coverage':report['data'],
              'feature_importance_gain':dict(zip(FEATURES,final.feature_importance('gain').tolist()))}
    report['test_prediction_records']=[{'match_id':r['match_id'],'date':r['date'],'primary':f'{np.argmax(m)//51}-{np.argmax(m)%51}',
                                        'primary_probability':float(np.max(m))} for r,m,meta in prediction_items]
    artifact['report_hash']=legacy.digest(report)
    return artifact,report


def predict(artifact,current,fixture):
    if artifact.get('version')!=VERSION or artifact.get('code_hash')!=code_hash() or artifact['config']!=CONFIG:
        raise ValueError('continued ML artifact/code mismatch')
    legacy.history(artifact['history'])
    if legacy.classify(fixture)['route_key']!=artifact['competition_route']['route_key']:raise ValueError('route mismatch')
    cutoff=legacy.aware(fixture['cutoff_at']);kickoff=legacy.aware(fixture['kickoff_at']);now=datetime.now(timezone.utc)
    if current['input']!=fixture or not cutoff<=now<kickoff:raise ValueError('genuinely future paired forecast required')
    if any(legacy.aware(artifact[k])>cutoff for k in ('built_at','observed_at')):raise ValueError('later model data')
    if artifact['train_end']>=kickoff.date().isoformat():raise ValueError('history reaches target date')
    rates=legacy.rates(artifact['core']['long'],fixture)[:2]
    f=feature_matrix(artifact['history'],fixture,rates)
    booster=lgb.Booster(model_str=artifact['booster_model'])
    ml,delta=previous.ml_matrix(booster,f,rates,artifact['core']['long']['nuisance']['dixon_coles'])
    dc,_=legacy.distribution(artifact['core'],None,artifact['history'],fixture,'dc365')
    m=(1-artifact['alpha'])*dc+artifact['alpha']*ml
    output=copy.deepcopy(current);rank=np.argsort(-m.ravel(),kind='stable')[:3]
    output.update(version=VERSION,model_label='test模型',role='CONTINUED_ML_RESEARCH',code_hash=code_hash(),
                  generated_at=now.isoformat(),model_hash=legacy.digest(artifact),model_built_at=artifact['built_at'],
                  model_observed_at=artifact['observed_at'],matrix=m.tolist(),matrix_hash=legacy.digest(m.tolist()),
                  primary=f'{np.argmax(m)//51}-{np.argmax(m)%51}',primary_probability=float(np.max(m)),
                  wdl=dict(zip(('主胜','平局','客胜'),map(float,legacy.probabilities(m)))),
                  top3=[{'score':f'{i//51}-{i%51}','probability':float(m.ravel()[i])} for i in rank],
                  recipe='temporally_tuned_dc_lightgbm',primary_basis='TEMPORALLY_TUNED_ML',
                  primary_label='test模型0.2研究比分',acceptance=artifact['acceptance'],
                  rates={'means':[float(np.arange(51)@m.sum(axis=1)),float(np.arange(51)@m.sum(axis=0))]},
                  ml={'alpha':artifact['alpha'],'params':artifact['params'],'data_coverage':artifact['data_coverage'],
                      'residual':delta.tolist(),'feature_count':len(FEATURES)},formal_kpi_eligible=False)
    output['wdl_reference']=max(output['wdl'],key=output['wdl'].get)
    if output['total']:
        t=output['total'];line=t['quote']['line']
        t['over']=legacy.total_settlement(m,line,'over');t['under']=legacy.total_settlement(m,line,'under')
        value=lambda p:p['win']+.5*p['half_win']-.5*p['half_loss']-p['loss']
        t['reference_direction']='over' if value(t['over'])>value(t['under']) else 'under'
    output['reasons'].append('Continued research uses previously inspected historical tests; no prospective validation')
    return output
