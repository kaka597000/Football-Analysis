"""Explicit checksum-locked J1 recovery, not a hash override or model promotion.

The caller opts in with WCPP_J1_COMPATIBILITY_MANIFEST. Old inference runs in a
separate interpreter; no historical module is imported into the active process.
"""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_manifest(path, role="test"):
    path=Path(path);m=json.loads(path.read_text(encoding='utf-8'))
    if m['schema']!='WCPP-RETAINED-J1-1':raise ValueError('unsupported J1 recovery manifest')
    if role not in ('current','test'):raise ValueError('explicit recovery track required')
    if sha(m[role+'_artifact'])!=m[role+'_sha256']:raise ValueError('retained artifact changed')
    m['manifest_sha256']=sha(path)
    if role=='current':return m
    root=Path(m['source_root']).resolve()
    actual={str(p.relative_to(root)):sha(p) for p in root.rglob('*.py')}
    if actual!=m['source_files']:raise ValueError('retained runtime source changed')
    test=json.loads(Path(m['test_artifact']).read_text(encoding='utf-8'))
    files={p.name:sha(p) for p in sorted((root/'optimization').glob('*.py')) if not p.name.startswith('test_')}
    digest=hashlib.sha256(json.dumps(files,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    if digest!=test['optimization_code_hash'] or sha(root/'optimization_tools/candidate.py')!=test['finalizer_sha256']:
        raise ValueError('retained source is not the artifact original dependency')
    if sha(Path(test['dataset'])/'manifest.json')!=test['dataset_manifest_hash']:
        raise ValueError('retained dataset manifest changed')
    m['manifest_sha256']=sha(path)
    return m


def fixture_for(m,fixture,role):
    if fixture['cohort']!='jpn.1' or fixture.get('competition')!='jpn.1':raise ValueError('J1-only recovery')
    result=copy.deepcopy(fixture)
    for side in ('home','away'):
        cid=fixture[side]
        if cid not in m['team_mapping']:raise ValueError('team has no verified J1 crosswalk')
        entry=m['team_mapping'][cid];events=set()
        for proof in entry['proof']:
            p=Path(proof['source_file'])
            if sha(p)!=proof['source_sha256']:raise ValueError('team identity evidence changed')
            wrapper=json.loads(p.read_text(encoding='utf-8'));event=wrapper['data']['header']
            if str(event['id'])!=proof['event_id']:raise ValueError('team evidence event conflict')
            candidates=[v for v in event['competitions'][0]['competitors'] if v['homeAway']==proof['side']]
            if len(candidates)!=1:raise ValueError('ambiguous source team side')
            tid=str(candidates[0]['team']['id'])
            if tid!=entry['current_id'] or entry['test_id']!='espn:'+tid or cid!=entry['test_id']:
                raise ValueError('unverified provider-team crosswalk')
            events.add(proof['event_id'])
        if len(events)<2:raise ValueError('two distinct retained team identities required')
        result[side]=entry[role+'_id']
    return result


def calculate(m,fixture):
    target=fixture_for(m,fixture,'test')
    script="""import json,sys
from pathlib import Path
root=Path(sys.argv[1]);sys.path.insert(0,str(root));sys.path.insert(0,str(root/'optimization_tools'))
import candidate
print(json.dumps(candidate.calculate(sys.argv[2],json.load(sys.stdin)),ensure_ascii=False,allow_nan=False))
"""
    result=subprocess.run([sys.executable,'-I','-c',script,m['source_root'],m['test_artifact']],
        input=json.dumps(target),text=True,encoding='utf-8',capture_output=True,timeout=90,check=False)
    if result.returncode:raise ValueError('retained J1 inference failed; inspect isolated validation log')
    raw=json.loads(result.stdout)
    raw['retained_runtime']={'source_commit':m['source_commit'],'manifest_sha256':m['manifest_sha256'],
         'source_dependencies_verified':True,'original_loader_executed':True,'artifact_unchanged':True}
    return raw
