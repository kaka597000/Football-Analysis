"""Bounded, isolated recovery and real-asset checks; never writes production forecasts."""
import argparse
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
ARCHIVES = {
    'core/assets.tar.gz': 'fec7b55d534597d7381e9ca9cbb5320fd7c6dbfad8b57a675eeef4706fc20b5b',
    'j1/history-selected.tar.gz': '66eb2b534cc17c3e897eca4252a82757f2f04cb3c4b8d0371c5b7e9a017639be',
    'integration/integration-assets.tar.gz': '76a77ab7b10aaad68f1f5ab7c870c63cae3dd0169b0d0a14e69bb5f9e5ea81fd',
    'research/20261105-nine-items/research.tar.gz': '806770392ad20248af02bce6a64c91e8bb3bf206a752bff7503fe56f551ae9aa',
    'research/20261105-remaining-nine-market/research.tar.gz': 'ebe1acee81f2909b8d0c5f9cf6c535c543ef3c656c502b1aecbc0c194366b655',
    'j1/compatible-source.tar.gz': '721bb2767e37dcf4dff40781a5bc1a5d7e9d425e321a3d329fb0d95cf0c88dec',
}


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as f:
        json.dump(value, f, indent=2, ensure_ascii=False, allow_nan=False)
        f.write('\n')


def restore(bundles, out):
    workspace = Path('/workspace'); workspace.mkdir(exist_ok=True)
    if (workspace/'WCPP').exists():
        raise FileExistsError('use a fresh isolated verification runner')
    receipts = []
    for name, expected in ARCHIVES.items():
        archive = bundles/name
        if sha256(archive.read_bytes()).hexdigest() != expected:
            raise ValueError('retained archive identity mismatch')
        destination = out/'original-source' if 'compatible-source' in name else workspace
        destination.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive) as t:
            for member in t:
                if not member.isfile():
                    continue
                rel = Path(member.name)
                if rel.is_absolute() or '..' in rel.parts:
                    raise ValueError('unsafe archive member')
                path = destination/rel; content = t.extractfile(member).read()
                if path.exists():
                    if path.read_bytes() != content:
                        raise ValueError('conflicting retained snapshots')
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open('xb') as f:
                    f.write(content)
        receipts.append({'archive':name, 'sha256':expected})
    code = workspace/'Football-Analysis'
    if code.exists() or code.is_symlink():
        raise FileExistsError(code)
    code.symlink_to(ROOT, target_is_directory=True)
    registry = json.loads((workspace/'WCPP/runtime/active-model.json').read_text())
    checked=[]
    for cohort, route in registry['models'].items():
        for prefix in ('', 'test_'):
            path=Path(route[prefix+'artifact_path'])
            if sha256(path.read_bytes()).hexdigest()!=route[prefix+'artifact_sha256']:
                raise ValueError('active artifact identity mismatch')
            checked.append({'cohort':cohort,'track':prefix or 'current','sha256':route[prefix+'artifact_sha256']})
    write(out/'restore-receipt.json', {'archives':receipts,'artifacts':checked,
         'isolated_runner':True,'production_modified':False})
    from build_retained_j1_manifest import build
    build('/workspace/WCPP/reports/japan-korea-20261010/jpn.1-artifact.json',
          '/workspace/WCPP/models/systematic-20261015-final-extended-003/jpn.1/artifact.json',
          out/'original-source/runtime', '/workspace/WCPP/reports/japan-korea-20261010/j1-history-summaries',
          'b6242ba06ec8e23b1613bb9ed32397f6ebad0987',out/'j1-compatibility.json')
    print('Original artifacts verified:',len(checked),flush=True)


def smoke(out):
    sys.path.insert(0,str(ROOT/'runtime'))
    from prediction_engine import predict_models
    from research.analysis_freeze import save,verify
    manifest=out/'j1-compatibility.json';m=json.loads(manifest.read_text());teams=sorted(m['team_mapping'])
    os.environ['WCPP_J1_COMPATIBILITY_MANIFEST']=str(manifest)
    now=datetime.now(timezone.utc);kickoff=now+timedelta(days=1)
    fixture={'match_id':'SYNTHETIC-REAL-ASSET-TEST-NOT-FORECAST','cohort':'jpn.1','competition':'jpn.1',
        'phase':'PreMatch','stage':'regular','season':'2026/27','gender':'male','age':'senior',
        'home':teams[0],'away':teams[1],'neutral':False,'date':kickoff.date().isoformat(),
        'cutoff_at':now.isoformat(),'kickoff_at':kickoff.isoformat(),
        'sources':[{'source_url':'https://example.org/SYNTHETIC-TEST','query_time':now.isoformat()}]}
    pair=predict_models(fixture,'/workspace/WCPP/runtime/active-model.json')
    assert pair['current']['matrix'] is not None and pair['experimental']['matrix'] is not None
    payload={'match_id':fixture['match_id'],'current_model':pair['current'],'test_model':pair['experimental'],
             'synthetic':True,'formal_kpi_eligible':False}
    with tempfile.TemporaryDirectory() as folder:
        a=save(payload,fixture,stage='prematch',track='current-test-pair',root=folder,idempotency_key='asset-test')
        b=save(payload,fixture,stage='prematch',track='current-test-pair',root=folder,idempotency_key='asset-test')
        assert a==b
        verify(json.loads(Path(a['path']).read_text(encoding='utf-8')))
        m['source_root']=str(Path(folder)/'missing-original-runtime')
        temp=Path(folder)/'missing-test.json';temp.write_text(json.dumps(m),encoding='utf-8')
        os.environ['WCPP_J1_COMPATIBILITY_MANIFEST']=str(temp)
        partial=predict_models(fixture,'/workspace/WCPP/runtime/active-model.json',retain_current_on_test_failure=True)
        assert partial['current']['matrix']==pair['current']['matrix'] and partial['experimental']['matrix'] is None
    write(out/'j1-smoke.json',{'real_artifacts':True,'synthetic_fixture':True,'mapped_teams':len(teams),
         'pair_loaded':True,'original_test_loader':pair['experimental']['retained_runtime'],
         'temporary_freeze_idempotent':True,'partial_current_exact':True,'active_model_modified':False})
    print('Real J1 paired and partial paths verified; target was synthetic',flush=True)


def probe(out):
    sys.path.insert(0,str(ROOT))
    from runtime.automation.collector import collect
    scope={'competition':'eng.1','cohort':'eng.1','stages':['regular'],'gender':'men','age':'senior'}
    result=collect(out/'live-probe',[scope],lookback_days=0,max_seconds=30)
    write(out/'live-probe-summary.json',{k:v for k,v in result.items() if k!='new_rows'})


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['restore','smoke','probe']);p.add_argument('--bundles',type=Path);p.add_argument('--out',required=True,type=Path);a=p.parse_args()
    a.out.mkdir(parents=True,exist_ok=True)
    if a.mode=='restore':restore(a.bundles,a.out)
    elif a.mode=='smoke':smoke(a.out)
    else:probe(a.out)
