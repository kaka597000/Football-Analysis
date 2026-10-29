"""Fixed preregistered xG shadow refits and a separately frozen future track."""
import argparse,importlib.util,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'optimization'))
from schema import save,file_hash,aware,now
import candidate
SCOPE=('eng.1','esp.1','ger.1','ita.1','fra.1')
def finalize(run,spec,out):
 run=Path(run);spec=Path(spec);spec.mkdir(exist_ok=False);p=json.loads((run/'protocol.json').read_text());cfg=next(c for c in p['candidates'] if c['name']=='M3-xg-a0.5');save(spec/'protocol.json',p)
 for code in SCOPE:
  d=spec/code;d.mkdir();save(d/'development-selection.json',dict(selected=cfg,selection_mode='FIXED_PREREGISTERED_XG_SHADOW_NOT_DEVELOPMENT_WINNER',source_protocol=str(run/'protocol.json'),source_protocol_sha256=file_hash(run/'protocol.json'),audit_artifact=str(run/code/'audit-M3-xg-a0.5-artifact.json'),audit_artifact_sha256=file_hash(run/code/'audit-M3-xg-a0.5-artifact.json'),reason='User-authorized xG full-history reuse; no new hyperparameter search; do not replace selected/default/current/test'))
 index=candidate.finalize(spec,out)
 for row in index:
  art=json.loads(Path(row['artifact']).read_text());assert art['trained_xg_channel'] and art['xg_oof_bilateral_targets_ge3']>0
 save(Path(out)/'shadow-policy.json',dict(status='XG_SHADOW_NOT_PROMOTED',configuration=cfg,cohorts=list(SCOPE),index_sha256=file_hash(Path(out)/'candidate-index.json'),source_sha256=file_hash(__file__),future_freeze_track='systematic_xg_shadow',active_switch=False,selected_default_replaced=False,accuracy_not_verified=True,source_run=str(run),note='Finalizer generic selected fields identify the fixed configuration spec, not a development win. No audit-based choice.'))
 return dict(cohorts=len(index),role='XG_SHADOW_NOT_PROMOTED')
def future(artifact,fixture,out,freeze_root=None):
 if Path(out).exists():raise FileExistsError('output exists')
 target=json.loads(Path(fixture).read_text());current=aware(now());cutoff=aware(target['cutoff_at']);kickoff=aware(target['kickoff_at']);a=json.loads(Path(artifact).read_text())
 if a['configuration']['name']!='M3-xg-a0.5' or a['cohort'] not in SCOPE or not a['trained_xg_channel']:raise ValueError('requires fixed five-league trained xG shadow')
 policy=Path(artifact).parent.parent/'shadow-policy.json';p=json.loads(policy.read_text())
 if p['source_sha256']!=file_hash(__file__):raise ValueError('shadow tool changed')
 if cutoff>current or kickoff<=current or aware(a['created_at'])>cutoff or aware(json.loads((Path(a['dataset'])/'manifest.json').read_text())['created_at'])>cutoff:raise ValueError('model/data/cutoff not available before kickoff')
 result=candidate.calculate(artifact,target);result.update(role='XG_SHADOW_NOT_PROMOTED',shadow_policy_sha256=file_hash(policy));path=Path(__file__).resolve().parents[1]/'research/analysis_freeze.py';spec=importlib.util.spec_from_file_location('_xg_shadow_freeze',path);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);kwargs=dict(stage='prematch',track='systematic_xg_shadow')
 if freeze_root:kwargs['root']=freeze_root
 result['freeze']=mod.save(result,target,**kwargs);save(out,result);return dict(primary=result['primary'],freeze=result['freeze'],active=False)
if __name__=='__main__':
 p=argparse.ArgumentParser();s=p.add_subparsers(dest='command',required=True);f=s.add_parser('finalize');[f.add_argument('--'+a,required=True) for a in ('run','spec','out')];f=s.add_parser('predict');[f.add_argument('--'+a,required=True) for a in ('artifact','fixture','out')];f.add_argument('--freeze-root');a=vars(p.parse_args());c=a.pop('command');print(json.dumps(finalize(**a) if c=='finalize' else future(**a),ensure_ascii=False))
