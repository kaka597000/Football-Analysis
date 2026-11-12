"""One bounded job against a private data checkout, with append-only run status."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

REPO=Path(__file__).resolve().parents[2]


def execute(state_dir, mode, *, max_collection_seconds=300):
    if type(max_collection_seconds) is not int or not 30 <= max_collection_seconds <= 300:
        raise ValueError('collection budget must be an integer from 30 to 300 seconds')
    root=Path(state_dir).resolve()
    if root==REPO or root.is_relative_to(REPO/'runtime'):
        raise ValueError('separate private data checkout required')
    from ..competition_scope import permitted
    original_scopes=json.loads((root/'config/scopes.json').read_text())
    scopes=[s for s in original_scopes if permitted(s)]
    policy={'max_cohorts':len(scopes),'historical_only_scopes_skipped':len(original_scopes)-len(scopes),'max_requests':100,'lookback_days':2,'promotion':'NEVER',
            'max_collection_seconds':max_collection_seconds,
            'data_sources':'existing public JSON endpoints only','paid_api_calls':False}
    stamp=datetime.now(timezone.utc);rid=stamp.strftime('%Y%m%dT%H%M%S')+'-'+mode
    status={'run_id':rid,'started_at':stamp.isoformat(),'mode':mode,'policy':policy,
            'github_run_id':os.environ.get('GITHUB_RUN_ID'),
            'github_run_attempt':os.environ.get('GITHUB_RUN_ATTEMPT'),
            'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip(),
            'source_worktree_dirty':bool(subprocess.check_output(['git','status','--porcelain'],cwd=REPO)),
            'seed_sha256':hashlib.sha256((root/'seed/training.json.gz').read_bytes()).hexdigest(),
            'active_model_modified':False}
    manifest=json.loads((root/'seed/manifest.json').read_text())
    if status['seed_sha256']!=manifest['seed_sha256']:raise ValueError('seed identity mismatch')
    folder=root/'runs';folder.mkdir(parents=True,exist_ok=True)
    try:
        if mode=='collect':
            from .collector import collect
            status['result']=collect(root,scopes,lookback_days=2,max_seconds=max_collection_seconds)
        elif mode=='train':
            from .trainer import train
            status['result']=train(root,max_cohorts=len(scopes))
        else:raise ValueError('unknown job mode')
        status['status']='COMPLETED'
        if mode=='collect':
            status['status_scope']='REQUEST_EXECUTION_NOT_MODEL_READINESS'
            status['data_status']=status['result'].get('data_status','NOT_ASSESSED')
            status['market_model_ready']=status['result'].get('market_model_ready',False)
        if mode=='collect' and not status['result'].get('successful_requests'):
            status['status']='FAILED_NO_USABLE_RESPONSE'
        if mode=='train' and any(x.get('status')=='TRAINING_FAILED' for x in status['result'].get('cohorts',[])):
            status['status']='PARTIAL_TRAINING_FAILURE'
    except Exception as exc:
        status['status']='FAILED';status['error_type']=type(exc).__name__
        # No provider bodies, URLs with credentials, or arbitrary exception strings.
    status['finished_at']=datetime.now(timezone.utc).isoformat()
    with (folder/(rid+'.json')).open('x') as f:json.dump(status,f,ensure_ascii=False,indent=2,allow_nan=False)
    print(json.dumps({'run_id':rid,'mode':mode,'status':status['status'],'paid_api_calls':False,
                      'data_status':status.get('data_status'),'market_model_ready':status.get('market_model_ready')}))
    return status


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=('collect','train'));p.add_argument('--state-dir',required=True)
    p.add_argument('--max-collection-seconds',type=int,default=300)
    a=p.parse_args();r=execute(a.state_dir,a.mode,max_collection_seconds=a.max_collection_seconds)
    sys.exit(0 if r['status']=='COMPLETED' else 1)
