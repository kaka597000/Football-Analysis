"""Reconcile one previously quarantined result from a pinned request ledger and two raw responses."""
import argparse
import gzip
from hashlib import sha256
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory

from raw_summary_adapter import authenticate


DATA = '076fe6d1a83281cb9393ee4887d1deced995eb0a'
ISSUE = 'e958e8e09e062d147e98714a466d69ff6d35ca56'
ADAPTER = 'd905fe4d02d06a7d1a0ed4df3d70b16f020305b8'
EVENT = 'espn:401912612'
SUMMARY = '26d6051f5e5b2c409117b407ab30f2f43550198a0bdf4f4876c7a3b16f142a11'
BOARD = 'db0ea747e915e537777c5f0ed17901b1a6e341b20741a8d64bedb3a8f760727b'


def git(repo,*args):
    return subprocess.check_output(['git','-C',str(repo),*args])


def extract(data, adapter, inputs, lock_file):
    if git(adapter,'rev-parse','HEAD').decode().strip() != ADAPTER or git(adapter,'diff','HEAD','--','runtime'):
        raise ValueError('expected the fixed retained-source verifier')
    sys.path.insert(0,str((adapter/'runtime').resolve()))
    from research.analysis_freeze import aware, verify
    from research.display_review import certify_retained

    lock = json.loads(lock_file.read_bytes())
    predictions = (lock_file.parent/'forecasts.jsonl').read_bytes()
    if (lock['panel'] != 'native' or sha256(predictions).hexdigest() != lock['forecasts_sha256']
            or sha256(inputs.read_bytes()).hexdigest() != lock['input_sha256']):
        raise ValueError('inputs or forecasts changed')
    prediction, = [p for p in map(json.loads,predictions.splitlines()) if p['id'] == EVENT and p.get('forecasts')]
    original, = [r for r in map(json.loads,inputs.read_bytes().splitlines()) if r['id'] == prediction['id']]
    archive=git(data,'show',ISSUE+':workspace-updates/20261123-evening-slate/evidence.tar.gz')
    with tarfile.open(fileobj=io.BytesIO(archive)) as t:
        record=verify(json.load(t.extractfile('freezes/401912612-comprehensive.json')))
    if record['record_sha256'] != original['freeze_sha256'] or record['evaluation_slot'] != 'FIRST_PREMATCH':
        raise ValueError('original first fixture mismatch')
    fixture=record['fixture']
    ledger=git(data,'show',DATA+':requests.jsonl').splitlines()
    refs={}
    with TemporaryDirectory() as tmp:
        state=Path(tmp)
        for h in (BOARD,SUMMARY):
            req, = [r for r in (json.loads(s) for s in ledger if h.encode() in s)
                    if r['status']=='OK' and r['raw']['sha256']==h]
            ref=req['raw']
            if (ref['path'] != 'raw/'+h+'.json.gz' or ref['captured_at'] != req['captured_at']
                    or ref['source_url'] != req['source_url']
                    or not aware(original['kickoff_at']) < aware(ref['captured_at']) < aware(lock['result_cutoff_at'])):
                raise ValueError('receipt identity or capture time mismatch')
            local=state/ref['path']
            local.parent.mkdir(exist_ok=True)
            local.write_bytes(git(data,'show',DATA+':'+ref['path']))
            refs[h]=ref
        meta=dict(refs[SUMMARY],scoreboard_reference=refs[BOARD])
        result=certify_retained('espn',fixture,meta,state,at=aware(lock['result_cutoff_at']))
        label=authenticate(gzip.decompress((state/meta['path']).read_bytes()),expected=original,
                           source_url=meta['source_url'],captured_at=meta['captured_at'],
                           cutoff_at=lock['result_cutoff_at'],evidence_sha256=SUMMARY)
    if label['score'] != [result['home_goals'],result['away_goals']]:
        raise ValueError('paired native stage and strict two-period score differ')
    return label


if __name__=='__main__':
    cli=argparse.ArgumentParser(description=__doc__)
    for name in ('data','adapter','inputs','lock','out'):
        cli.add_argument('--'+name,type=Path,required=True)
    a=cli.parse_args()
    certified=extract(a.data,a.adapter,a.inputs,a.lock)
    with a.out.open('x',encoding='utf-8') as stream:
        print(json.dumps(certified,sort_keys=True,separators=(',',':')),file=stream)
