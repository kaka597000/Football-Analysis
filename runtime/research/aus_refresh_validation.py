"""Fixed AUS-only refresh and locked date holdout; never promotes active models.

Uses the exact retained optimization source pinned by the original AUS artifact.
The model bodies are loaded without generic module imports or booster training.
All fits here are retrospective research, not historical-asof forecasts.
"""
import argparse
import ast
import collections
from datetime import datetime, timedelta, timezone
import hashlib
import gzip
import json
import math
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse, parse_qs

import numpy as np
from scipy import sparse
from scipy.optimize import minimize
from scipy.stats import poisson
from datetime import date


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    path = Path(path)
    return json.loads(gzip.decompress(path.read_bytes()) if path.suffix == '.gz' else path.read_bytes())


def write_new(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def aware(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('timezone required')
    return parsed.astimezone(timezone.utc)


def verify_protocol(protocol):
    if digest({k:v for k,v in protocol.items() if k != 'protocol_sha256'}) != protocol['protocol_sha256']:
        raise ValueError('locked protocol changed')
    if protocol['cohort'] != 'aus.1' or protocol['test_recipe'] != dict(kind='poisson', half_life=365, ridge=20, rho=0, booster=None):
        raise ValueError('unsupported or reselected AUS recipe')
    for path, sha in protocol['protected_files'].items():
        if file_sha(path) != sha:
            raise ValueError('protected artifact/source changed: ' + path)


def original_engine(protocol, artifact):
    directory = Path(protocol['legacy_source'])
    hashes = {p.name:file_sha(p) for p in sorted(directory.glob('*.py')) if not p.name.startswith('test_')}
    if digest(hashes) != artifact['optimization_code_hash']:
        raise ValueError('original optimization source mismatch')
    if artifact['configuration'] != 'poisson' or artifact['rho'] != 0 or artifact['booster_file'] is not None or artifact['booster_sha256'] is not None:
        raise ValueError('only the pinned no-booster Poisson AUS recipe is supported')
    tree = ast.parse((directory/'models.py').read_text())
    names = {'matrix', 'design', 'fit_core', 'rates'}
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    if {n.name for n in nodes} != names:
        raise ValueError('original model API mismatch')
    scope = dict(np=np, sparse=sparse, minimize=minimize, poisson=poisson, math=math, date=date)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(directory/'models.py'), 'exec'), scope)
    runner = Path(__file__).resolve().parents[1]/'aus_expert'/'runner.py'
    if file_sha(runner) != artifact['runner_sha256']:
        raise ValueError('original AUS runner source mismatch')
    dc_nodes = [n for n in ast.parse(runner.read_text()).body if isinstance(n, ast.FunctionDef) and n.name == 'dc_matrix']
    if len(dc_nodes) != 1:raise ValueError('original AUS matrix API mismatch')
    exec(compile(ast.Module(body=dc_nodes,type_ignores=[]),str(runner),'exec'),scope)
    return SimpleNamespace(**{name:scope[name] for name in names|{'dc_matrix'}})


def baseline_rows(protocol, current, test):
    directory = Path(protocol['dataset']); manifest = read(directory/'manifest.json')
    if file_sha(directory/'manifest.json') != test['dataset_manifest_hash'] or file_sha(directory/'manifest.json') != current['dataset_manifest_sha256']:
        raise ValueError('original dataset manifest mismatch')
    for name in ('fixtures.jsonl', 'labels.jsonl', 'facts.jsonl'):
        if file_sha(directory/name) != manifest['files'][name]:
            raise ValueError('original dataset bytes mismatch')
    fixtures = [json.loads(s) for s in (directory/'fixtures.jsonl').read_text().splitlines()]
    labels = [json.loads(s) for s in (directory/'labels.jsonl').read_text().splitlines()]
    by_id = {r['match_id']:r for r in labels}
    if len(by_id) != len(labels) or len({r['match_id'] for r in fixtures}) != len(fixtures):
        raise ValueError('duplicate original fixture/label')
    if set(by_id) != {r['match_id'] for r in fixtures}:
        raise ValueError('original fixture/label join mismatch')
    rows = sorted([{**f, **by_id[f['match_id']]} for f in fixtures], key=lambda r:(r['date'], r['match_id']))
    if len(rows) != current['core']['fit_n'] or len(rows) != test['core']['fit_n'] or rows[-1]['date'] != test['core']['fit_end']:
        raise ValueError('original training coverage mismatch')
    for row in rows:
        if row['cohort'] != 'aus.1' or row['stage'] not in ('regular', 'regular-season'):
            raise ValueError('original cohort/phase mismatch')
    return rows


def validate_new(row, protocol, raw_root):
    if row.get('cohort') != 'aus.1' or row.get('competition') != 'aus.1' or row.get('stage') != 'regular':
        raise ValueError('not AUS regular')
    if row.get('gender', 'men') not in ('men', 'male') or row.get('age', 'senior') != 'senior':
        raise ValueError('unsupported gender/age')
    if row.get('status') != 'FT' or row.get('score_basis') != 'regulation_90':
        raise ValueError('not regulation FT')
    if not protocol['range'][0] <= row['date'] <= protocol['range'][1]:
        raise ValueError('outside locked window')
    for key in ('home_goals', 'away_goals'):
        if type(row.get(key)) is not int or not 0 <= row[key] <= 30:
            raise ValueError('invalid goals')
    ev = row['score_evidence']; source = Path(ev['source_path'])
    if not source.is_absolute(): source = Path(raw_root).parent/source
    if not source.resolve().is_relative_to(Path(raw_root).resolve()) or not source.is_file():
        raise ValueError('raw response outside retained root')
    sha = file_sha(source)
    if sha != ev['response_sha256']:
        raise ValueError('raw response hash mismatch')
    url = urlparse(row['source_url'])
    if url.scheme != 'https' or url.netloc != 'site.api.espn.com' or url.path != '/apis/site/v2/sports/soccer/aus.1/summary':
        raise ValueError('AUS ESPN summary source required')
    payload = read(source); payload = payload.get('data', payload)
    header = payload['header']; event_id = str(header['id'])
    if ev.get('source_key') != 'espn:'+event_id or row['match_id'] != 'espn:'+event_id or parse_qs(url.query).get('event') != [event_id]:
        raise ValueError('raw event mapping mismatch')
    if header['league']['slug'] != 'aus.1': raise ValueError('raw league mismatch')
    season = header['season']; season_name = str(season.get('name', '')).lower()
    if 'regular season' not in season_name or 'final' in season_name:
        raise ValueError('raw season not explicitly regular')
    if len(header['competitions']) != 1: raise ValueError('ambiguous competition')
    comp = header['competitions'][0]; state = comp['status']['type']
    if state.get('completed') is not True or state.get('name') != 'STATUS_FULL_TIME':
        raise ValueError('raw status not regulation FT')
    kickoff = aware(comp['date'])
    if kickoff.date().isoformat() != row['date'] or aware(row['kickoff_at']) != kickoff:
        raise ValueError('raw kickoff mismatch')
    if not kickoff+timedelta(minutes=90) <= aware(row['observed_at']) <= datetime.now(timezone.utc):
        raise ValueError('invalid result observation time')
    if not kickoff+timedelta(minutes=90) <= aware(ev['query_time']) <= aware(row['observed_at']):
        raise ValueError('invalid source observation time')
    if type(comp.get('neutralSite')) is not bool or comp['neutralSite'] is not row.get('neutral'):
        raise ValueError('raw venue unknown/conflict')
    sides = {c['homeAway']:c for c in comp['competitors']}
    if len(comp['competitors']) != 2 or set(sides) != {'home', 'away'} or row['home'] == row['away']:
        raise ValueError('ambiguous teams')
    for side, item in sides.items():
        if row[side] != 'espn:'+str(item['team']['id']): raise ValueError('raw team mismatch')
        score = item['score']; score = score.get('value', score.get('displayValue')) if isinstance(score, dict) else score
        if float(score) != row[side+'_goals']: raise ValueError('raw score mismatch')
        halves = item.get('linescores', [])
        if len(halves) != 2 or sum(int(p['displayValue']) for p in halves) != row[side+'_goals']:
            raise ValueError('two regulation halves required')
    return {**row, 'source_event_key':ev['source_key'], 'source_raw_sha256':sha,
            'first_known_at':None, 'season':season.get('name')}


def date_split(rows):
    rows = sorted(rows, key=lambda r:(r['date'],r['match_id']))
    days = sorted({r['date'] for r in rows})
    if len(days) < 2: raise ValueError('insufficient unique dates for locked holdout')
    boundary = days[len(days)//2]
    return [r for r in rows if r['date'] < boundary], [r for r in rows if r['date'] >= boundary], boundary


def fit_fixed(engine, rows):
    rows = sorted(rows, key=lambda r:(r['date'],r['match_id']))
    return {'current':{'constant':[float(np.mean([r[k] for r in rows])) for k in ('home_goals','away_goals')],
                        'fit_n':len(rows), 'fit_end':rows[-1]['date']},
            'test':engine.fit_core(rows,365,20)}


def predict_matrix(engine, core, kind, target):
    safe = {k:target.get(k) for k in ('match_id','date','home','away','neutral')}
    if target['date'] <= core['fit_end']: raise ValueError('target is in fitted history')
    if kind == 'test' and any(safe[k] not in core['teams'] for k in ('home','away')):
        raise ValueError('ABSTAIN_COLD_START_TEAM_NOT_TRAINED')
    mu = core['constant'] if kind == 'current' else engine.rates(core,[safe])[0]
    matrix = engine.matrix(mu) if kind == 'current' else engine.dc_matrix(mu,0.)
    if not np.isfinite(matrix).all() or (matrix < 0).any() or abs(float(matrix.sum())-1) > 1e-9:
        raise ValueError('invalid probability matrix')
    return matrix


def score(matrix, target):
    h,a = target['home_goals'],target['away_goals']; primary = list(map(int,np.unravel_index(matrix.argmax(),matrix.shape)))
    wd = np.array([np.tril(matrix,-1).sum(),np.trace(matrix),np.triu(matrix,1).sum()])
    outcome = 0 if h>a else 1 if h==a else 2; onehot = np.eye(3)[outcome]
    prob = matrix[h,a] if h < matrix.shape[0] and a < matrix.shape[1] else 0.
    return {'status':'PREDICTED_RESEARCH_ONLY', 'primary':primary, 'primary_hit':primary==[h,a],
            'wdl_pick':['主胜','平局','客胜'][int(wd.argmax())], 'wdl_hit':bool(wd.argmax()==outcome),
            'wdl_probabilities':wd.tolist(), 'score_log_loss':float(-np.log(max(float(prob),1e-300))),
            'wdl_brier':float(np.sum((wd-onehot)**2)), 'wdl_rps':float(np.sum((np.cumsum(wd)[:-1]-np.cumsum(onehot)[:-1])**2)/2),
            'matrix':matrix.tolist(), 'matrix_sha256':digest(matrix.tolist())}


def metrics(records, key):
    good = [r[key] for r in records if r[key]['status'] == 'PREDICTED_RESEARCH_ONLY']; n=len(good)
    return {'scope':len(records),'predicted':n,'abstentions':len(records)-n,'coverage':n/len(records) if records else None,
            'primary_hits':sum(x['primary_hit'] for x in good),'wdl_hits':sum(x['wdl_hit'] for x in good),
            'primary_top1':sum(x['primary_hit'] for x in good)/n if n else None,
            'wdl_accuracy':sum(x['wdl_hit'] for x in good)/n if n else None,
            'primary_rate_all_targets':sum(x['primary_hit'] for x in good)/len(records) if records else None,
            'wdl_rate_all_targets':sum(x['wdl_hit'] for x in good)/len(records) if records else None,
            **{name:float(np.mean([x[name] for x in good])) if n else None for name in ('score_log_loss','wdl_brier','wdl_rps')},
            'actual_market_ou':{'status':'NOT_EVALUATED_NO_VERIFIED_HISTORICAL_PREMATCH_QUOTES','n':0}}


def paired_bootstrap(records, old_key, new_key, draws=10000, seed=20261110):
    good = [r for r in records if r[old_key]['status'] == r[new_key]['status'] == 'PREDICTED_RESEARCH_ONLY']
    if not good: return {'scope':len(records),'paired_n':0,'intervals':None}
    fields = ('primary_hit','wdl_hit','score_log_loss','wdl_brier','wdl_rps')
    delta=np.array([[float(r[new_key][k])-float(r[old_key][k]) for k in fields] for r in good])
    dates=sorted({r['date'] for r in good}); blocks=[np.array([i for i,r in enumerate(good) if r['date']==d]) for d in dates]
    rng=np.random.default_rng(seed); results=[]
    for _ in range(draws):
        indices=np.concatenate([blocks[i] for i in rng.integers(0,len(blocks),len(blocks))]); results.append(delta[indices].mean(0))
    intervals=np.quantile(results,[.025,.975],axis=0)
    return {'scope':len(records),'paired_n':len(good),'date_blocks':len(blocks),'draws':draws,'seed':seed,
            'difference_definition':'refreshed minus old; positive hit deltas better, negative loss deltas better',
            'net_primary_hits':int(delta[:,0].sum()),'net_wdl_hits':int(delta[:,1].sum()),
            'hit_transitions':{name:{'old_wrong_new_right':sum(not r[old_key][name] and r[new_key][name] for r in good),
                                     'old_right_new_wrong':sum(r[old_key][name] and not r[new_key][name] for r in good)} for name in fields[:2]},
            'metrics':{name:{'mean_difference':float(delta[:,i].mean()),'date_block_bootstrap_95':intervals[:,i].tolist()} for i,name in enumerate(fields)}}


def run(protocol_path, rows_path, raw_root, out, global_protocol=None, coverage_path=None):
    protocol=read(protocol_path); verify_protocol(protocol)
    output=Path(out); output.mkdir(parents=True,exist_ok=False)
    current=read(protocol['current_artifact']); test=read(protocol['test_artifact'])
    engine=original_engine(protocol,test); old=baseline_rows(protocol,current,test)
    reproduced=fit_fixed(engine,old)
    reproduction={'original_training_rows':len(old),
        'current_constant_max_abs_difference':float(np.max(np.abs(np.asarray(reproduced['current']['constant'])-current['core']['constant']))),
        'test_coefficient_max_abs_difference':float(np.max(np.abs(np.asarray(reproduced['test']['coefficients'])-test['core']['coefficients']))),
        'test_teams_equal':reproduced['test']['teams']==test['core']['teams']}
    if not reproduction['test_teams_equal'] or max(reproduction['current_constant_max_abs_difference'],reproduction['test_coefficient_max_abs_difference'])>1e-10:
        raise ValueError('original recipe reproduction mismatch')
    write_new(output/'original-fit-reproduction.json',reproduction)
    input_sha=file_sha(rows_path)
    coverage=read(coverage_path) if coverage_path else {'schedule_inventory_status':'NOT_PROVIDED','inventory_verified':False}
    source=read(rows_path); incoming=source['rows'] if isinstance(source,dict) else source
    accepted=[]; rejected=[]; counts=collections.Counter(r.get('match_id') for r in incoming)
    for row in incoming:
        try:
            if counts[row['match_id']] != 1: raise ValueError('duplicate incoming event identity')
            accepted.append(validate_new(row,protocol,raw_root))
        except (ValueError,KeyError,TypeError,OverflowError) as exc:
            rejected.append({'match_id':row.get('match_id'),'date':row.get('date'),'reason':str(exc)})
    event_counts=collections.Counter(r['source_event_key'] for r in accepted)
    duplicated={k for k,n in event_counts.items() if n>1}
    if duplicated:
        rejected += [{'match_id':r['match_id'],'reason':'duplicate source event identity'} for r in accepted if r['source_event_key'] in duplicated]
        accepted=[r for r in accepted if r['source_event_key'] not in duplicated]
    write_new(output/'data-quality.json',{'incoming':len(incoming),'accepted':len(accepted),'rejected':rejected,'first_known_missing':len(old)+len(accepted),'source_input_sha256':file_sha(rows_path)})
    refresh,holdout,boundary=date_split(accepted)
    if old[-1]['date'] >= refresh[0]['date']: raise ValueError('new data overlaps original fit')
    plan={'origin':boundary,'refresh_ids':[r['match_id'] for r in refresh],'holdout_ids':[r['match_id'] for r in holdout],
          'protocol_sha256':protocol['protocol_sha256'],'source_input_sha256':input_sha,'source_coverage':coverage,
          'active':False,'asof_status':'RETROSPECTIVE_NOT_ASOF','phase':'LOCKED_BEFORE_EVALUATION_FIT'}
    write_new(output/'evaluation-plan.json',plan)
    evaluation=fit_fixed(engine,old+refresh)
    write_new(output/'evaluation-fits.json',{**plan,'phase':'EVALUATION_FIT','fit':evaluation,'plan_sha256':file_sha(output/'evaluation-plan.json')})
    records=[]
    for row in holdout:
        record={k:row.get(k) for k in ('match_id','date','kickoff_at','observed_at','first_known_at','home','away','home_goals','away_goals','source_event_key')}
        for key,core,kind in [('old_current',current['core'],'current'),('old_test',test['core'],'test'),('refreshed_current',evaluation['current'],'current'),('refreshed_test',evaluation['test'],'test')]:
            try:record[key]=score(predict_matrix(engine,core,kind,row),row)
            except (ValueError,FloatingPointError,OverflowError) as exc:record[key]={'status':'ABSTAIN','reason':str(exc)}
        records.append(record)
    write_new(output/'holdout-predictions.json',records)
    summary={'mode':'RETROSPECTIVE_NOT_ASOF_NOT_PROSPECTIVE','cohort':'aus.1','domain':'OOD','protocol_sha256':protocol['protocol_sha256'], 'global_protocol_sha256':file_sha(global_protocol) if global_protocol else None,
             'data_coverage':coverage,'original_n':len(old),'new_accepted':len(accepted),'refresh_n':len(refresh),'holdout_n':len(holdout),'holdout_start':boundary,
             'training_end':max(r['date'] for r in old+refresh),'metrics':{key:metrics(records,key) for key in ('old_current','old_test','refreshed_current','refreshed_test')},
             'paired':{kind:paired_bootstrap(records,'old_'+kind,'refreshed_'+kind,protocol['bootstrap']['draws'],protocol['bootstrap']['seed']) for kind in ('current','test')},
             'artifact_created_at':{'current':current['created_at'],'test':test['created_at']},
             'all_incremental_matches_preceding_old_artifact_creation':{name:sum(aware(r['kickoff_at'])<aware(art['created_at']) for r in accepted) for name,art in [('current',current),('test',test)]},
             'holdout_matches_preceding_old_artifact_creation':{name:sum(aware(r['kickoff_at'])<aware(art['created_at']) for r in holdout) for name,art in [('current',current),('test',test)]},
             'rejected_data_rows':len(rejected),'active':False,'decision':'DO_NOT_PROMOTE','weight_selection_performed':False,
             'primary_semantics':'STATISTICAL_MATRIX_TOP1_REFERENCE; comprehensive Primary not evaluated',
             'booster_dependency':{'required':False,'file':None,'sha256':None},'historical_first_known':'MISSING_OR_LATE_NOT_CERTIFIED'}
    write_new(output/'summary.json',summary)
    # These latest candidates are intentionally created only after holdout output
    # and never scored on its already-consumed labels.
    final=fit_fixed(engine,old+accepted)
    write_new(output/'final-training-rows.json',old+accepted)
    serialization={}
    for kind in ('current','test'):
        candidate={'version':'WCPP-AUS-FIXED-REFRESH-20261110-'+kind,'active':False,'promotion':'DO_NOT_PROMOTE','scope':'aus.1 men senior regular OOD',
                   'created_at':datetime.now(timezone.utc).isoformat(),'kind':kind,'core':final[kind],'rho':0.,'booster_file':None,'booster_sha256':None,
                   'recipe':protocol[kind+'_recipe'],'protocol_sha256':protocol['protocol_sha256'],'training_input_sha256':file_sha(output/'final-training-rows.json'),
                   'source_coverage':coverage,'asof_status':'RETROSPECTIVE_NOT_ASOF','comprehensive_primary':'NOT_EVALUATED_STATISTICAL_REFERENCE_ONLY',
                   'source_artifact_sha256':file_sha(protocol[kind+'_artifact']),'optimization_code_hash':test['optimization_code_hash'],
                   'evaluation_claim':'final fit has no holdout score; preceding evaluation uses separate fit'}
        write_new(output/('final-'+kind+'-artifact.json'),candidate)
        loaded=read(output/('final-'+kind+'-artifact.json'))
        if digest(loaded['core'])!=digest(final[kind]):raise ValueError('candidate serialization mismatch')
        synthetic={k:accepted[0][k] for k in ('home','away','neutral')}
        synthetic.update(match_id='SYNTHETIC-SERIALIZATION-ONLY',date=(date.fromisoformat(final[kind]['fit_end'])+timedelta(days=1)).isoformat())
        expected=predict_matrix(engine,final[kind],kind,synthetic)
        actual=predict_matrix(engine,loaded['core'],kind,synthetic)
        if not np.array_equal(expected,actual):raise ValueError('candidate matrix serialization mismatch')
        serialization[kind]={'synthetic':True,'matrix_exactly_equal':True,'matrix_sha256':digest(actual.tolist()),'not_holdout_evaluation':True}
    write_new(output/'serialization-check.json',serialization)
    verify_protocol(protocol)
    if file_sha(Path(__file__).resolve().parents[1]/'aus_expert'/'runner.py')!=test['runner_sha256']:raise ValueError('AUS runner changed during run')
    if file_sha(rows_path)!=input_sha:raise ValueError('source input changed during run')
    write_new(output/'verification.json',{'protected_files_unchanged':True,'protocol_sha256':protocol['protocol_sha256'],'source_code_sha256':file_sha(__file__),
             'training_end_before_holdout':summary['training_end']<boundary,'booster_unchanged':True,'active_registry_unchanged':True,
             'files':{p.name:file_sha(p) for p in output.iterdir() if p.is_file()}})
    return summary


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--protocol',required=True);p.add_argument('--rows',required=True);p.add_argument('--raw-root',required=True);p.add_argument('--out',required=True);p.add_argument('--global-protocol');p.add_argument('--coverage');a=p.parse_args()
    print(json.dumps(run(a.protocol,a.rows,a.raw_root,a.out,a.global_protocol,a.coverage),ensure_ascii=False,indent=2,allow_nan=False))


if __name__=='__main__':main()
