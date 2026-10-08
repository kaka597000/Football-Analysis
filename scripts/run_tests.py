"""Run each suite in a separate interpreter to avoid same-name module collisions."""
import os,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SUITES=('runtime','runtime/v2','runtime/v3','runtime/v4','runtime/emperor_cup')

def main():
    env=dict(os.environ,OPENBLAS_NUM_THREADS='1')
    for suite in SUITES:
        print('Testing '+suite,flush=True)
        result=subprocess.run([sys.executable,'-m','unittest','discover','-s','.','-p','test_*.py','-v'],cwd=ROOT/suite,env=env)
        if result.returncode:return result.returncode
    return 0

if __name__=='__main__':raise SystemExit(main())
