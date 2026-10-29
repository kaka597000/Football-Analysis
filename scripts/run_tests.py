"""Run each suite in a separate interpreter to avoid same-name module collisions."""
import os,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SUITES=sorted({p.parent.relative_to(ROOT).as_posix()
               for p in (ROOT/'runtime').rglob('test_*.py')})

def main():
    env=dict(os.environ,OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1',
             PYTHONPATH=str(ROOT/'runtime'))
    failures=[]
    for suite in SUITES:
        print('Testing '+suite,flush=True)
        modules=sorted(p.stem for p in (ROOT/suite).glob('test_*.py'))
        result=subprocess.run([sys.executable,'-m','unittest','-v',*modules],cwd=ROOT/suite,env=env)
        if result.returncode:failures.append(suite)
    print('Failed suites: '+(', '.join(failures) if failures else 'none'),flush=True)
    return bool(failures)

if __name__=='__main__':raise SystemExit(main())
