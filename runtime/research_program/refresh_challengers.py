"""Forward-only refits; diagnostic and final-refit identities remain separate."""
import argparse,copy,hashlib,json,sys
from pathlib import Path
from datetime import date,datetime,timedelta,timezone
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from competition_scope import require
from research_program.ingest_forward_history import normalize
from research_program.challengers import fit,experiment
from prediction_engine import source_identity
from v2_test.data_contract import aware,digest

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def fixture_key(r):return (r['cohort'],r['date'],r['home'],r['away'])

def merge_rows(base,new):
    merged=copy.deepcopy(base);by_key={fixture_key(r):r for r in merged};by_id={r['match_id']:r for r in merged};duplicates=0;added=0
    if len(by_id)!=len(merged) or len(by_key)!=len(merged):raise ValueError('duplicate baseline identity')
    for extra in new:
        old=by_id.get(extra['match_id']) or by_key.get(fixture_key(extra))
        if old is None:
            nearby=[r for r in merged if (r['cohort'],r['home'],r['away'])==(extra['cohort'],extra['home'],extra['away']) and abs((date.fromisoformat(r['date'])-date.fromisoformat(extra['date'])).days)<=1]
            if nearby:raise ValueError('cross-source near-date fixture conflict requires review')
            value=copy.deepcopy(extra);merged.append(value);by_id[value['match_id']]=value;by_key[fixture_key(value)]=value;added+=1
        else:
            if fixture_key(old)!=fixture_key(extra) or any(old[k]!=extra[k] for k in ('home_goals','away_goals','neutral')):
                raise ValueError('cross-source identity/score/venue conflict requires review')
            duplicates+=1
            old['forward_summary_evidence']=copy.deepcopy(extra['evidence'])
            old['provider_match_ids']=sorted(set(old.get('provider_match_ids',[])+[extra['match_id']]))
            old['observed_at']=max((old['observed_at'],extra['observed_at']),key=aware)
            if not old.get('season') and extra.get('season'):
                old['season']=extra['season'];old['season_evidence']=copy.deepcopy(extra['season_evidence'])
    return merged,{'new_unique_rows':added,'duplicate_provider_rows_merged':duplicates,'total_rows':len(merged)}


def refresh(registry_path,collection_root,out,cohorts):
    collection_root=Path(collection_root);out=Path(out);out.mkdir(parents=True,exist_ok=False)
    new=normalize(json.loads((collection_root/'rows.json').read_text(encoding='utf-8')),collection_root)
    registry=json.loads(Path(registry_path).read_text(encoding='utf-8'));initial_registry_hash=sha(registry_path);summary=[]
    for cohort in cohorts:
        require({'cohort':cohort});route=registry['models'][cohort];source=Path(route['artifact_path'])
        if sha(source)!=route['artifact_sha256']:raise ValueError('source artifact checksum')
        value=json.loads(source.read_text(encoding='utf-8'));base=value.get('history') or value.get('anchor',{}).get('history')
        if not base:raise ValueError('retained baseline history required')
        try:rows,audit=merge_rows(base,[r for r in new if r['cohort']==cohort])
        except ValueError as error:
            summary.append({'cohort':cohort,'status':'BLOCKED_SOURCE_CONFLICT','reason':str(error)});continue
        diagnostic=experiment(rows)
        forward={'schema':'WCPP-DYNAMIC-FORWARD-CANDIDATE-1','version':'WCPP-DYNAMIC-FORWARD-0.1',
                 'cohort':cohort,'generated_at':datetime.now(timezone.utc).isoformat(),'model':fit(rows),
                 'distribution':{k:diagnostic['selected'][k] for k in ('family','parameter')},
                 'lineage':{'forward_refit_uses_inspected_diagnostic_labels':True,'diagnostic_metrics_belong_to_other_model':True,
                            'source_current_sha256':route['artifact_sha256'],'new_collection_rows_sha256':sha(collection_root/'rows.json'),
                            'source_identity':source_identity(),'input_available_before_training':True,'first_known_inferred':False},
                 'status':'INACTIVE_FORWARD_ONLY_NOT_VALIDATED','promotion':False,'historical_asof_certified':False}
        folder=out/cohort;folder.mkdir();(folder/'merged-history.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        for name,body in [('diagnostic',diagnostic),('forward-candidate',forward)]:
            (folder/(name+'.json')).write_text(json.dumps(body,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
        summary.append({'cohort':cohort,'status':forward['status'],'rows':audit,'forward_training_end':forward['model']['training_end'],
                        'family':forward['distribution'],'forward_sha256':sha(folder/'forward-candidate.json'),
                        'development_diagnostic_not_for_forward_refit':diagnostic['diagnostic']['metrics']})
        if sha(source)!=route['artifact_sha256']:raise ValueError('source artifact modified')
        print(cohort,flush=True)
    if sha(registry_path)!=initial_registry_hash:raise ValueError('registry changed during experiment')
    report={'generated_at':datetime.now(timezone.utc).isoformat(),'source_registry_sha256':initial_registry_hash,
            'collection_rows':len(new),'cohorts':summary,'registry_modified':False,'accuracy_improvement_proven':False,
            'notice':'Forward refit uses all currently captured labels; old diagnostic is not its unseen validation. Collection coverage remains incomplete.'}
    (out/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--registry',required=True);p.add_argument('--collection-root',required=True);p.add_argument('--out',required=True);p.add_argument('--cohorts',nargs='+',required=True);a=p.parse_args()
    refresh(a.registry,a.collection_root,a.out,a.cohorts)
