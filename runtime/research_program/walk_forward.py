"""Fixed monthly historical reconstruction; never historical capture certification."""
import argparse
from collections import Counter
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'v4'))
from research_program import challengers
from v4 import main_model as legacy
from v2_test.evaluation import evaluate
from v2_test.data_contract import digest
from competition_scope import policy

MODELS = ('current_recipe', 'dynamic_poisson', 'negative_binomial_5', 'tempo_mixture')
PROTOCOL = {'mode':'RESULT_DATE_RECONSTRUCTION', 'block':'calendar_month',
            'minimum_training_rows':120, 'families':list(MODELS),
            'selection':'none; all four fixed before execution',
            'baseline':'per-group explicitly declared reference recipe refit on prior dates, not historical deployed weights',
            'historical_asof_certified':False, 'prospective_validation':False,
            'market_policy':'no guessed historical line or retrospective closing quote',
            'xg_used':False, 'activation_modified':False}


def splits(rows):
    rows = challengers.prepare(rows)
    entities = [(r['cohort'],r['stage'],r['date'],r['home'],r['away']) for r in rows]
    if len(entities) != len(set(entities)):
        raise ValueError('duplicate physical fixture under different IDs')
    for month in sorted({r['date'][:7] for r in rows}):
        origin = month + '-01'
        prior = [r for r in rows if r['date'] < origin]
        targets = [r for r in rows if r['date'][:7] == month]
        yield origin, prior, targets


def predict_block(prior, targets, reference_kind='weighted_baseline'):
    """Fit on supplied prior only; targets' labels are never read here."""
    if not targets or len({r['date'][:7] for r in targets}) != 1:
        raise ValueError('one explicit monthly target block required')
    origin = targets[0]['date'][:7] + '-01'
    if not prior or any(r['date'] >= origin for r in prior):
        raise ValueError('training must precede the entire target month')
    prior = challengers.prepare(prior)
    if reference_kind == 'weighted_baseline':
        checked = legacy.history(copy.deepcopy(prior))
        core = legacy.fit_baseline(checked)
    elif reference_kind == 'constant':
        means = np.clip([np.mean([r[k] for r in prior]) for k in ('home_goals','away_goals')],.08,8.)
    else:
        raise ValueError('unsupported reference recipe')
    strength = challengers.fit(prior)
    predictions = []
    for r in targets:
        if r['date'] <= prior[-1]['date']:
            raise ValueError('training is not strictly prior')
        # Only baseline is needed; do not run or select other Current recipes.
        if reference_kind == 'weighted_baseline':
            cm, meta = legacy.distribution({'baseline':core}, None, prior, r, 'baseline')
        else:
            # Match the generic route's original support/normalization policy.
            size=max(16,int(challengers.poisson.isf(1e-13,float(max(means))))+1)
            cm=np.outer(*[challengers.poisson.pmf(np.arange(size),rate) for rate in means])
            cm/=cm.sum()
            meta = {'unseen_teams':[]}
        mu = challengers.rates(strength, r)
        matrices = {'current_recipe':cm}
        for name, family, parameter in [('dynamic_poisson','poisson',None),
                                        ('negative_binomial_5','negative_binomial',5.),
                                        ('tempo_mixture','tempo_mixture',None)]:
            matrices[name] = challengers.distribution(mu, family, parameter)[0]
        predictions.append((r, matrices, meta['unseen_teams']))
    return predictions


def paired_delta(records, model):
    grouped = {}
    for r in records:
        h,a = r['actual'];actual=[h,a]
        diff=int(r['models'][model]['primary']==actual)-int(r['models']['current_recipe']['primary']==actual)
        grouped.setdefault(r['date'], []).append(diff)
    values=list(grouped.values()); n=sum(map(len,values))
    if not n:return None
    rng=np.random.default_rng(20261104); samples=[]
    for _ in range(1000):
        chosen=rng.integers(0,len(values),len(values))
        samples.append(sum(sum(values[i]) for i in chosen)/sum(len(values[i]) for i in chosen))
    return {'n':n,'net_hits':sum(sum(v) for v in values),
            'date_cluster_bootstrap95':np.quantile(samples,[.025,.975]).tolist(),
            'notice':'Development diagnostic interval; not a repeated-selection promotion test.'}


def run_cohort(rows, folder, reference_kind='weighted_baseline', reference_label='CURRENT_RECIPE_RECONSTRUCTED'):
    folder=Path(folder);folder.mkdir()
    evaluated={name:[] for name in MODELS}; records=[]; folds=[]; skips=[]
    for origin, prior, targets in splits(rows):
        if len(prior) < PROTOCOL['minimum_training_rows']:
            skips.extend({'match_id':r['match_id'],'date':r['date'],'reason':'WARMUP_LT_120'} for r in targets)
            continue
        ledger={'origin':origin,'train_n':len(prior),'training_end':prior[-1]['date'],
                'target_n':len(targets),'training_rows_sha256':digest(prior)}
        predictions=predict_block(prior, targets, reference_kind)
        arrays={name:[] for name in MODELS}
        for index,(r, matrices, missing) in enumerate(predictions):
            record={'match_id':r['match_id'],'date':r['date'],'cohort':r['cohort'],
                    'stage':r.get('stage','regular'),'home':r['home'],'away':r['away'],
                    'actual':[r['home_goals'],r['away_goals']], 'fold':origin,
                    'unseen_baseline_teams':missing,'models':{}}
            for name,m in matrices.items():
                primary=list(map(int,np.unravel_index(np.argmax(m),m.shape)))
                evaluated[name].append({'matrix':m,'home_goals':r['home_goals'],
                                        'away_goals':r['away_goals'],'primary_score':primary})
                # Pad to common support without changing probability mass.
                padded=np.zeros((81,81));padded[:m.shape[0],:m.shape[1]]=m
                arrays[name].append(padded)
                record['models'][name]={'primary':primary,'matrix_sha256':digest(padded.tolist()),
                    'matrix_file':origin+'.npz','matrix_index':index}
            records.append(record)
        np.savez_compressed(folder/(origin+'.npz'),**{k:np.stack(v) for k,v in arrays.items()})
        ledger['matrix_file_sha256']=hashlib.sha256((folder/(origin+'.npz')).read_bytes()).hexdigest()
        folds.append(ledger)
    metrics={name:evaluate(v) if v else None for name,v in evaluated.items()}
    report={'protocol':PROTOCOL,'reference_kind':reference_kind,'reference_label':reference_label,
            'current_comparison':reference_label=='CURRENT_RECIPE_RECONSTRUCTED',
            'population':len(rows),'predicted':len(records),
            'warmup':len(skips),'coverage':len(records)/len(rows), 'folds':folds,
            'metrics':metrics,'paired':{name:paired_delta(records,name) for name in MODELS[1:]},
            'actual_line_ou':{'n':0,'status':'NO_VERIFIED_HISTORICAL_MARKET_QUOTES'},
            'failure_count':0,'note':'Observed results/fixtures reconstructed retrospectively; no historical capture claim.'}
    (folder/'predictions.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))
    (folder/'excluded.json').write_text(json.dumps(skips,indent=2))
    (folder/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    return report


def reference_contract(route, allow_research_reference=False):
    if route is None:
        if not allow_research_reference:raise ValueError('no active Current route')
        return {'reference_kind':'constant','reference_label':'RESEARCH_CONSTANT_NO_CURRENT_ROUTE',
                'current_artifact_sha256':None}
    path=Path(route['artifact_path']);raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=route['artifact_sha256']:raise ValueError('active artifact checksum')
    artifact=json.loads(raw);engine=route.get('current_engine')
    if engine in (None,'legacy_weighted_anchor') and artifact.get('anchor',{}).get('execution_recipe')=='baseline':
        kind='weighted_baseline'
    elif engine=='v4_main_compat' and artifact.get('execution_recipe')=='baseline':
        kind='weighted_baseline'
    elif engine=='generic_poisson' and artifact.get('model_kind')=='constant':
        manifest=Path(artifact['dataset_manifest'])
        if hashlib.sha256(manifest.read_bytes()).hexdigest()!=artifact['dataset_manifest_sha256']:
            raise ValueError('active reference dataset manifest mismatch')
        kind='constant'
    else:raise ValueError('unsupported actual Current recipe; never silently replace it')
    return {'reference_kind':kind,'reference_label':'CURRENT_RECIPE_RECONSTRUCTED',
            'current_artifact_sha256':route['artifact_sha256']}


def main():
    p=argparse.ArgumentParser();p.add_argument('--histories',required=True);p.add_argument('--registry',required=True);p.add_argument('--out',required=True)
    p.add_argument('--allow-research-reference',action='store_true',help='Explicitly compare missing routes with research constant, never call it Current')
    a=p.parse_args()
    out=Path(a.out);out.mkdir(parents=True,exist_ok=False)
    registry=json.loads(Path(a.registry).read_text());inputs=sorted(Path(a.histories).glob('*/merged-history.json'))
    if not inputs:raise ValueError('no integrated history inputs')
    manifest=[]
    for path in inputs:
        rows=json.loads(path.read_text())
        if not rows:raise ValueError('empty input is not a trained route')
        co=rows[0]['cohort'];reference=reference_contract(registry['models'].get(co),a.allow_research_reference)
        manifest.append({'cohort':co,'input_group':path.parent.name,'path':str(path),
                         'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),**reference})
    # Persist protocol and input identity before any model fitting or scoring.
    (out/'protocol.json').write_text(json.dumps({'protocol':PROTOCOL,'inputs':manifest,
        'created_at':datetime.now(timezone.utc).isoformat(),'dynamic_config':challengers.CONFIG,
        'source_files_sha256':{str(p.relative_to(Path(__file__).parents[2])):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [Path(__file__),Path(challengers.__file__),Path(legacy.__file__),
                      Path(sys.modules[legacy.fit_baseline.__module__].__file__),
                      Path(sys.modules[evaluate.__module__].__file__)]},
        'registry_sha256':hashlib.sha256(Path(a.registry).read_bytes()).hexdigest()},indent=2))
    summary=[]
    for entry in manifest:
        rows=json.loads(Path(entry['path']).read_text());co=entry['cohort']
        result=run_cohort(rows,out/entry['input_group'],entry['reference_kind'],entry['reference_label'])
        summary.append({'cohort':co,'population':result['population'],'predicted':result['predicted'],
                        'input_group':entry['input_group'],'reference_label':entry['reference_label'],
                        'reference_kind':entry['reference_kind'],
                        'metrics':result['metrics'],'paired':result['paired']})
        print(co,result['predicted'],{k:round(v['metrics']['exact_top1'],4) if v else None for k,v in result['metrics'].items()},flush=True)
    coverage=[]
    for c in policy()['competitions']:
        groups=[x for x in summary if x['cohort'].split(':')[0]==c['code']]
        scored=sum(x['predicted'] for x in groups)
        coverage.append({'competition':c['code'],
            'status':'RECONSTRUCTION_RUN' if scored else ('WARMUP_ONLY_INSUFFICIENT_HISTORY' if groups else 'NOT_RUN_NO_INTEGRATED_VERIFIED_INPUT_IN_THIS_BATCH'),
            'predicted':scored,'cohort_groups':[x['input_group'] for x in groups]})
    (out/'summary.json').write_text(json.dumps({'results':summary,'scope18':coverage,'protocol':PROTOCOL},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
