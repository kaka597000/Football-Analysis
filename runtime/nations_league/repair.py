"""Independent Nations data-refresh, strength, tail and selection experiments.

The legacy engines/artifacts stay byte-for-byte unchanged. Calendar partitions
are development diagnostics, not historical information-time certification.
Only a newly frozen future population can supply prospective evidence.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import itertools
import json
import math
from pathlib import Path

import numpy as np
from scipy.special import gammaln, logsumexp
from scipy.stats import nbinom, poisson

from . import database, model as legacy, safe_cli
from research.analysis_freeze import digest, verify
from research_program.nations_phase import classify
from v2.model import fit_strength, rates
from v2_test.evaluation import evaluate

SCHEMA = 'WCPP-NATIONS-REPAIR-ARTIFACT-1'
WDL = ('主胜', '平局', '客胜')
SELECTORS = ('joint_map', 'wdl_conditioned_map', 'total_mode_conditioned_map')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    legacy.write(path, value)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def recipe(half_life=730., ridge=8., variance=0.):
    return {'half_life_days': float(half_life), 'ridge': float(ridge),
            'shared_gamma_variance': float(variance)}


def recipe_id(value):
    return 'h{half_life_days:g}-r{ridge:g}-g{shared_gamma_variance:g}'.format(**value)


def probability_matrix(home, away, variance=0., temperature=1.):
    """Poisson or shared gamma pace mixture; explicitly bound omitted mass.

    The mixture preserves both marginal means before temperature calibration.
    It changes dispersion and goal correlation, never adds goals by hand.
    """
    values=np.asarray([home,away,variance,temperature],float)
    if (not np.isfinite(values).all() or not 0<home<=12 or not 0<away<=12
            or not 0<=variance<=.25 or not .9<=temperature<=1.1):
        raise ValueError('unsupported finite rate/dispersion/temperature')
    for cap in (50,75,100,150,200):
        if variance:
            k=1/variance
            omitted=nbinom.sf(cap,k,k/(k+home))+nbinom.sf(cap,k,k/(k+away))
        else:
            omitted=poisson.sf(cap,home)+poisson.sf(cap,away)
        if omitted<1e-12:
            break
    else:
        raise ValueError('score support would omit material probability')
    h,a=np.indices((cap+1,cap+1))
    if variance:
        total=h+a
        logp=(gammaln(k+total)-gammaln(k)-gammaln(h+1)-gammaln(a+1)
              +k*math.log(k)+h*math.log(home)+a*math.log(away)
              -(k+total)*math.log(k+home+away))
    else:
        logp=poisson.logpmf(h,home)+poisson.logpmf(a,away)
    if abs(math.exp(float(logsumexp(logp)))-1)>1e-10:
        raise ValueError('unacceptable captured probability')
    # Do not let temperature amplify an already material truncated tail.
    calibrated=logp/temperature
    m=np.exp(calibrated-logsumexp(calibrated))
    if float(m[-1,:].sum()+m[:,-1].sum())>1e-10:
        raise ValueError('calibrated probability requires larger support')
    return m


def select_score(matrix, selector='joint_map'):
    if selector not in SELECTORS:
        raise ValueError('unknown preregistered selector')
    m=np.asarray(matrix,float)
    h,a=np.indices(m.shape)
    mask=np.ones(m.shape,dtype=bool)
    if selector=='wdl_conditioned_map':
        direction=int(np.argmax([m[h>a].sum(),m[h==a].sum(),m[h<a].sum()]))
        mask=(h>a,h==a,h<a)[direction]
    elif selector=='total_mode_conditioned_map':
        total=np.bincount((h+a).ravel(),weights=m.ravel())
        mask=h+a==int(total.argmax())
    return [int(x) for x in np.unravel_index(np.where(mask,m,-1).argmax(),m.shape)]


def fitted(rows, spec, role='test'):
    if role=='current':
        return legacy.fit_current(rows)
    if role!='test':
        raise ValueError('explicit numerical role required')
    return fit_strength(rows,half_life=spec['half_life_days'],ridge=spec['ridge'])


def distribution(core, spec, fixture, role='test', temperature=1.):
    if role=='current':
        mu=[math.exp(core['intercept']+core['home_advantage']*(not fixture['neutral'])),
            math.exp(core['intercept'])]
        missing=[]
    else:
        h,a,missing=rates(core,fixture);mu=[h,a]
    return probability_matrix(*mu,spec['shared_gamma_variance'],temperature),mu,missing


def record_prediction(matrix, row, selector, *, base_rates=None, missing=None):
    primary=select_score(matrix,selector)
    metrics=evaluate([{'matrix':matrix,'home_goals':row['home_goals'],
                      'away_goals':row['away_goals'],'primary_score':primary}])
    h,a=np.indices(matrix.shape)
    total=np.bincount((h+a).ravel(),weights=matrix.ravel())
    actual=[row['home_goals'],row['away_goals']]
    wdl=[float(matrix[h>a].sum()),float(matrix[h==a].sum()),float(matrix[h<a].sum())]
    return {'match_id':row['match_id'],'date':row['date'],'cohort':row['cohort'],
            'actual':actual,'primary':primary,'primary_hit':primary==actual,
            'statistical_top1':select_score(matrix),'wdl':wdl,
            'wdl_reference':WDL[int(np.argmax(wdl))],
            'metrics':metrics['metrics'],'tail':metrics['tail'],
            'expected_goals':[float((h*matrix).sum()),float((a*matrix).sum())],
            'base_rates':base_rates,'zero_probabilities':[float(matrix[0,:].sum()),float(matrix[:,0].sum())],
            'total_distribution':total.tolist(),'matrix_hash':digest(matrix.tolist()),
            'unseen_teams_using_fitted_global_prior':missing or []}


def summarize(records):
    if not records:
        return {'n':0,'status':'NO_QUALIFIED_TARGETS'}
    n=len(records)
    totals=[sum(r['actual']) for r in records]
    tail={}
    for label in records[0]['tail']:
        tail[label]={'brier':float(np.mean([r['tail'][label]['brier'] for r in records])),
                     'observed_events':sum(r['tail'][label]['observed_events'] for r in records)}
    return {'n':n,'primary_hits':sum(r['primary_hit'] for r in records),
            'primary_accuracy':sum(r['primary_hit'] for r in records)/n,'coverage':1.,
            'wdl_hits':sum(int(r['metrics']['wdl_accuracy']) for r in records),
            **{k:float(np.mean([r['metrics'][k] for r in records])) for k in records[0]['metrics']},
            'expected_total_mean':float(np.mean([sum(r['expected_goals']) for r in records])),
            'actual_total_mean':float(np.mean(totals)),
            'actual_total4plus_n':sum(x>=4 for x in totals),
            'expected_total4plus_n':sum(sum(r['total_distribution'][4:]) for r in records),
            'zero_brier':float(np.mean([np.square(np.asarray(r['zero_probabilities'])
                    -(np.asarray(r['actual'])==0)).mean() for r in records])),
            'primary_score_counts':dict(Counter('-'.join(map(str,r['primary'])) for r in records)),
            'extreme_rate_rows':sum(max(r['expected_goals'])>=4 for r in records),
            'tail':tail,'actual_line_n':0,'actual_line_status':'NO_HISTORICAL_TIMESTAMPED_QUOTES'}


def paired(before, after, repetitions=4000):
    if [r['match_id'] for r in before]!=[r['match_id'] for r in after]:
        raise ValueError('exact same ordered fixtures required for comparison')
    if not before:
        return {'n':0}
    groups=defaultdict(lambda:np.zeros(3))
    gained=[];lost=[]
    for b,c in zip(before,after):
        delta=int(c['primary_hit'])-int(b['primary_hit'])
        if delta==1:gained.append(c['match_id'])
        if delta==-1:lost.append(c['match_id'])
        groups[b['date']]+=[delta,c['metrics']['wdl_accuracy']-b['metrics']['wdl_accuracy'],1]
    values=np.array(list(groups.values()))
    rng=np.random.default_rng(20261116)
    sampled=values[rng.integers(len(values),size=(repetitions,len(values)))].sum(axis=1)
    return {'n':len(before),'primary_net_hits':len(gained)-len(lost),
            'gained_match_ids':gained,'lost_match_ids':lost,'date_clusters':len(values),
            'primary_net_rate_95':np.quantile(sampled[:,0]/sampled[:,2],[.025,.975]).tolist(),
            'wdl_net_hits':int(values[:,1].sum()),
            'wdl_net_rate_95':np.quantile(sampled[:,1]/sampled[:,2],[.025,.975]).tolist(),
            'scope':'known-history development; date-cluster descriptive uncertainty'}


def evaluate_fit(core,spec,targets,role='test',temperature=1.,selector='joint_map'):
    records=[]
    for row in targets:
        matrix,mu,missing=distribution(core,spec,row,role,temperature)
        records.append(record_prediction(matrix,row,selector,base_rates=mu,missing=missing))
    return records


def split(rows, boundary):
    start,end=boundary
    if start>=end:
        raise ValueError('ordered calendar interval required')
    train=[r for r in rows if r['date']<start]
    targets=[r for r in rows if start<=r['date']<end]
    if train and targets and max(r['date'] for r in train)>=min(r['date'] for r in targets):
        raise ValueError('training intersects target block')
    return train,targets


def quality_reasons(candidate,baseline,comparison):
    reasons=[]
    if not candidate.get('n') or candidate['n']!=baseline.get('n'):
        return ['INSUFFICIENT_MATCHED_GATE']
    if candidate['primary_hits']<=baseline['primary_hits']:reasons.append('NO_STRICT_TOP1_GAIN')
    if candidate['wdl_hits']<baseline['wdl_hits']:reasons.append('WDL_REGRESSION')
    for key in ('score_log_loss','score_brier','wdl_brier','wdl_rps','zero_brier'):
        if candidate[key]>baseline[key]+1e-12:reasons.append('QUALITY_REGRESSION:'+key)
    if comparison.get('primary_net_rate_95',[0])[0]<=0:
        reasons.append('TOP1_NET_INTERVAL_INCLUDES_ZERO')
    return reasons


def verify_protocol(path,receipt_path):
    protocol=read(path);receipt=read(receipt_path)
    frozen=verify(read(receipt['path']))
    if (receipt['sha256']!=frozen['record_sha256'] or frozen['payload']!=protocol
            or frozen['track']!='research-preregistration' or frozen['stage']!='review'
            or protocol.get('schema')!='WCPP-NATIONS-REPAIR-PROTOCOL-1'):
        raise ValueError('actual unchanged preregistered protocol required')
    if sha(protocol['old_index'])!=protocol['old_index_sha256']:
        raise ValueError('original reference index changed')
    return protocol


def dependency_identity():
    from research_program import nations_phase
    from v2 import model as strength_engine
    modules=[Path(__file__),Path(legacy.__file__),Path(strength_engine.__file__),
             Path(nations_phase.__file__),Path(database.__file__),Path(safe_cli.__file__)]
    return {str(p.resolve()):sha(p) for p in modules}


def save_artifact(out,level,role,core,spec,temperature,selector,dataset_path,protocol_path):
    identity=digest({'dependencies':dependency_identity(),'dataset':sha(dataset_path),
                     'protocol':sha(protocol_path),'role':role,'recipe':spec,
                     'temperature':temperature,'selector':selector})[:12]
    version=f'WCPP-NATIONS-{level}-{role.upper()}-20261116-{identity}'
    artifact={'schema':SCHEMA,'version':version,'role':role,'numeric_role':'current' if role=='fixed_current' else 'test',
              'competition':'uefa.nations','stage':'league-phase','league_level':level,
              'cohort':f'uefa.nations:{level}:league-phase','core':core,'recipe':spec,
              'temperature':temperature,'selector':selector,'train_end':core['train_end'],
              'created_at':legacy.now(),'training_n':core.get('training_n',core.get('train_rows')),
              'dataset_path':str(dataset_path.resolve()),'dataset_sha256':sha(dataset_path),
              'protocol_path':str(Path(protocol_path).resolve()),'protocol_sha256':sha(protocol_path),
              'dependencies':dependency_identity(),'activation_allowed':False,'historical_asof_certified':False,
              'primary_scope':'STATISTICAL_EXPERIMENT_NOT_COMPREHENSIVE_RECOMMENDATION',
              'trained_features':['regulation_goals','home_indicator']+([] if role=='fixed_current' else ['team_attack','opponent_defence']),
              'untrained_features':['xg','injuries','lineup','motivation','full_rest','market']}
    path=out/(role+'-artifact.json');write(path,artifact)
    return {'path':str(path.resolve()),'sha256':sha(path),'version':version}


def run(db,protocol_path,receipt_path,out):
    protocol=verify_protocol(protocol_path,receipt_path)
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    original_index=read(protocol['old_index']);baseline_recipe=recipe()
    guards=dependency_identity();db_hash=sha(db)
    specs=[recipe(h,r,v) for h,r,v in itertools.product(protocol['strength_grid']['half_life_days'],
           protocol['strength_grid']['ridge'],protocol['shared_gamma_variance_grid'])]
    results={};routes={}
    for level in protocol['levels']:
        folder=out/level;folder.mkdir();dataset_path=folder/'dataset.json'
        database.export(db,dataset_path,'league-phase',level)
        rows=legacy.prepare(read(dataset_path)['rows'])
        if any(not classify(r)['complete'] for r in rows):
            raise ValueError('unverified historical row')
        old_artifact=read(original_index['routes'][level]['test']['path'])
        old_rows=read(old_artifact['dataset_path'])['rows']
        if (sha(original_index['routes'][level]['test']['path'])!=original_index['routes'][level]['test']['sha256']
                or sha(old_artifact['dataset_path'])!=old_artifact['dataset_sha256']):
            raise ValueError('original data or artifact checksum changed')
        training,targets=split(rows,protocol['partitions']['selection'])
        selection={};models={};selection_records={};failures={}
        for spec in specs:
            rid=recipe_id(spec)
            try:
                core=fitted(training,spec);models[rid]=core
                for selector in protocol['selection_rules']:
                    key=rid+':'+selector
                    records=evaluate_fit(core,spec,targets,selector=selector)
                    selection_records[key]=records;selection[key]=summarize(records)
            except ValueError as exc:
                failures[rid]=str(exc)
        baseline_key=recipe_id(baseline_recipe)+':joint_map'
        if baseline_key not in selection or len(targets)<protocol['minimum_selection']:
            raise ValueError(f'{level}: insufficient qualified chronological selection')
        candidates=[k for k,v in selection.items() if v['n']==len(targets)]
        chosen=min(candidates,key=lambda k:(-selection[k]['primary_hits'],selection[k]['score_log_loss'],
                   k!=baseline_key,k))
        rid,selector=chosen.split(':');selected_spec=next(s for s in specs if recipe_id(s)==rid)
        write(folder/'selection-fitted-cores.json',models)
        write(folder/'selection-grid-predictions.json',selection_records)
        selected={'key':chosen,'recipe':selected_spec,'selector':selector,
                  'selected_on':'2024 only','failures':failures}
        # Freeze the recipe/selector on disk before touching calibration/gate.
        write(folder/'locked-selection.json',selected)
        train_cal,cal_targets=split(rows,protocol['partitions']['calibration'])
        cal_core=fitted(train_cal,selected_spec)
        calibrations={}
        for t in protocol['temperature_grid']:
            records=evaluate_fit(cal_core,selected_spec,cal_targets,temperature=t,selector=selector)
            calibrations[str(t)]={'summary':summarize(records),'records':records}
        temperature=1.
        if len(cal_targets)>=protocol['minimum_calibration']:
            temperature=min(protocol['temperature_grid'],key=lambda t:(
                calibrations[str(t)]['summary']['score_log_loss'],t!=1.,t))
        write(folder/'locked-calibration.json',{'temperature':temperature,
              'n':len(cal_targets),'insufficient':len(cal_targets)<protocol['minimum_calibration']})
        write(folder/'calibration.json',{'core':cal_core,'candidates':calibrations})
        partitions={}
        for name,boundary in protocol['partitions'].items():
            train,targets=split(rows,boundary);old_train,_=split(old_rows,boundary)
            variants={};fit_records={}
            block_temperature=temperature if name in ('gate','known_recent_diagnostic') else 1.
            # Same-target data ablation with the exact original recipe.
            for key,data,role,spec,temp,sel in [
                ('old_coverage_current',old_train,'current',baseline_recipe,1.,'joint_map'),
                ('old_coverage_test',old_train,'test',baseline_recipe,1.,'joint_map'),
                ('fixed_current',train,'current',baseline_recipe,1.,'joint_map'),
                ('fixed_test',train,'test',baseline_recipe,1.,'joint_map'),
                ('strength_only',train,'test',{**selected_spec,'shared_gamma_variance':0.},1.,'joint_map'),
                ('tail_only',train,'test',{**baseline_recipe,'shared_gamma_variance':selected_spec['shared_gamma_variance']},1.,'joint_map'),
                ('selected_joint',train,'test',selected_spec,block_temperature,'joint_map'),
                ('challenger',train,'test',selected_spec,block_temperature,selector)]:
                # Selection/calibration scores are explicitly in-development;
                # only gate and later diagnostics use a fully locked recipe.
                core=fitted(data,spec,role)
                fit_records[key]={'core':core,'recipe':spec,'temperature':temp,'selector':sel,
                                  'fit_n':len(data),'fit_max_date':max(r['date'] for r in data)}
                variants[key]=evaluate_fit(core,spec,targets,role,temp,sel)
            summary={k:summarize(v) for k,v in variants.items()}
            comparisons={k:paired(variants['fixed_test'],variants[k]) for k in
                         ('strength_only','tail_only','selected_joint','challenger')}
            comparisons['data_refresh_current']=paired(variants['old_coverage_current'],variants['fixed_current'])
            comparisons['data_refresh_test']=paired(variants['old_coverage_test'],variants['fixed_test'])
            partitions[name]={'summary':summary,'paired':comparisons,'target_n':len(targets),
                              'selection_leakage_notice':'SELECTION_OR_CALIBRATION_USED' if name in ('selection','calibration') else 'LOCKED_RECIPE_KNOWN_HISTORY_DIAGNOSTIC'}
            write(folder/(name+'-predictions.json'),{'fitted':fit_records,'predictions':variants})
        gate=partitions['gate'];reasons=quality_reasons(gate['summary']['challenger'],
            gate['summary']['fixed_test'],gate['paired']['challenger'])
        final_routes={}
        for role,spec,temp,sel,numeric in [
            ('fixed_current',baseline_recipe,1.,'joint_map','current'),
            ('fixed_test',baseline_recipe,1.,'joint_map','test'),
            ('challenger',selected_spec,temperature,selector,'test')]:
            core=fitted(rows,spec,numeric)
            final_routes[role]=save_artifact(folder,level,role,core,spec,temp,sel,dataset_path,protocol_path)
        routes[level]=final_routes
        results[level]={'training_n':len(rows),'train_end':rows[-1]['date'],
            'training_year_counts':dict(Counter(r['date'][:4] for r in rows)),
            'selection':{'chosen':selected,'metrics':selection},'temperature':temperature,
            'partitions':partitions,'development_gate_passed':not reasons,'gate_reasons':reasons,
            'challenger_status':'SHADOW_ONLY_NO_AUTOMATIC_PROMOTION',
            'formal_acceptance_passed':False,'activation_allowed':False}
        write(folder/'report.json',results[level])
        print(json.dumps({'level':level,'training_n':len(rows),'selected':chosen,
                          'temperature':temperature,'gate_reasons':reasons}),flush=True)
    if dependency_identity()!=guards or sha(db)!=db_hash:
        raise ValueError('source/data changed during experiment')
    index={'schema':'WCPP-NATIONS-REPAIR-INDEX-1','created_at':legacy.now(),
           'protocol_path':str(Path(protocol_path).resolve()),'protocol_sha256':sha(protocol_path),
           'database_path':str(Path(db).resolve()),'database_sha256':db_hash,
           'routes':routes,'historical_asof_certified':False,'activation_allowed':False,
           'dependencies':guards}
    write(out/'candidates.json',index);write(out/'report.json',results)
    return index


def predict(path,fixture):
    safe_cli.validate_future(fixture)
    artifact=read(path)
    if (artifact.get('schema')!=SCHEMA or artifact['cohort']!=fixture['cohort']
            or artifact['league_level']!=fixture['league_level'] or artifact['stage']!=fixture['stage']):
        raise ValueError('exact repair artifact/fixture route required')
    if artifact['dependencies']!=dependency_identity():
        raise ValueError('executed repair dependencies differ from trained identities')
    for kind in ('dataset','protocol'):
        if sha(artifact[kind+'_path'])!=artifact[kind+'_sha256']:
            raise ValueError('repair dependency checksum:'+kind)
    if (artifact['train_end']!=artifact['core']['train_end'] or artifact['train_end']>=fixture['date']
            or legacy.aware(artifact['created_at'])>legacy.aware(fixture['cutoff_at'])):
        raise ValueError('training/availability boundary')
    matrix,mu,missing=distribution(artifact['core'],artifact['recipe'],fixture,
                                  artifact['numeric_role'],artifact['temperature'])
    h,a=np.indices(matrix.shape);primary=select_score(matrix,artifact['selector'])
    wdl=dict(zip(WDL,[float(matrix[h>a].sum()),float(matrix[h==a].sum()),float(matrix[h<a].sum())]))
    return {'version':artifact['version'],'role':artifact['role'],'status':'INACTIVE_RESEARCH_CANDIDATE',
            'input':fixture,'generated_at':legacy.now(),'matrix':matrix.tolist(),
            'matrix_hash':digest(matrix.tolist()),'primary':'-'.join(map(str,primary)),
            'primary_probability':float(matrix[tuple(primary)]),
            'statistical_top1':'-'.join(map(str,select_score(matrix))),
            'primary_scope':artifact['primary_scope'],'wdl':wdl,'wdl_reference':max(wdl,key=wdl.get),
            'expected_goals':[float((h*matrix).sum()),float((a*matrix).sum())],
            'base_rates':mu,'training_data_cutoff':artifact['train_end'],
            'artifact_sha256':sha(path),'recipe':artifact['recipe'],'temperature':artifact['temperature'],
            'selector':artifact['selector'],'unseen_teams_using_fitted_global_prior':missing,
            'actual_line':fixture.get('total_quote'),'formal_kpi_eligible':False,'active_route':False,
            'untrained_features':artifact['untrained_features']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',required=True);parser.add_argument('--protocol',required=True)
    parser.add_argument('--protocol-receipt',required=True);parser.add_argument('--out',required=True)
    args=parser.parse_args()
    result=run(args.db,args.protocol,args.protocol_receipt,args.out)
    print(json.dumps({'routes':len(result['routes']),'activation_allowed':False}))


if __name__=='__main__':main()
