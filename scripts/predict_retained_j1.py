"""Use the installed J1 compatibility manifest with the existing publication CLI."""
import os
from pathlib import Path
import runpy

if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    os.environ.setdefault('WCPP_J1_COMPATIBILITY_MANIFEST',
                          '/workspace/WCPP/runtime/retained-j1/manifest.json')
    import sys
    sys.path.insert(0, str(root / 'runtime'))
    runpy.run_path(str(root / 'runtime/predict_active.py'), run_name='__main__')
