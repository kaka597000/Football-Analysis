"""Run each suite in a separate interpreter to avoid same-name module collisions."""
import argparse,os,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SUITES=sorted({p.parent.relative_to(ROOT).as_posix()
               for p in (ROOT/'runtime').rglob('test_*.py')})

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--layer', choices=['all','portable','integration'], default='all')
    args=parser.parse_args()
    env=dict(os.environ,OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1',
             PYTHONPATH=os.pathsep.join((str(ROOT/'runtime'), str(ROOT/'runtime/optimization'))))
    if args.layer == 'integration':
        import json,hashlib
        registry=Path(env.get('WCPP_ACTIVE_MODEL', str(Path(env.get('WCPP_RUNTIME_ROOT','/workspace/WCPP'))/'runtime/active-model.json')))
        models=json.loads(registry.read_text())['models']
        for route in models.values():
            for prefix in ('','test_'):
                value=route.get(prefix+'artifact_path')
                if value and hashlib.sha256(Path(value).read_bytes()).hexdigest()!=route[prefix+'artifact_sha256']:
                    raise ValueError('integration artifact mismatch')
    failures=[]
    suites = ['runtime'] if args.layer == 'portable' else SUITES
    for suite in suites:
        print('Testing '+suite,flush=True)
        modules=(['test_refactor_contracts','test_review_contracts'] if args.layer == 'portable' else sorted(p.stem for p in (ROOT/suite).glob('test_*.py')))
        result=subprocess.run([sys.executable,'-m','unittest','-v',*modules],cwd=ROOT/suite,env=env, capture_output=args.layer=='integration', text=True)
        if args.layer=='integration':
            print(result.stdout, end='');print(result.stderr, end='')
            if 'skipped ' in result.stderr or 'skipped=' in result.stderr:
                failures.append(suite+' (integration skip prohibited)')
        if result.returncode:failures.append(suite)
    print('Failed suites: '+(', '.join(failures) if failures else 'none'),flush=True)
    return bool(failures)

if __name__=='__main__':raise SystemExit(main())
