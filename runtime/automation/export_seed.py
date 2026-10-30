"""Export small, identity-checked current-model histories to private storage."""
import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def export(root, out):
    root=Path(root);out=Path(out);(out/'seed').mkdir(parents=True,exist_ok=True)
    (out/'config').mkdir(exist_ok=True)
    if (out/'seed/training.json.gz').exists():raise FileExistsError('seed already exported')
    registry=json.loads((root/'runtime/active-model.json').read_text())
    stamp=datetime.now(timezone.utc).isoformat();rows=[];scopes=[];sources=[]
    for cohort,entry in registry['models'].items():
        path=Path(entry['artifact_path'])
        if sha(path)!=entry['artifact_sha256']:raise ValueError('active artifact identity mismatch')
        data=json.loads(path.read_text());base=data.get('anchor',data)
        history=base.get('history') or data.get('history')
        if not history:
            manifest=Path(data['dataset_manifest']);folder=manifest.parent
            if sha(manifest)!=data['dataset_manifest_sha256']:raise ValueError('dataset manifest identity mismatch')
            if (folder/'history.json').exists():history=json.loads((folder/'history.json').read_text())
            else:
                fixtures=[json.loads(x) for x in (folder/'fixtures.jsonl').read_text().splitlines() if x]
                labels={x['match_id']:x for x in map(json.loads,(folder/'labels.jsonl').read_text().splitlines())}
                history=[{**x,**labels[x['match_id']]} for x in fixtures if x['match_id'] in labels]
        competition=data.get('forecast_scope',{}).get('competition',cohort.split(':')[0])
        stages=data.get('forecast_scope',{}).get('stages')
        if not stages:stages=sorted({r.get('stage','regular') for r in history if r.get('stage','regular')})
        if not stages:raise ValueError('no explicit source stages')
        scope={'cohort':cohort,'competition':competition,'stages':stages,'gender':'men','age':'senior'}
        scopes.append(scope);n=0
        for r in history:
            day=r['date'][:10]
            if day>=stamp[:10]:continue
            goals=[r.get('home_goals'),r.get('away_goals')]
            if any(type(x) is not int or x<0 for x in goals):raise ValueError('invalid source goals')
            def team(value):
                value=str(value)
                return 'espn:'+value if value.isdigit() and 'espn.com' in r.get('source_url','') else value
            neutral=r.get('neutral')
            if type(neutral) is int and neutral in (0,1):neutral=bool(neutral)
            rows.append({'match_id':str(r['match_id']),'cohort':cohort,'competition':competition,
                'stage':r.get('stage',stages[0]),'date':day,'season':r.get('season'),
                'home':team(r['home']),'away':team(r['away']),'neutral':neutral,
                'home_goals':goals[0],'away_goals':goals[1],'score_basis':'regulation_90',
                'dataset_kind':'SEED_RETROSPECTIVE','known_at':stamp,
                'first_known_at':r.get('first_known_at'),'source_url':r.get('source_url'),
                'source_artifact_sha256':entry['artifact_sha256']});n+=1
        sources.append({'cohort':cohort,'source_artifact_sha256':entry['artifact_sha256'],'rows':n})
    content=json.dumps(rows,ensure_ascii=False,sort_keys=True,allow_nan=False).encode()
    with (out/'seed/training.json.gz').open('wb') as f:f.write(gzip.compress(content,mtime=0))
    (out/'config/scopes.json').write_text(json.dumps(scopes,ensure_ascii=False,indent=2))
    manifest={'created_at':stamp,'registry_version':registry['version'],'registry_sha256':sha(root/'runtime/active-model.json'),
              'rows':len(rows),'cohorts':len(scopes),'seed_sha256':sha(out/'seed/training.json.gz'),
              'sources':sources,'scope':'RETROSPECTIVE_BASELINE_NOT_EVENT_TRAINING_OR_FULL_DATABASE_BACKUP'}
    (out/'seed/manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    return manifest


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',default='/workspace/WCPP');p.add_argument('--out',required=True)
    a=p.parse_args();result=export(a.root,a.out);print(json.dumps({k:result[k] for k in ('rows','cohorts','seed_sha256')}))
