"""Independent Nations League paired candidates and immutable acceptance evidence.

Current-candidate: fitted Poisson intercept/home advantage. Test-candidate:
regularized opponent-adjusted team Poisson. No registry changes or promotion.
The retained 2026 results are retrospective diagnostics, never as-of validation.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys

import numpy as np
from scipy.optimize import minimize

if __package__ in (None, '') or __package__.startswith('runtime.'):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from competition_scope import training_rows
from nations_league.database import export, stats
from research_program.nations_phase import resolve_phase_evidence
from v2.model import fit_strength, rates, matrix, aware
from v2_test.evaluation import evaluate
from research.analysis_freeze import save, verify

PROTOCOL = {
    'schema': 'WCPP-NATIONS-MODEL-PROTOCOL-1',
    'levels': ['A','B','C','D'], 'stage': 'league-phase',
    'history_rule': 'only reverified database training_eligible rows',
    'fit_before': '2026-01-01', 'holdout_from': '2026-01-01',
    'current': 'fitted_intercept_home_poisson',
    'test': 'regularized_team_poisson', 'half_life_days': 730., 'ridge': 8.,
    'minimum_fit': 60, 'minimum_diagnostic': 30,
    'selection': 'none; both fixed before this run, no holdout tuning',
    'research_quality_rule': 'test strictly improves Top1; no regression in score log loss, score Brier, WDL Brier, WDL RPS or WDL accuracy',
    'prospective_rule': 'at least 30 independently source/market-audited first freezes per level; independent acceptance review required',
    'historical_asof_certified': False, 'automatic_activation': False,
}


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read(path): return json.loads(Path(path).read_text())
def write(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x') as f:json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n')
def now(): return datetime.now(timezone.utc).isoformat()


def prepare(rows):
    training_rows(rows)
    rows=sorted(rows,key=lambda r:(r['date'],r['match_id']))
    if len({r['match_id'] for r in rows})!=len(rows):raise ValueError('duplicate match')
    for r in rows:
        if r['date']>=datetime.now(timezone.utc).date().isoformat():raise ValueError('future/today training label')
        if aware(r['observed_at'])>datetime.now(timezone.utc):raise ValueError('future capture')
    return rows


def split(rows):
    prior=[r for r in rows if r['date']<PROTOCOL['fit_before']]
    targets=[r for r in rows if r['date']>=PROTOCOL['holdout_from']]
    if len(prior)<PROTOCOL['minimum_fit'] or not targets:raise ValueError('insufficient fixed chronological groups')
    assert max(r['date'] for r in prior)<min(r['date'] for r in targets)
    return prior,targets


def fit_current(rows):
    h=np.array([r['home_goals'] for r in rows]);a=np.array([r['away_goals'] for r in rows])
    home=np.array([not r['neutral'] for r in rows],float)
    def objective(v):
        mh=np.exp(v[0]+v[1]*home);ma=np.exp(v[0])
        loss=float(np.sum(mh-h*np.log(mh)+ma-a*np.log(ma)))
        return loss,np.array([sum(mh-h)+sum(ma-a),sum((mh-h)*home)])
    fit=minimize(objective,[math.log(max(.1,float(a.mean()))),0.],jac=True,
                 method='L-BFGS-B',bounds=[(-2.,1.5),(-.8,.8)])
    if not fit.success:raise ValueError('constant Poisson fit failed')
    return {'intercept':float(fit.x[0]),'home_advantage':float(fit.x[1]),
            'training_n':len(rows),'train_end':max(r['date'] for r in rows)}


def fit_pair(rows):
    current=fit_current(rows)
    test=fit_strength(rows,half_life=PROTOCOL['half_life_days'],ridge=PROTOCOL['ridge'])
    return {'current':current,'test':test}


def distribution(core,role,fixture):
    if type(fixture.get('neutral')) is not bool:raise ValueError('explicit verified venue required')
    if role=='current':
        mu=(math.exp(core['intercept']+core['home_advantage']*(not fixture['neutral'])),math.exp(core['intercept']))
        missing=[]
    elif role=='test':
        h,a,missing=rates(core,fixture);mu=(h,a)
    else:raise ValueError('explicit independent role required')
    return matrix(*mu),list(mu),missing


def diagnostic(pair,targets):
    outputs={};predictions={}
    for role,core in pair.items():
        records=[];evaluated=[]
        for row in targets:
            m,mu,missing=distribution(core,role,row)
            primary=[int(x) for x in np.unravel_index(m.argmax(),m.shape)]
            evaluated.append({'matrix':m,'home_goals':row['home_goals'],'away_goals':row['away_goals'],'primary_score':primary})
            records.append({'match_id':row['match_id'],'date':row['date'],'primary':primary,
                            'actual':[row['home_goals'],row['away_goals']],'hit':primary==[row['home_goals'],row['away_goals']],
                            'unseen_teams_using_fitted_global_prior':missing,'expected_goals':mu,'matrix':m.tolist()})
        outputs[role]=evaluate(evaluated);predictions[role]=records
    c,t=(outputs[k]['metrics'] for k in ('current','test'))
    reasons=[]
    if len(targets)<PROTOCOL['minimum_diagnostic']:reasons.append('INSUFFICIENT_DIAGNOSTIC_SAMPLE')
    if t['exact_top1']<=c['exact_top1']:reasons.append('NO_STRICT_PRIMARY_IMPROVEMENT')
    for key in ('score_log_loss','score_brier','wdl_brier','wdl_rps'):
        if t[key]>c[key]:reasons.append('QUALITY_REGRESSION:'+key)
    if t['wdl_accuracy']<c['wdl_accuracy']:reasons.append('WDL_ACCURACY_REGRESSION')
    net=sum(int(t['hit'])-int(c['hit']) for c,t in zip(predictions['current'],predictions['test']))
    return {'metrics':outputs,'predictions':predictions,'net_primary_hits':net,
            'retrospective_quality_passed':not reasons,'quality_reasons':reasons,
            'formal_acceptance_passed':False,'activation_allowed':False,
            'acceptance_reasons':['NO_COMPLETED_PROSPECTIVE_FIRST_FREEZES','INDEPENDENT_SOURCE_AND_ACTUAL_LINE_AUDITS_REQUIRED'],
            'historical_asof_certified':False,'actual_line_metrics':None}


def train(db,out):
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    db_before=sha(db)
    write(out/'protocol.json',{**PROTOCOL,'locked_at':now(),'database_sha256':db_before,
                               'executable_sha256':sha(__file__)})
    summary=[];routes={}
    for level in PROTOCOL['levels']:
        folder=out/level;folder.mkdir()
        export(db,folder/'dataset.json','league-phase',level)
        rows=prepare(read(folder/'dataset.json')['rows'])
        prior,targets=split(rows)
        evaluation_pair=fit_pair(prior)
        report=diagnostic(evaluation_pair,targets)
        write(folder/'evaluation-models.json',evaluation_pair)
        write(folder/'diagnostic.json',report)
        final_pair=fit_pair(rows)
        route={}
        for role,core in final_pair.items():
            value={'schema':'WCPP-NATIONS-POISSON-1','version':f'WCPP-NATIONS-{level}-{role.upper()}-0.1',
                   'role':role,'cohort':f'uefa.nations:{level}:league-phase','league_level':level,
                   'stage':'league-phase','competition':'uefa.nations','core':core,'created_at':now(),
                   'train_end':rows[-1]['date'],'training_n':len(rows),
                   'dataset_path':str((folder/'dataset.json').resolve()),'dataset_sha256':sha(folder/'dataset.json'),
                   'protocol_path':str((out/'protocol.json').resolve()),'protocol_sha256':sha(out/'protocol.json'),
                   'engine_path':str(Path(__file__).resolve()),'engine_sha256':sha(__file__),
                   'validation_status':'TRAINED_RESEARCH_PENDING_PROSPECTIVE_ACCEPTANCE',
                   'formal_acceptance_passed':False,'activation_allowed':False,
                   'retrospective_quality_passed':report['retrospective_quality_passed'],
                   'trained_features':['historical_regulation_goals','home_indicator']+(['team_attack','opponent_defence'] if role=='test' else []),
                   'untrained_features':['xg','injuries','lineup','motivation','full_rest','market'],
                   'historical_asof_certified':False}
            path=folder/(role+'-artifact.json');write(path,value)
            route[role]={'path':str(path.resolve()),'sha256':sha(path)}
        routes[level]=route
        summary.append({'level':level,'n':len(rows),'fit_n':len(prior),'holdout_n':len(targets),
                        'fit_end':prior[-1]['date'],'holdout_start':targets[0]['date'],'train_end':rows[-1]['date'],
                        'current':report['metrics']['current'],'test':report['metrics']['test'],
                        'net_primary_hits':report['net_primary_hits'],'quality_reasons':report['quality_reasons'],
                        'formal_acceptance_passed':False})
    if sha(db)!=db_before:raise ValueError('database changed during training')
    result={'schema':'WCPP-NATIONS-PAIRED-CANDIDATES-1','created_at':now(),'routes':routes,
            'summary':summary,'active_registry_modified':False,'automatic_activation':False,
            'acceptance_status':'AWAITING_REAL_FUTURE_FREEZES_AND_INDEPENDENT_REVIEW'}
    write(out/'candidates.json',result)
    return result


def validate_future(fixture):
    if fixture.get('competition')!='uefa.nations' or fixture.get('gender') not in ('men','male') or fixture.get('age')!='senior':
        raise ValueError('explicit senior male Nations League identity required')
    level=fixture.get('league_level')
    if level not in PROTOCOL['levels'] or fixture.get('stage')!='league-phase' or fixture.get('cohort')!=f'uefa.nations:{level}:league-phase':
        raise ValueError('exact league and phase route required')
    if any(k in fixture for k in ('home_goals','away_goals','actual','result')):raise ValueError('target labels prohibited')
    cutoff=aware(fixture['cutoff_at']);kickoff=aware(fixture['kickoff_at'])
    if not cutoff<=datetime.now(timezone.utc)<kickoff or fixture.get('phase')!='PreMatch':raise ValueError('genuinely future prematch required')
    e=fixture['phase_evidence'];p=Path(e['source_path'])
    if sha(p)!=e['source_sha256'] or aware(e['query_time'])>cutoff:raise ValueError('primary source integrity/time')
    from urllib.parse import urlparse,parse_qs
    url=urlparse(e['source_url'])
    eid=str(fixture['source_event_id'])
    if url.scheme!='https' or url.hostname!='site.api.espn.com' or url.port not in (None,443) or url.path!='/apis/site/v2/sports/soccer/uefa.nations/summary' or parse_qs(url.query).get('event')!=[eid]:
        raise ValueError('exact protected source endpoint/event required')
    raw=read(p);h=raw['header'];c=h['competitions'][0]
    if str(h['id'])!=eid or h['league']['slug']!='uefa.nations' or len(h['competitions'])!=1 or str(c['id'])!=eid:raise ValueError('raw event identity')
    sides={x['homeAway']:'espn:'+str(x['team']['id']) for x in c['competitors']}
    if len(c['competitors'])!=2 or set(sides)!= {'home','away'} or len(set(sides.values()))!=2 or any(fixture[s]!=tid for s,tid in sides.items()):raise ValueError('raw team identity')
    if aware(c['date'])!=kickoff or fixture['date']!=kickoff.astimezone(timezone.utc).date().isoformat():raise ValueError('raw kickoff identity')
    state=c['status']['type']
    if state['state']!='pre' or state['completed'] is not False:raise ValueError('source is not future')
    if type(c.get('neutralSite')) is not bool or fixture.get('neutral') is not c['neutralSite']:raise ValueError('verified neutral venue required')
    phase=resolve_phase_evidence(raw,fixture.get('supplemental_phase_evidence'),{'cutoff_at':fixture['cutoff_at']})
    if not phase['complete'] or phase['stage']!='league-phase' or phase['league_level']!=level:raise ValueError('source phase/grade mismatch')


def predict(path,fixture):
    artifact=read(path)
    if artifact.get('schema')!='WCPP-NATIONS-POISSON-1' or artifact['cohort']!=fixture['cohort']:raise ValueError('candidate route identity')
    for stem in ('dataset','protocol','engine'):
        if sha(artifact[stem+'_path'])!=artifact[stem+'_sha256']:raise ValueError('candidate dependency changed:'+stem)
    validate_future(fixture)
    if aware(artifact['created_at'])>aware(fixture['cutoff_at']) or artifact['train_end']>=fixture['date']:raise ValueError('training/availability boundary')
    m,mu,missing=distribution(artifact['core'],artifact['role'],fixture)
    wdl={'主胜':float(np.tril(m,-1).sum()),'平局':float(np.trace(m)),'客胜':float(np.triu(m,1).sum())}
    primary=[int(v) for v in np.unravel_index(m.argmax(),m.shape)]
    from v2.model import digest
    return {'version':artifact['version'],'role':artifact['role'],'generated_at':now(),'input':fixture,
            'matrix':m.tolist(),'matrix_hash':digest(m.tolist()),'primary':f'{primary[0]}-{primary[1]}',
            'primary_probability':float(m[tuple(primary)]),'expected_goals':mu,'wdl':wdl,
            'wdl_reference':max(wdl,key=wdl.get),'artifact_sha256':sha(path),'training_data_cutoff':artifact['train_end'],
            'unseen_teams_using_fitted_global_prior':missing,'status':'TRAINED_CANDIDATE_RESEARCH_ONLY',
            'primary_label':'统计参考；非综合第一推荐','formal_kpi_eligible':False,
            'comprehensive_primary_ready':False,'actual_line':fixture.get('total_quote'),
            'acceptance_passed':False,'actual_line_status':'NOT_SUPPLIED' if not fixture.get('total_quote') else 'REQUIRES_SEPARATE_QUOTE_AUDIT'}


def freeze_pairs(index_path,fixtures,out,freeze_root):
    index=read(index_path);out=Path(out);out.mkdir(parents=True,exist_ok=False)
    ids=[f['match_id'] for f in fixtures]
    if not ids or len(ids)!=len(set(ids)):raise ValueError('unique complete future population required')
    for f in fixtures:validate_future(f)
    protocol={'schema':'WCPP-NATIONS-FORWARD-PLAN-1','created_at':now(),'index_path':str(Path(index_path).resolve()),
              'index_sha256':sha(index_path),'routes':index['routes'],'fixture_population':fixtures,
              'minimum_completed_per_level':30,'alpha':.05,'primary_coverage':1.,
              'selection_rule':'all supplied future IDs; no choosing better current/test after results',
              'source_and_actual_market_audits_required':True,'automatic_activation':False}
    shell={'match_id':'nations-forward-plan:'+sha(index_path),'cohort':'uefa.nations',
           'cutoff_at':now(),'kickoff_at':min(f['kickoff_at'] for f in fixtures)}
    receipt=save(protocol,shell,stage='review',track='research-preregistration',root=freeze_root)
    write(out/'protocol.json',protocol);write(out/'plan-receipt.json',receipt)
    receipts=[]
    for original in fixtures:
        f={**original,'cutoff_at':now()};route=index['routes'][f['league_level']]
        for role in ('current','test'):
            if sha(route[role]['path'])!=route[role]['sha256']:raise ValueError('candidate checksum')
        pair={role:predict(route[role]['path'],f) for role in ('current','test')}
        pair.update(plan_sha256=receipt['sha256'],formal_kpi_eligible=False,active_route=False)
        record=save(pair,f,stage='prematch',track='nations-paired-candidates',root=freeze_root,
                    idempotency_key='nations-pair:'+receipt['sha256']+':'+f['match_id'])
        write(out/(f['match_id'].replace(':','-')+'.json'),pair);receipts.append(record)
    write(out/'receipts.json',receipts)
    return {'frozen':len(receipts),'plan':receipt,'automatic_activation':False}


def main():
    p=argparse.ArgumentParser(description=__doc__);s=p.add_subparsers(dest='cmd',required=True)
    t=s.add_parser('train');t.add_argument('--db',required=True);t.add_argument('--out',required=True)
    f=s.add_parser('freeze-pairs');f.add_argument('--index',required=True);f.add_argument('--fixtures',required=True);f.add_argument('--out',required=True);f.add_argument('--freeze-root',default='/workspace/WCPP/forecasts/issued-analyses')
    a=p.parse_args()
    if a.cmd=='train':result=train(a.db,a.out);print(json.dumps({'trained_routes':len(result['routes']),'activation_allowed':False}))
    else:
        fixtures=read(a.fixtures)
        print(json.dumps(freeze_pairs(a.index,fixtures,a.out,a.freeze_root)))


if __name__=='__main__':main()
