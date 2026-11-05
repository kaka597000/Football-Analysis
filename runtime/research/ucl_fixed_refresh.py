"""Fixed-recipe UCL data refresh, isolated from active artifacts and forecasts."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit, parse_qs
from datetime import datetime, timezone, timedelta
import numpy as np
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT));sys.path.insert(0,str(ROOT/'v5'));sys.path.insert(0,str(ROOT/'continental'))
from weighted_model import legacy
import weighted_model as weighted
import ucl_ml as ml
from research.fixed_recipe_refresh import current_refit
from v2_test.evaluation import evaluate
COHORT='uefa.champions:NEW_FORMAT:league_phase'
STAGE='NEW_FORMAT:league_phase'

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read(path):return json.loads(Path(path).read_text())
def write(path,value):
    with Path(path).open('x') as f:json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False)

def verify_addition(row, cutoff):
    if (row.get('cohort')!=COHORT or row.get('competition')!='uefa.champions'
            or row.get('stage')!=STAGE or row.get('score_basis')!='regulation_90'
            or row.get('neutral') is not False):raise ValueError('explicit supported nonneutral UCL phase required')
    if any(k in row for k in ('home_xg','away_xg','xg_evidence','process','process_evidence')):raise ValueError('new process/xG channels require separate validation')
    if row.get('first_known_at') is not None:raise ValueError('first-known certification not supported by retrospective importer')
    e=row['score_evidence'];p=Path(e['source_path']);raw=read(p)
    if sha(p)!=e['response_sha256']:raise ValueError('raw hash conflict')
    h=raw['header'];event=str(h['id']);c=h['competitions'][0]
    u=urlsplit(row.get('source_url',''));q=parse_qs(u.query)
    if u.scheme!='https' or u.netloc!='site.api.espn.com' or u.path!='/apis/site/v2/sports/soccer/uefa.champions/summary' or set(q)!={'event'} or q['event']!=[event]:raise ValueError('unreviewed source URL or event')
    if any(type(row.get(k))is not int or not 0<=row[k]<=30 for k in ('home_goals','away_goals')):raise ValueError('invalid score values')
    if h.get('league',{}).get('slug')!='uefa.champions':raise ValueError('raw competition')
    if int(h.get('season',{}).get('year',0))<2024:raise ValueError('pre-new-format season')
    phase=h.get('season',{}).get('slug','')+' '+h.get('season',{}).get('name','')
    if 'league-phase' not in phase.lower() and 'league phase' not in phase.lower():raise ValueError('raw phase unknown')
    if row.get('provider_key')!='espn:'+event:raise ValueError('raw event identity')
    if c['status']['type']['name']!='STATUS_FULL_TIME' or not c['status']['type']['completed']:raise ValueError('raw not regulation full time')
    if c.get('neutralSite') is not False:raise ValueError('raw neutral unknown or unsupported')
    sides={t['homeAway']:t for t in c['competitors']}
    for side in ('home','away'):
        t=sides[side]
        if row[side]!='espn:'+str(t['team']['id']):raise ValueError('raw team')
        periods=t.get('linescores',[])
        if len(periods)!=2 or sum(int(p['displayValue'])for p in periods)!=row[side+'_goals']:raise ValueError('raw two-period score')
        if int(t['score'])!=row[side+'_goals']:raise ValueError('raw final score')
    kick=legacy.aware(c.get('date')or h.get('date'))
    observed=legacy.aware(row['observed_at']);verified=legacy.aware(e.get('verified_at')or e['query_time'])
    if kick.date().isoformat()!=row['date'] or not kick+timedelta(minutes=90)<=verified<=observed<=cutoff<=datetime.now(timezone.utc):raise ValueError('raw time boundary')
    if e.get('query_time') and not kick+timedelta(minutes=90)<=legacy.aware(e['query_time'])<=observed:raise ValueError('network observation time boundary')
    return row

def merge(old, additions):
    rows=copy.deepcopy(old);events={r.get('provider_key')or r.get('providerkey'):r for r in rows};keys={(r['date'],r['home'],r['away']):r for r in rows};new=[]
    for row in additions:
        prev=events.get(row['provider_key'])or keys.get((row['date'],row['home'],row['away']))
        if prev:
            if any(prev[k]!=row[k]for k in ('date','home','away','home_goals','away_goals','neutral','stage')):raise ValueError('historical identity/score conflict')
            continue
        rows.append(copy.deepcopy(row));events[row['provider_key']]=row;keys[(row['date'],row['home'],row['away'])]=row;new.append(row['provider_key'])
    return legacy.history(rows),new

def test_refit(original, rows, built, report_hash):
    if original['code_hash']!=ml.code_hash()or original['config']!=ml.CONFIG or original['feature_names']!=ml.FEATURES:raise ValueError('original UCL ML contract mismatch')
    items,folds=ml.rolling_items(rows,rows)
    if not items or any(f['fit_end']>=f['first_target']for f in folds):raise ValueError('invalid prior-only ML folds')
    booster=ml.fit_booster(items);candidate=copy.deepcopy(original)
    candidate.update(history=copy.deepcopy(rows),core=legacy.fit_core(rows),booster_model=booster.model_to_string(),built_at=built,observed_at=max((r['observed_at']for r in rows),key=legacy.aware),train_end=rows[-1]['date'],report_hash=report_hash,formal_validation=False,formal_kpi_eligible=False,feature_xg_match_count=sum(bool(r.get('xg_evidence'))for r in rows),feature_process_match_count=sum(bool(r.get('process_evidence'))for r in rows),feature_importance_gain=dict(zip(ml.FEATURES,booster.feature_importance('gain').tolist())))
    if candidate['alpha']!=original['alpha']:raise ValueError('fixed alpha changed')
    return candidate,{'training_matches':len(items),'folds':folds,'alpha':candidate['alpha']}

def matrices(current,test,row):
    teams={r[s]for r in current['anchor']['history']for s in ('home','away')}
    if any(row[s]not in teams for s in ('home','away')):return None
    a=current['anchor'];base,meta=legacy.distribution(a['core'],a['form'],a['history'],row,a['execution_recipe'])
    if meta['unseen_teams']:return None
    rates=legacy.rates(test['core']['long'],row)[:2];features=ml.feature_matrix(test['history'],row,rates);booster=ml.lgb.Booster(model_str=test['booster_model']);mm,_=ml.ml_matrix(booster,features,rates,test['core']['long']['nuisance']['dixon_coles']);dc,_=legacy.distribution(test['core'],None,test['history'],row,'dc365');return base,(1-test['alpha'])*dc+test['alpha']*mm

def monthly(rows, old_current, old_test, out):
    records=[];folds=[];evaluated={k:[]for k in ['old_current','old_test','refreshed_current','refreshed_test']};built=datetime.now(timezone.utc).isoformat()
    for month in sorted({r['date'][:7]for r in rows}):
        origin=month+'-01'
        if origin<=old_current['anchor']['train_end']:continue
        prior=[r for r in rows if r['date']<origin];targets=[r for r in rows if r['date'][:7]==month]
        if len(prior)<120:continue
        c=current_refit(old_current,prior,built,'RECONSTRUCTION');t,ml_info=test_refit(old_test,prior,built,'RECONSTRUCTION');fold={'origin':origin,'training_end':prior[-1]['date'],'n_prior':len(prior),'n_targets':len(targets),'training_ids':[r['match_id']for r in prior],'training_rows_sha256':legacy.digest(prior),'ml_training_matches':ml_info['training_matches']};arrays={k:[]for k in ['old_current','old_test','refreshed_current','refreshed_test']}
        for row in targets:
            old=matrices(old_current,old_test,row);fresh=matrices(c,t,row);record={'match_id':row['match_id'],'provider_key':row.get('provider_key'),'date':row['date'],'actual':[row['home_goals'],row['away_goals']],'models':{}}
            for label,pair in [('old',old),('refreshed',fresh)]:
                for index,role in enumerate(('current','test')):
                    name=label+'_'+role
                    if pair is None:record['models'][name]={'status':'ABSTAIN_UNSEEN_TEAM'};continue
                    m=pair[index];pred=tuple(map(int,np.unravel_index(m.argmax(),m.shape)));wdl=tuple(map(float,legacy.probabilities(m)));record['models'][name]={'status':'PREDICTED','primary':list(pred),'wdl':wdl,'wdl_reference':int(np.argmax(wdl)),'matrix_index':len(arrays[name]),'matrix_sha256':legacy.digest(m.tolist())};arrays[name].append(m);evaluated[name].append({'matrix':m,'home_goals':row['home_goals'],'away_goals':row['away_goals'],'primary_score':list(pred)})
            records.append(record)
        matrixfile=out/(month+'.npz');np.savez_compressed(matrixfile,**{k:np.stack(v)if v else np.empty((0,51,51))for k,v in arrays.items()});fold.update(matrix_file=matrixfile.name,matrix_sha256=sha(matrixfile));folds.append(fold)
    summary={}
    for role in ('current','test'):
        a='old_'+role;b='refreshed_'+role;paired=[r for r in records if all(r['models'][k]['status']=='PREDICTED'for k in (a,b))];gain=loss=0
        for r in paired:
            oldhit=r['models'][a]['primary']==r['actual'];newhit=r['models'][b]['primary']==r['actual'];gain+=newhit and not oldhit;loss+=oldhit and not newhit
        summary[role]={'population':len(records),'paired_n':len(paired),'new_only_hits':gain,'lost_only_hits':loss,'net_hits':gain-loss,'models':{}}
        for k in (a,b):
            valid=[r for r in records if r['models'][k]['status']=='PREDICTED'];summary[role]['models'][k]={'evaluation':evaluate(evaluated[k])if evaluated[k]else None,'available':len(valid),'top1_hits':sum(r['models'][k]['primary']==r['actual']for r in valid),'wdl_hits':sum(r['models'][k]['wdl_reference']==(0 if r['actual'][0]>r['actual'][1]else 2 if r['actual'][0]<r['actual'][1]else 1)for r in valid)}
    write(out/'predictions.json',records);write(out/'folds.json',folds);write(out/'summary.json',{'mode':'RESULT_DATE_RECONSTRUCTION_NOT_HISTORICAL_ASOF_NOT_PROSPECTIVE','metrics':summary});return summary

def run(args):
    out=Path(args.out);out.mkdir(parents=True,exist_ok=False);registry=read(args.registry);route=registry['models'][COHORT];tracked={str(Path(args.registry)):sha(args.registry)}
    for p,h in [('artifact_path','artifact_sha256'),('test_artifact_path','test_artifact_sha256')]:
        if sha(route[p])!=route[h]:raise ValueError('active artifact checksum');
        tracked[route[p]]=sha(route[p])
    current=read(route['artifact_path']);test=read(route['test_artifact_path']);cutoff=legacy.aware(args.cutoff)
    write(out/'protocol.json',{'protocol':'WCPP-UCL-FIXED-REFRESH-1','locked_at':datetime.now(timezone.utc).isoformat(),'cutoff':args.cutoff,'rows_sha256':sha(args.rows),'execution_code_sha256':sha(__file__), 'algorithm_code_hashes':{'current':weighted.code_hash(),'test':ml.code_hash()},'original_sha256':tracked,'fixed_current_recipe':current['anchor']['execution_recipe'],'fixed_weights':current['weights'],'fixed_test_alpha':test['alpha'],'fixed_features':test['feature_names'],'fixed_config':test['config'],'historical_asof_certified':False,'automatic_promotion':False,'selection':'none; both original recipes fixed; monthly first-day training strictly before target month'})
    additions=[];rejected=[]
    for row in read(args.rows):
        try:additions.append(verify_addition(row,cutoff))
        except (ValueError,KeyError,TypeError,OSError)as exc:rejected.append({'match_id':row.get('match_id'),'reason':str(exc)})
    write(out/'row-audit.json',{'accepted':len(additions),'rejected':rejected});rows,new=merge(current['anchor']['history'],additions);testrows,_=merge(test['history'],additions)
    if [r['match_id']for r in rows]!=[r['match_id']for r in testrows]:raise ValueError('unpaired updated histories')
    write(out/'merged-history.json',rows);built=datetime.now(timezone.utc).isoformat();c=current_refit(current,rows,built,'INACTIVE_REFRESH_RESEARCH');t,info=test_refit(test,testrows,built,'INACTIVE_REFRESH_RESEARCH')
    lineage={'id':'UCL-FIXED-REFRESH-20261105','active':False,'status':'INACTIVE_AWAITING_PROSPECTIVE_VALIDATION','historical_asof_certified':False,'original_acceptance_applies_to_refit':False,'refit_acceptance_status':'NOT_EVALUATED_PROSPECTIVELY','new_event_ids':new,'training_end':rows[-1]['date'],'training_n':len(rows),'old_training_end':current['anchor']['train_end'],'coverage':'RETAINED_SOURCE_PARTIAL_NOT_INDEPENDENT_FULL_INVENTORY'}
    c['refresh_experiment']=copy.deepcopy(lineage);t['refresh_experiment']=copy.deepcopy(lineage);write(out/'current-candidate.json',c);write(out/'test-candidate.json',t);write(out/'ml-training.json',info);evaluation=out/'monthly';evaluation.mkdir();summary=monthly(rows,current,test,evaluation)
    if any(sha(p)!=h for p,h in tracked.items()):raise ValueError('active assets changed')
    write(out/'execution.json',{'new_rows':len(new),'old_n':len(current['anchor']['history']),'new_n':len(rows),'old_end':current['anchor']['train_end'],'new_end':rows[-1]['date'],'models':{'current':sha(out/'current-candidate.json'),'test':sha(out/'test-candidate.json')},'active_assets_unchanged':True,'automatic_promotion':False,'monthly':summary})
    print(json.dumps({'new_rows':len(new),'history':len(rows),'end':rows[-1]['date'],'monthly':summary},indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--registry',default='/workspace/WCPP/runtime/active-model.json');p.add_argument('--rows',required=True);p.add_argument('--cutoff',required=True);p.add_argument('--out',required=True);run(p.parse_args())
