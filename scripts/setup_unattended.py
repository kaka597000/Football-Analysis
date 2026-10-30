"""Install a repo-scoped data deploy key using the operator's GitHub permissions.

Requires gh with repository creation, deploy-key, secret and variable permissions.
Never displays key material. Run after the private seed repository is populated.
"""
import argparse
import json
import os
import shutil
from pathlib import Path
import subprocess
import tempfile

CODE='kaka597000/Football-Analysis'
DATA='kaka597000/kaka597000-Football-Analysis-Data'


def gh(*args, input_bytes=None):
    r=subprocess.run(['gh',*args],input=input_bytes,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    if r.returncode:
        raise RuntimeError('GitHub operation denied or failed: '+' '.join(args[:3]))
    return r.stdout.decode().strip()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--code',default=CODE);p.add_argument('--data',default=DATA)
    p.add_argument('--seed-dir',help='local verified seed/config export to populate a new private repository')
    a=p.parse_args()
    try:gh('repo','view',a.data,'--json','isPrivate','--jq','.isPrivate')
    except RuntimeError:
        gh('repo','create',a.data,'--private','--description','Private WCPP research state')
    if gh('repo','view',a.data,'--json','isPrivate','--jq','.isPrivate')!='true':
        raise ValueError('data repository must exist and be private')
    if a.seed_dir:
        source=Path(a.seed_dir)
        if not (source/'seed/manifest.json').is_file() or not (source/'config/scopes.json').is_file():
            raise ValueError('verified seed and scope config required')
        with tempfile.TemporaryDirectory(prefix='wcpp-private-seed-') as directory:
            checkout=Path(directory)/'data'
            gh('auth','setup-git')
            gh('repo','clone',a.data,str(checkout))
            exists=(checkout/'seed/manifest.json').exists()
            if exists:
                old=json.loads((checkout/'seed/manifest.json').read_text())
                new=json.loads((source/'seed/manifest.json').read_text())
                if old['seed_sha256']!=new['seed_sha256']:raise ValueError('refusing to replace a different existing seed')
            else:
                for folder in ('seed','config'):shutil.copytree(source/folder,checkout/folder)
                (checkout/'.gitignore').write_text('.env\n*.key\n__pycache__/\n.venv/\n')
                for args in [('config','user.name','WCPP operator'),('config','user.email','wcpp-operator@users.noreply.github.com'),
                             ('add','seed','config','.gitignore'),('commit','-m','Initialize private WCPP training seed'),
                             ('branch','-M','main'),('push','origin','main')]:
                    subprocess.run(['git','-C',str(checkout),*args],check=True,stdout=subprocess.DEVNULL)
    # Data population is mandatory; do not turn on empty-repository jobs.
    gh('api','repos/'+a.data+'/contents/seed/manifest.json','--jq','.sha')
    gh('api','repos/'+a.code+'/actions/secrets/public-key','--jq','.key_id')
    folder=Path(tempfile.mkdtemp(prefix='wcpp-deploy-key-'));os.chmod(folder,0o700)
    key=folder/'id_ed25519'
    subprocess.run(['ssh-keygen','-t','ed25519','-N','','-C','WCPP-data-only-automation','-f',str(key)],
                   check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    key_id=None
    try:
        key_id=gh('api','repos/'+a.data+'/keys','-X','POST','-f','title=WCPP data-only automation',
                  '-F','key=@'+str(key)+'.pub','-F','read_only=false','--jq','.id')
        gh('secret','set','WCPP_DATA_DEPLOY_KEY','--repo',a.code,input_bytes=key.read_bytes())
        gh('variable','set','WCPP_UNATTENDED_ENABLED','--repo',a.code,'--body','true')
        gh('workflow','run','unattended.yml','--repo',a.code,'-f','mode=collect')
        print('Installed repo-scoped deploy key and enabled bounded unattended jobs.')
        print('Run the collect workflow once and inspect the private runs/ record before relying on schedules.')
    except Exception:
        if key_id:
            try:gh('api','repos/'+a.data+'/keys/'+key_id,'-X','DELETE')
            except RuntimeError:pass
        try:gh('variable','set','WCPP_UNATTENDED_ENABLED','--repo',a.code,'--body','false')
        except RuntimeError:pass
        raise
    finally:
        for path in (key,Path(str(key)+'.pub')):
            if path.exists():path.unlink()
        folder.rmdir()


if __name__=='__main__':main()
